from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import DataConfig, GenerationConfig, RenderConfig, Settings, TTSConfig
from .security import redact
from .storage import atomic_json, rebuild_indexes, render_lock

TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
PERSONALIZATION_VERSION = 3
GENERATION_STATUS_FILENAME = "generation-status.json"
LOGGER = logging.getLogger(__name__)


class SchedulerOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_demo_on_start: bool = False
    generate_personal_on_start: bool = True
    allow_external_tts: bool = False
    daily_time: str = "06:15"
    weekly_day: int = Field(default=6, ge=0, le=6)
    weekly_time: str = "06:30"
    auto_discover_sensors: bool = True
    include_binary_sensors: bool = True
    entity_allowlist: list[str] = Field(default_factory=list, max_length=2_000)
    max_discovered_entities: int = Field(default=2_000, ge=1, le=5_000)
    history_hours_daily: int = Field(default=24, ge=1, le=168)
    history_days_weekly: int = Field(default=7, ge=1, le=31)
    history_batch_size: int = Field(default=20, ge=1, le=100)
    max_observations_per_entity: int = Field(default=512, ge=8, le=2048)
    max_response_bytes: int = Field(default=10_000_000, ge=64_000, le=20_000_000)
    max_highlights: int = Field(default=5, ge=3, le=5)
    render_width: int = Field(default=720, ge=240, le=1080)
    render_height: int = Field(default=1280, ge=426, le=1920)
    render_fps: int = Field(default=24, ge=12, le=30)
    render_preset: str = "veryfast"
    video_style: str = "engaging"
    video_energy: str = "balanced"
    motion_level: str = "medium"
    music_enabled: bool = True
    sound_effects_enabled: bool = True
    captions_style: str = "animated"
    video_theme: str = "adaptive"
    motion_sample_fps: int = Field(default=8, ge=4, le=12)
    storyboard_provider: str = Field(default="claude", pattern=r"^(claude|offline)$")
    claude_model: str = Field(default="claude-opus-5", min_length=1, max_length=64)
    claude_config_dir: Path = Path("/homeassistant/.claude-video-runner")
    claude_timeout_seconds: int = Field(default=300, ge=60, le=900)
    story_detail: str = Field(default="personal", pattern=r"^(personal|aggregate)$")
    video_id_namespace: str = Field(
        default="claude", max_length=32, pattern=r"^$|^[a-z0-9]([a-z0-9-]*[a-z0-9])?$"
    )

    @field_validator("claude_config_dir")
    @classmethod
    def validate_claude_config_dir(cls, value: Path) -> Path:
        # The add-on runs on Linux; a leading slash is absolute there even when the
        # validating host is Windows, where Path("/x").is_absolute() is False.
        if not value.is_absolute() and not value.as_posix().startswith("/"):
            raise ValueError("claude_config_dir must be an absolute path")
        return value

    @field_validator("daily_time", "weekly_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        if not TIME_PATTERN.fullmatch(value):
            raise ValueError("schedule times must use 24-hour HH:MM format")
        return value

    @field_validator("entity_allowlist")
    @classmethod
    def validate_entities(cls, values: list[str]) -> list[str]:
        allowed_prefixes = ("sensor.", "binary_sensor.", "input_number.")
        if any(not value.startswith(allowed_prefixes) for value in values):
            raise ValueError("entity allowlist contains a disallowed domain")
        if len(values) != len(set(values)):
            raise ValueError("entity allowlist contains duplicates")
        return values

    @field_validator("render_preset")
    @classmethod
    def validate_preset(cls, value: str) -> str:
        if value not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium"}:
            raise ValueError("unsupported x264 render preset")
        return value


def prepare_addon(
    options_path: Path,
    config_path: Path,
    schedule_path: Path,
) -> SchedulerOptions:
    payload: object = {}
    if options_path.is_file():
        payload = json.loads(options_path.read_text(encoding="utf-8"))
    options = SchedulerOptions.model_validate(payload)
    settings = Settings(
        data=DataConfig(
            auto_discover_sensors=options.auto_discover_sensors,
            include_binary_sensors=options.include_binary_sensors,
            entity_allowlist=options.entity_allowlist,
            max_discovered_entities=options.max_discovered_entities,
            history_hours_daily=options.history_hours_daily,
            history_days_weekly=options.history_days_weekly,
            history_batch_size=options.history_batch_size,
            max_observations_per_entity=options.max_observations_per_entity,
            max_response_bytes=options.max_response_bytes,
            max_highlights=options.max_highlights,
        ),
        generation=GenerationConfig(
            provider=options.storyboard_provider,
            claude_config_dir=options.claude_config_dir,
            claude_home_dir=Settings().private_data_directory / "claude-home",
            model=options.claude_model,
            timeout_seconds=options.claude_timeout_seconds,
            story_detail=options.story_detail,
            video_id_namespace=options.video_id_namespace,
        ),
        tts=TTSConfig(allow_external_egress=options.allow_external_tts),
        render=RenderConfig(
            width=options.render_width,
            height=options.render_height,
            fps=options.render_fps,
            preset=options.render_preset,
            style=options.video_style,
            energy=options.video_energy,
            motion_level=options.motion_level,
            music_enabled=options.music_enabled,
            sound_effects_enabled=options.sound_effects_enabled,
            captions_style=options.captions_style,
            theme=options.video_theme,
            motion_sample_fps=options.motion_sample_fps,
        ),
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(settings.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )
    config_path.chmod(0o600)
    schedule_payload = {
        "run_demo_on_start": options.run_demo_on_start,
        "generate_personal_on_start": options.generate_personal_on_start,
        "daily_time": options.daily_time,
        "weekly_day": options.weekly_day,
        "weekly_time": options.weekly_time,
    }
    atomic_json(schedule_path, schedule_payload)
    schedule_path.chmod(0o600)
    return options


def should_run(now: datetime, scheduled_time: str, previous_key: str) -> tuple[bool, str]:
    key = f"{now.date().isoformat()}T{scheduled_time}"
    return now.strftime("%H:%M") == scheduled_time and previous_key != key, key


def _run_child(config_path: Path, *arguments: str) -> None:
    command = [
        sys.executable,
        "-m",
        "video_runner.cli",
        *arguments,
        "--config",
        str(config_path),
    ]
    completed = subprocess.run(command, env=os.environ.copy(), timeout=3600, check=False)
    if completed.returncode:
        raise RuntimeError(f"video generation command failed with exit code {completed.returncode}")


def generation_status_path(private_root: Path) -> Path:
    return private_root / GENERATION_STATUS_FILENAME


def read_generation_status(private_root: Path) -> dict[str, object]:
    path = generation_status_path(private_root)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _record_outcome(private_root: Path, period: str, *, reason: str) -> None:
    status = read_generation_status(private_root)
    status[period] = {
        "ok": not reason,
        "reason": reason,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    try:
        atomic_json(generation_status_path(private_root), status)
        generation_status_path(private_root).chmod(0o600)
    except OSError:
        LOGGER.warning("could not persist generation status for %s", period)


def _guarded_run(settings: Settings, config_path: Path, period: str) -> bool:
    """Run one generation, recording and swallowing failure so the scheduler survives."""
    try:
        _run_child(config_path, "generate", "--period", period)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        reason = redact(exc)[:400]
        LOGGER.error("%s video generation failed: %s", period, reason)
        _record_outcome(settings.private_data_directory, period, reason=reason)
        return False
    _record_outcome(settings.private_data_directory, period, reason="")
    return True


def _catalog_has_both(video_root: Path) -> bool:
    catalog = video_root / "indexes" / "all.json"
    if not catalog.is_file():
        return False
    try:
        payload = json.loads(catalog.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return isinstance(payload, list) and {
        item.get("type") for item in payload if isinstance(item, dict)
    } >= {
        "daily",
        "weekly",
    }


def _run_startup_generation(
    settings: Settings, schedule: dict[str, object], config_path: Path
) -> None:
    marker_path = settings.private_data_directory / "personalization-version.json"
    marker_version = 0
    if marker_path.is_file():
        try:
            marker_payload = json.loads(marker_path.read_text(encoding="utf-8"))
            marker_version = int(marker_payload.get("version", 0))
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError, OSError):
            marker_version = 0
    if bool(schedule.get("generate_personal_on_start", True)) and (
        marker_version < PERSONALIZATION_VERSION
    ):
        # Each period is independent: a failed daily must not skip the weekly, and a
        # failed boot must be retried on the next start rather than marked done.
        daily_ok = _guarded_run(settings, config_path, "daily")
        weekly_ok = _guarded_run(settings, config_path, "weekly")
        if daily_ok and weekly_ok:
            atomic_json(marker_path, {"version": PERSONALIZATION_VERSION})
            marker_path.chmod(0o600)
    elif bool(schedule.get("run_demo_on_start", False)) and not _catalog_has_both(
        settings.video_directory
    ):
        try:
            _run_child(config_path, "generate-demo")
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            LOGGER.error("demo generation failed: %s", redact(exc)[:400])


def run_scheduler(config_path: Path, schedule_path: Path, *, poll_seconds: int = 15) -> None:
    settings = Settings.model_validate(yaml.safe_load(config_path.read_text(encoding="utf-8")))
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    state_path = settings.private_data_directory / "scheduler-state.json"
    state: dict[str, str] = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}
    try:
        with render_lock(settings.video_directory):
            rebuild_indexes(settings.video_directory)
    except RuntimeError as exc:
        # A render killed mid-encode — an OOM kill is the usual cause on small
        # hosts — leaves its lock behind until the staleness window expires. The
        # index rebuild is a convenience, so refusing to schedule anything for
        # the rest of that window turns one dead render into hours of downtime.
        LOGGER.warning("skipping startup index rebuild: %s", redact(exc)[:200])
    _run_startup_generation(settings, schedule, config_path)
    while True:
        now = datetime.now().astimezone()
        daily_due, daily_key = should_run(now, str(schedule["daily_time"]), state.get("daily", ""))
        if daily_due:
            _guarded_run(settings, config_path, "daily")
            # Recorded whether or not it succeeded, so a failure is not retried every poll.
            state["daily"] = daily_key
            atomic_json(state_path, state)
        weekly_due, weekly_key = should_run(
            now, str(schedule["weekly_time"]), state.get("weekly", "")
        )
        if weekly_due and now.weekday() == int(schedule["weekly_day"]):
            _guarded_run(settings, config_path, "weekly")
            state["weekly"] = weekly_key
            atomic_json(state_path, state)
        time.sleep(poll_seconds)
