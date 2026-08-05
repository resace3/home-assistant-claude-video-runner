from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from video_runner.claude_provider import (
    ClaudeProviderError,
    build_request,
    child_environment,
    generate_with_claude,
)
from video_runner.config import GenerationConfig
from video_runner.model_policy import generate_storyboard
from video_runner.schemas import PeriodType, Storyboard

NARRATION = " ".join(["word"] * 150)


def _storyboard_payload(period: str = "daily", narration: str = NARRATION) -> dict[str, object]:
    return {
        "title": "Your Daily Reflection",
        "period_type": period,
        "summary": "A warm private reflection.",
        "narration": narration,
        "scenes": [
            {
                "scene_id": "hook",
                "start_seconds": 0,
                "duration_seconds": 20,
                "scene_type": "title",
                "heading": "Steps climbed today",
                "body": "8,123 steps, well above your usual range.",
                "visual": {
                    "kind": "hook",
                    "data_reference": "steps",
                    "payload": {"category": "movement", "series": [1.0, 2.0]},
                },
            },
            {
                "scene_id": "middle",
                "start_seconds": 20,
                "duration_seconds": 20,
                "scene_type": "metric",
                "heading": "Rest held steady",
                "body": "Sleep stayed close to your baseline.",
                "visual": {"kind": "metric_grid", "data_reference": "sleep", "payload": {}},
            },
            {
                "scene_id": "close",
                "start_seconds": 40,
                "duration_seconds": 20,
                "scene_type": "closing",
                "heading": "One kind next step",
                "body": "Protect the wind-down that worked.",
                "visual": {"kind": "closing", "data_reference": "closing", "payload": {}},
            },
        ],
        "safety_notes": ["Descriptive only", "Not medical advice"],
        "data_categories_used": ["movement", "sleep"],
    }


def _envelope(result_text: str, *, is_error: bool = False) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": is_error,
            "result": result_text,
            "session_id": "abc",
            "total_cost_usd": 0,
            "usage": {"input_tokens": 1200, "output_tokens": 900},
        }
    )


def _config(tmp_path: Path, **overrides: object) -> GenerationConfig:
    base: dict[str, object] = {
        "provider": "claude",
        "claude_config_dir": tmp_path / "cfg",
        "claude_home_dir": tmp_path / "home",
    }
    return GenerationConfig(**(base | overrides))  # type: ignore[arg-type]


def _summary() -> dict[str, object]:
    return {
        "period": "daily",
        "discovered_sensor_count": 93,
        "usable_sensor_count": 40,
        "history_sensor_count": 30,
        "highlights": [
            {
                "label": "Steps",
                "current": "8,123 steps",
                "detail": "Rose across the period",
                "trend": "trending up",
                "numeric_value": 8123.0,
                "baseline": 6400.0,
                "delta_percent": 22.0,
                "unit": "steps",
                "chart_values": [2200.0, 8123.0],
            }
        ],
    }


class _Recorder:
    def __init__(self, *stdouts: str, returncode: int = 0) -> None:
        self.stdouts = list(stdouts)
        self.returncode = returncode
        self.calls: list[dict[str, Any]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        # The scratch cwd is a context-managed temp dir, so observe it during the call.
        workspace = Path(str(kwargs.get("cwd", ".")))
        self.calls.append(
            {
                "argv": argv,
                "cwd_is_dir": workspace.is_dir(),
                "cwd_is_empty": workspace.is_dir() and not any(workspace.iterdir()),
                **kwargs,
            }
        )
        stdout = self.stdouts.pop(0) if self.stdouts else ""
        return subprocess.CompletedProcess(argv, self.returncode, stdout, "")


def test_claude_provider_returns_validated_storyboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder(_envelope(json.dumps(_storyboard_payload())))
    monkeypatch.setattr("video_runner.claude_provider.subprocess.run", recorder)
    result = generate_with_claude(PeriodType.DAILY, _summary(), _config(tmp_path))
    assert isinstance(result.storyboard, Storyboard)
    assert result.model == "claude-opus-5"
    assert (result.input_tokens, result.output_tokens) == (1200, 900)
    assert result.estimated_cost_usd == 0.0
    assert result.fallback_reason == ""
    assert len(recorder.calls) == 1


def test_argv_is_non_interactive_tool_free_and_uses_a_scratch_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder(_envelope(json.dumps(_storyboard_payload())))
    monkeypatch.setattr("video_runner.claude_provider.subprocess.run", recorder)
    generate_with_claude(PeriodType.DAILY, _summary(), _config(tmp_path))
    call = recorder.calls[0]
    argv = call["argv"]
    assert argv[0] == "claude"
    assert argv[1] == "-p"
    for flag, value in (
        ("--output-format", "json"),
        ("--model", "claude-opus-5"),
        ("--tools", ""),
        ("--permission-mode", "dontAsk"),
        ("--max-turns", "1"),
    ):
        assert argv[argv.index(flag) + 1] == value
    assert "--strict-mcp-config" in argv
    assert "--disable-slash-commands" in argv
    assert "--no-session-persistence" in argv
    assert "--dangerously-skip-permissions" not in argv
    assert "--bare" not in argv
    assert call.get("shell", False) is False
    assert call["check"] is False
    assert call["timeout"] == 300
    assert call["stdin"] == subprocess.DEVNULL
    assert call["cwd_is_dir"] is True
    assert call["cwd_is_empty"] is True
    assert Path(call["cwd"]) != Path.cwd()


def test_supervisor_token_and_api_keys_never_reach_the_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "canary-supervisor")
    monkeypatch.setenv("HASSIO_TOKEN", "canary-hassio")
    monkeypatch.setenv("HOMEASSISTANT_API", "canary-api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "canary-key")
    monkeypatch.setenv("PATH", "/usr/bin")
    recorder = _Recorder(_envelope(json.dumps(_storyboard_payload())))
    monkeypatch.setattr("video_runner.claude_provider.subprocess.run", recorder)
    config = _config(tmp_path)
    generate_with_claude(PeriodType.DAILY, _summary(), config)
    environment = recorder.calls[0]["env"]
    assert "SUPERVISOR_TOKEN" not in environment
    assert "HASSIO_TOKEN" not in environment
    assert "HOMEASSISTANT_API" not in environment
    assert "ANTHROPIC_API_KEY" not in environment
    assert "canary-supervisor" not in json.dumps(environment)
    assert environment["CLAUDE_CONFIG_DIR"] == str(config.claude_config_dir)
    assert environment["HOME"] == str(config.claude_home_dir)
    assert environment["PATH"] == "/usr/bin"
    # The helper is the only way the child environment is built.
    assert "SUPERVISOR_TOKEN" not in child_environment(config, tmp_path / "home")


def test_fenced_and_prose_wrapped_json_is_recovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fenced = f"Here you go:\n```json\n{json.dumps(_storyboard_payload())}\n```\nHope that helps."
    recorder = _Recorder(_envelope(fenced))
    monkeypatch.setattr("video_runner.claude_provider.subprocess.run", recorder)
    result = generate_with_claude(PeriodType.DAILY, _summary(), _config(tmp_path))
    assert result.storyboard.title == "Your Daily Reflection"


def test_malformed_output_retries_once_with_the_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = json.dumps(_storyboard_payload() | {"narration": "too short"})
    recorder = _Recorder(_envelope(broken), _envelope(json.dumps(_storyboard_payload())))
    monkeypatch.setattr("video_runner.claude_provider.subprocess.run", recorder)
    result = generate_with_claude(PeriodType.DAILY, _summary(), _config(tmp_path))
    assert len(recorder.calls) == 2
    assert "retried after" in result.fallback_reason
    retry_prompt = recorder.calls[1]["argv"][2]
    assert "YOUR PREVIOUS REPLY WAS REJECTED" in retry_prompt
    assert "narration" in retry_prompt


def test_provider_gives_up_after_a_second_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder(_envelope("not json at all"), _envelope("still not json"))
    monkeypatch.setattr("video_runner.claude_provider.subprocess.run", recorder)
    with pytest.raises(ClaudeProviderError):
        generate_with_claude(PeriodType.DAILY, _summary(), _config(tmp_path))
    assert len(recorder.calls) == 2


def test_narration_may_not_copy_exact_readings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leaky = NARRATION.replace("word", "8,123", 1)
    recorder = _Recorder(
        _envelope(json.dumps(_storyboard_payload(narration=leaky))),
        _envelope(json.dumps(_storyboard_payload())),
    )
    monkeypatch.setattr("video_runner.claude_provider.subprocess.run", recorder)
    result = generate_with_claude(PeriodType.DAILY, _summary(), _config(tmp_path))
    assert "8,123" not in result.storyboard.narration
    assert "qualitative" in result.fallback_reason


@pytest.mark.parametrize(
    ("stdouts", "returncode", "exception"),
    [
        ([_envelope("Not logged in / Please run /login", is_error=True)], 1, None),
        ([""], 1, None),
        ([], 0, FileNotFoundError("claude")),
        ([], 0, subprocess.TimeoutExpired("claude", 300)),
        ([], 0, OSError("permission denied")),
    ],
)
def test_every_cli_failure_falls_back_to_the_offline_story(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdouts: list[str],
    returncode: int,
    exception: Exception | None,
) -> None:
    if exception is None:
        runner: Any = _Recorder(*stdouts, returncode=returncode)
    else:

        def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            raise exception

    monkeypatch.setattr("video_runner.claude_provider.subprocess.run", runner)
    result = generate_storyboard(PeriodType.DAILY, _summary(), _config(tmp_path))
    assert result.model == "offline-personal"
    assert result.fallback_reason
    assert result.estimated_cost_usd == 0.0
    assert len(result.storyboard.narration.split()) == 150


def test_failures_propagate_when_offline_fallback_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "video_runner.claude_provider.subprocess.run", _Recorder(_envelope("nope"), _envelope("no"))
    )
    config = _config(tmp_path, offline_fallback=False)
    with pytest.raises(ClaudeProviderError):
        generate_storyboard(PeriodType.DAILY, _summary(), config)


def test_offline_provider_never_shells_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the offline provider must not start a subprocess")

    monkeypatch.setattr("video_runner.claude_provider.subprocess.run", forbidden)
    result = generate_storyboard(
        PeriodType.DAILY, _summary(), _config(tmp_path, provider="offline")
    )
    assert result.model == "offline-personal"
    assert result.fallback_reason == ""


def test_unknown_provider_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _config(tmp_path, provider="openai")


def test_prompt_states_the_schema_contract_and_carries_the_chosen_payload(
    tmp_path: Path,
) -> None:
    personal = build_request(PeriodType.DAILY, _summary(), _config(tmp_path))
    assert "145 and 160 words" in personal.system_prompt
    assert "55 and 65" in personal.system_prompt
    assert "3 to 10 scenes" in personal.system_prompt
    for kind in ("hook", "metric_grid", "seven_day", "data_quality"):
        assert kind in personal.system_prompt
    for scene_type in ("title", "metric", "timeline", "reflection", "recommendation", "closing"):
        assert scene_type in personal.system_prompt
    assert "Descriptive, never diagnostic" in personal.system_prompt
    assert "safety_notes" in personal.system_prompt
    assert "JSON object only" in personal.system_prompt
    assert "8,123 steps" in personal.prompt
    assert '"daily"' in personal.prompt

    aggregate = build_request(
        PeriodType.WEEKLY, _summary(), _config(tmp_path, story_detail="aggregate")
    )
    assert "8,123" not in aggregate.prompt
    assert "highlight_count" in aggregate.prompt
    assert "weekly" in aggregate.prompt
