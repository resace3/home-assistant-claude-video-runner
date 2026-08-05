from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .config import GenerationConfig
from .personalization import disclosure_payload
from .schemas import PeriodType, Storyboard
from .security import redact

# Removed from the child environment: the Supervisor credential and anything that would
# let the CLI reach Home Assistant, plus API keys that would bill a metered account
# instead of using the user's Claude Code subscription login.
BLOCKED_ENV_NAMES = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "AWS_BEARER_TOKEN_BEDROCK",
        "OPENAI_API_KEY",
        "SUPERVISOR_TOKEN",
    }
)
BLOCKED_ENV_PREFIXES = (
    "ADDON_",
    "HASSIO",
    "HA_",
    "HOMEASSISTANT",
    "HOME_ASSISTANT",
    "SUPERVISOR",
)

VISUAL_KINDS = (
    "hook",
    "metric_grid",
    "sparkline",
    "seven_day",
    "comparison",
    "progress_ring",
    "recommendation",
    "closing",
    "data_quality",
    "gradient",
    "chart",
    "icon_grid",
    "timeline",
    "photo_placeholder",
)
SCENE_TYPES = ("title", "metric", "timeline", "reflection", "recommendation", "closing")
ACCENT_CATEGORIES = ("sleep", "movement", "recovery", "focus", "environment", "routine")

SYSTEM_PROMPT = f"""\
You write one-minute personal reflection videos from a person's own Home Assistant data.
You are not a coding assistant here. You do not use tools, read files or run commands.
You reply with exactly one JSON object and nothing else.

OUTPUT CONTRACT
Return a single JSON object matching this schema. Unknown keys are rejected.

{{
  "title": string, 1-80 chars,
  "period_type": "daily" | "weekly",
  "summary": string, <=400 chars,
  "narration": string, <=1800 chars,
  "scenes": [ Scene, ... ],            // 3-10 items
  "safety_notes": [string, ...],       // <=10 items, must be non-empty
  "data_categories_used": [string, ...] // <=20 items
}}

Scene = {{
  "scene_id": string matching ^[a-z0-9-]{{2,50}}$, unique per scene,
  "start_seconds": number 0-65,
  "duration_seconds": number >0 and <=65,
  "scene_type": one of {list(SCENE_TYPES)},
  "heading": string, 1-80 chars,
  "body": string, <=240 chars,
  "visual": {{
    "kind": one of {list(VISUAL_KINDS)},
    "data_reference": string <=80 chars,
    "payload": object
  }},
  "layout": string <=40 chars,
  "accent": one of {list(ACCENT_CATEGORIES)},
  "caption": string <=120 chars,
  "audio_cue": one of ["none", "soft", "insight", "complete"],
  "accessibility_description": string <=240 chars
}}

HARD CONSTRAINTS - a storyboard violating any of these is rejected outright
1. 3 to 10 scenes.
2. Scenes are ordered and non-overlapping: scene[i].start_seconds must equal the previous
   scene's start_seconds + duration_seconds. The first scene starts at 0.
3. The final start_seconds + duration_seconds must land between 55 and 65. Aim for exactly 60.
4. narration must contain between 145 and 160 words. Count them. This is the single most
   common failure - write the narration, count the words, then adjust before replying.
5. safety_notes must be populated.

VISUAL PAYLOAD SHAPES - the renderer reads these keys, so match them or the scene renders empty
- hook: {{"category", "current", "comparison", "series": [numbers], "badge"}}
- metric_grid: {{"category", "cards": [{{"label", "current", "comparison"}}]}}  (max 3 cards)
- sparkline / chart / timeline: {{"category", "current", "comparison", "numeric_value",
  "baseline", "unit", "series": [numbers]}}
- seven_day: {{"category", "comparison", "series": [7 numbers]}}
- progress_ring: {{"category", "current", "comparison"}}
- comparison: {{"category", "labels": [two strings], "values": [two numbers]}}
- recommendation / closing / data_quality: {{"category"}}
Only use numbers that appear in the supplied data. Never invent a reading or a series.

VOICE
Warm, specific and human - a friend who paid attention, not a dashboard reading itself out.
Use the person's real signals across different areas of life (rest, movement, recovery,
focus, home rhythm) rather than reciting several numbers from one area. Notice what
connects. Prefer one concrete observation over three vague ones.

SAFETY
Descriptive, never diagnostic. No medical advice, no diagnosis, no alarm. Changes are
observations, not verdicts. Two signals moving together is not causation. Populate
safety_notes with the posture you actually followed.

NARRATION PRIVACY
On-screen headings, bodies, captions and payloads may carry exact values - those stay on
the device. The narration may be sent to an external text-to-speech service, so keep it
qualitative: say "your steps climbed steadily through the afternoon", never "8,123 steps".
No digits copied from the data into narration.

Reply with the JSON object only. No prose, no explanation, no markdown fence.
"""

_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_DIGIT_RUN = re.compile(r"\d[\d,.]*")


class ClaudeProviderError(RuntimeError):
    """The claude CLI was unavailable, failed, or produced an unusable storyboard."""


@dataclass(frozen=True)
class ClaudeResult:
    storyboard: Storyboard
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    fallback_reason: str = ""


@dataclass(frozen=True)
class ClaudeRequest:
    model: str
    system_prompt: str
    prompt: str
    argv: list[str] = field(default_factory=list)


def build_user_prompt(
    period: PeriodType, summary: Mapping[str, object], *, story_detail: str
) -> str:
    payload = disclosure_payload(summary, story_detail=story_detail)
    period_word = "day" if period == PeriodType.DAILY else "week"
    span = "the last 24 hours" if period == PeriodType.DAILY else "the last seven days"
    if story_detail == "aggregate":
        guidance = (
            "Only counts were disclosed, so write an honest, warm reflection about the "
            "shape of the period without claiming to know any specific reading. Say what "
            "cannot be seen rather than inventing detail."
        )
    else:
        guidance = (
            "Each highlight carries a friendly label, a formatted current value, a plain "
            "detail sentence, a trend, a percentage delta against the person's own recent "
            "baseline, and a chart_values series. Build the story from the signals that "
            "actually moved, spread across different areas of life, and put the real "
            "values on screen in headings, bodies and visual payloads."
        )
    return (
        f"Write the {period.value} reflection video for {span}.\n\n"
        f'Set period_type to "{period.value}".\n\n'
        f"{guidance}\n\n"
        "Here is the person's own data for this period:\n\n"
        f"{json.dumps(payload, indent=2, sort_keys=True, default=str)}\n\n"
        f"Open on whatever genuinely stood out this {period_word}, carry it through the "
        "middle scenes, and close on one small realistic thing worth trying next. Total "
        "run time 60 seconds. Narration 145-160 words, qualitative, no digits.\n"
        "Reply with the storyboard JSON object only."
    )


def build_request(
    period: PeriodType, summary: Mapping[str, object], config: GenerationConfig
) -> ClaudeRequest:
    prompt = build_user_prompt(period, summary, story_detail=config.story_detail)
    return ClaudeRequest(
        model=config.model,
        system_prompt=SYSTEM_PROMPT,
        prompt=prompt,
        argv=_argv(config, prompt),
    )


def _argv(config: GenerationConfig, prompt: str) -> list[str]:
    return [
        config.claude_binary,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        config.model,
        "--system-prompt",
        SYSTEM_PROMPT,
        # Pure text to JSON: no built-in tools, no MCP servers, no on-disk settings,
        # no skill expansion, one turn, and no session transcript written to disk.
        "--tools",
        "",
        "--permission-mode",
        "dontAsk",
        "--max-turns",
        "1",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--setting-sources",
        "",
        "--no-session-persistence",
    ]


def child_environment(config: GenerationConfig, home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    for name in list(environment):
        upper = name.upper()
        if upper in BLOCKED_ENV_NAMES or upper.startswith(BLOCKED_ENV_PREFIXES):
            del environment[name]
    environment["CLAUDE_CONFIG_DIR"] = str(config.claude_config_dir)
    environment["HOME"] = str(home)
    return environment


def _resolve_home(config: GenerationConfig, workspace: Path) -> Path:
    try:
        config.claude_home_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        fallback = workspace / "home"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
    return config.claude_home_dir


def _short(value: object, limit: int = 200) -> str:
    return redact(value).replace("\n", " ")[:limit]


def _envelope_result(stdout: str) -> tuple[str, int, int]:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeProviderError("claude CLI did not return a JSON envelope") from exc
    if not isinstance(envelope, dict):
        raise ClaudeProviderError("claude CLI envelope was not a JSON object")
    result = envelope.get("result")
    if envelope.get("is_error"):
        raise ClaudeProviderError(f"claude CLI reported an error: {_short(result)}")
    if not isinstance(result, str) or not result.strip():
        raise ClaudeProviderError("claude CLI envelope carried no result text")
    usage = envelope.get("usage")
    usage_map: Mapping[str, object] = usage if isinstance(usage, Mapping) else {}

    def tokens(key: str) -> int:
        value = usage_map.get(key, 0)
        return value if isinstance(value, int) and value >= 0 else 0

    return result, tokens("input_tokens"), tokens("output_tokens")


def _extract_json_object(text: str) -> dict[str, object]:
    candidates = [match.group(1) for match in _FENCE_PATTERN.finditer(text)]
    candidates.append(text)
    for candidate in candidates:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            continue
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ClaudeProviderError("no JSON object was found in the claude CLI result text")


def _payload_values(summary: Mapping[str, object], story_detail: str) -> set[str]:
    if story_detail != "personal":
        return set()
    highlights = summary.get("highlights")
    if not isinstance(highlights, list):
        return set()
    values: set[str] = set()
    for item in highlights:
        if not isinstance(item, Mapping):
            continue
        for key in ("current", "numeric_value", "baseline"):
            for match in _DIGIT_RUN.finditer(str(item.get(key, ""))):
                token = match.group(0).rstrip(".,")
                if len(token.replace(",", "").replace(".", "")) >= 2:
                    values.add(token)
    return values


def _assert_narration_is_qualitative(
    storyboard: Storyboard, summary: Mapping[str, object], story_detail: str
) -> None:
    leaked = sorted(
        value for value in _payload_values(summary, story_detail) if value in storyboard.narration
    )
    if leaked:
        raise ClaudeProviderError(
            "narration copied exact readings from the data; narration can reach an external "
            f"speech service and must stay qualitative (found {len(leaked)} value(s))"
        )


def _run_cli(
    argv: list[str], config: GenerationConfig, workspace: Path
) -> subprocess.CompletedProcess[str]:
    home = _resolve_home(config, workspace)
    try:
        return subprocess.run(  # noqa: S603 - list argv, no shell, scrubbed env, fixed flags
            argv,
            capture_output=True,
            # The CLI reads stdin when it is open; without this it stalls 3s per call
            # and would block outright on an inherited pipe that never closes.
            stdin=subprocess.DEVNULL,
            text=True,
            # Pinned, not locale-derived. Narration routinely carries curly quotes
            # and em dashes, so a container that resolves to ASCII would raise
            # UnicodeDecodeError here and fail every generation.
            encoding="utf-8",
            errors="replace",
            timeout=config.timeout_seconds,
            check=False,
            env=child_environment(config, home),
            cwd=workspace,
        )
    except FileNotFoundError as exc:
        raise ClaudeProviderError(f"claude binary {config.claude_binary!r} was not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClaudeProviderError(
            f"claude CLI exceeded the {config.timeout_seconds}s timeout"
        ) from exc
    except OSError as exc:
        raise ClaudeProviderError(f"claude CLI could not be started: {_short(exc)}") from exc


def _attempt(
    argv: list[str], config: GenerationConfig, summary: Mapping[str, object], workspace: Path
) -> tuple[Storyboard, int, int]:
    completed = _run_cli(argv, config, workspace)
    if not completed.stdout.strip():
        detail = _short(completed.stderr) or f"exit code {completed.returncode}"
        raise ClaudeProviderError(f"claude CLI produced no output ({detail})")
    text, input_tokens, output_tokens = _envelope_result(completed.stdout)
    try:
        storyboard = Storyboard.model_validate(_extract_json_object(text))
    except ValidationError as exc:
        raise ClaudeProviderError(f"storyboard failed validation: {_short(exc, 700)}") from exc
    _assert_narration_is_qualitative(storyboard, summary, config.story_detail)
    return storyboard, input_tokens, output_tokens


def _repair_prompt(prompt: str, error: str) -> str:
    return (
        f"{prompt}\n\n"
        "YOUR PREVIOUS REPLY WAS REJECTED. Fix exactly this and reply again with the JSON "
        "object only:\n"
        f"{error}\n\n"
        "Re-check every hard constraint before replying: scene start times chain exactly, "
        "the timeline totals 55-65 seconds, the narration word count is between 145 and "
        "160, no key outside the schema, and no digits in the narration."
    )


def generate_with_claude(
    period: PeriodType, summary: Mapping[str, object], config: GenerationConfig
) -> ClaudeResult:
    """Drive the Claude Code CLI in headless mode using the user's subscription login."""
    prompt = build_user_prompt(period, summary, story_detail=config.story_detail)
    input_tokens = 0
    output_tokens = 0
    first_error = ""
    # Cleanup must never decide the outcome. This context manager wraps the
    # successful return, so a raising __exit__ would discard a valid storyboard
    # and demote the run to the offline template with a misleading reason.
    with tempfile.TemporaryDirectory(
        prefix="video-runner-claude-", ignore_cleanup_errors=True
    ) as raw_workspace:
        workspace = Path(raw_workspace)
        for attempt in range(2):
            active = prompt if attempt == 0 else _repair_prompt(prompt, first_error)
            try:
                storyboard, used_input, used_output = _attempt(
                    _argv(config, active), config, summary, workspace
                )
            except ClaudeProviderError as exc:
                if attempt:
                    raise
                first_error = str(exc)
                continue
            input_tokens += used_input
            output_tokens += used_output
            return ClaudeResult(
                storyboard=storyboard,
                model=config.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                # A Claude Code subscription is not billed per call.
                estimated_cost_usd=0.0,
                fallback_reason=f"retried after: {first_error}" if first_error else "",
            )
    raise ClaudeProviderError(first_error or "claude CLI produced no usable storyboard")
