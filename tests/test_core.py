from __future__ import annotations

import asyncio
import json
import re
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from video_runner import __version__
from video_runner.cli import app
from video_runner.config import GenerationConfig, Settings, TTSConfig, load_settings
from video_runner.home_assistant import HomeAssistantClient, SensorSnapshot
from video_runner.model_policy import offline_storyboard, personalized_storyboard
from video_runner.personalization import (
    build_personal_summary,
    disclosure_payload,
    external_disclosure_summary,
)
from video_runner.scheduler import (
    SchedulerOptions,
    _run_startup_generation,
    prepare_addon,
    read_generation_status,
    should_run,
)
from video_runner.schemas import (
    BrowserVideo,
    PeriodType,
    Scene,
    Storyboard,
    Visual,
    estimated_narration_seconds,
)
from video_runner.security import (
    aggregate_history,
    redact,
    scrub_supervisor_environment,
    validate_runtime_roots,
)
from video_runner.storage import atomic_json, rebuild_indexes, render_lock
from video_runner.tts import resolve_voice, synthesize_edge


def test_release_versions_are_consistent() -> None:
    root = Path(__file__).parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    manifests = [
        path
        for path in root.glob("*/config.yaml")
        if isinstance(yaml.safe_load(path.read_text(encoding="utf-8")), dict)
        and "slug" in yaml.safe_load(path.read_text(encoding="utf-8"))
    ]
    assert len(manifests) == 1
    addon = yaml.safe_load(manifests[0].read_text(encoding="utf-8"))
    assert project["project"]["version"] == addon["version"] == __version__


def test_doctor_fails_when_required_media_tools_are_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        f"video_directory: {(tmp_path / 'share').as_posix()}\n"
        f"private_data_directory: {(tmp_path / 'private').as_posix()}\n",
        encoding="utf-8",
    )
    (tmp_path / "share").mkdir()
    monkeypatch.setattr("video_runner.cli.shutil.which", lambda _name: None)
    result = CliRunner().invoke(
        app,
        ["doctor", "--config", str(config), "--no-require-supervisor-token"],
        env={"VIDEO_RUNNER_TEST_MODE": "1"},
    )
    assert result.exit_code == 1
    assert '"ready": false' in result.output


def test_redaction_masks_tokens_and_headers() -> None:
    secret = "A" * 48
    output = redact(f"Authorization: Bearer {secret} api_key={secret}", (secret,))
    assert secret not in output
    assert "[REDACTED]" in output


def test_aggregate_history_removes_identifiers_and_individual_readings() -> None:
    values = ["10", "10", "11", "11", "13", "14", "14", "15"]
    output = aggregate_history({"sensor.private_name": values, "sensor.location": ["home"]})
    encoded = json.dumps(output)
    assert "private_name" not in encoded
    assert "location" not in encoded
    assert output["metrics"] == {
        "metric_1": {
            "trend": "increasing",
            "variability": "high",
            "observations": "8-15",
        }
    }
    assert not {"minimum", "maximum", "median", "change", "latest"} & set(encoded.split('"'))


def test_aggregate_history_resists_exact_value_reconstruction() -> None:
    low_scale = aggregate_history({"sensor.one": [10, 10, 11, 11, 13, 14, 14, 15]})
    high_scale = aggregate_history({"sensor.one": [100, 100, 110, 110, 130, 140, 140, 150]})
    assert low_scale == high_scale
    assert aggregate_history({"sensor.one": [10, 20, 30]})["metrics"] == {}


def test_supervisor_token_is_scrubbed_before_provider_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "canary-value")
    scrub_supervisor_environment()
    assert "SUPERVISOR_TOKEN" not in __import__("os").environ
    import logging

    retained = [
        value
        for handler in logging.getLogger().handlers
        for filter_ in handler.filters
        for value in getattr(filter_, "sensitive_values", ())
    ]
    assert "canary-value" not in retained


def test_production_roots_are_fixed(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        validate_runtime_roots(tmp_path, Path("/data/personal_video_studio"), test_mode=False)
    validate_runtime_roots(tmp_path, tmp_path / "private", test_mode=True)


def test_offline_storyboard_is_one_minute_and_natural_word_count() -> None:
    board = offline_storyboard(PeriodType.DAILY)
    assert sum(scene.duration_seconds for scene in board.scenes) == 60
    assert 145 <= len(board.narration.split()) <= 160
    assert estimated_narration_seconds(board.narration) == pytest.approx(60.8)


def _personal_summary() -> dict[str, object]:
    snapshots = {
        "sensor.private_steps": SensorSnapshot(
            "sensor.private_steps", "Sample Steps", "8123", "steps", "distance"
        ),
        "sensor.bedroom_temperature": SensorSnapshot(
            "sensor.bedroom_temperature", "Bedroom Temperature", "72.4", "°F", "temperature"
        ),
        "binary_sensor.front_door": SensorSnapshot(
            "binary_sensor.front_door", "Front Door", "off", "", "door"
        ),
    }
    histories: dict[str, list[object]] = {
        "sensor.private_steps": [1000, 2100, 3400, 4800, 5900, 6800, 7500, 8123],
        "sensor.bedroom_temperature": [70.1, 70.8, 71.4, 71.8, 72.0, 72.4],
        "binary_sensor.front_door": ["off", "on", "off"],
    }
    return build_personal_summary(snapshots, histories, period="daily", max_highlights=5)


def test_personal_summary_contains_real_local_facts_but_external_preview_does_not() -> None:
    summary = _personal_summary()
    encoded = json.dumps(summary)
    assert '"label": "Steps"' in encoded
    assert "8,123 steps" in encoded
    assert summary["discovered_sensor_count"] == 3
    external = json.dumps(external_disclosure_summary(summary))
    assert "Tabby" not in external
    assert "8,123" not in external
    assert "entity_id" not in external


def test_personal_storyboard_is_real_on_screen_and_generic_in_external_tts() -> None:
    board = personalized_storyboard(PeriodType.DAILY, _personal_summary())
    visual_text = json.dumps(board.model_dump(mode="json"))
    assert "Steps" in visual_text
    assert "8,123 steps" in visual_text
    assert "Tabby" not in board.narration
    assert "8,123" not in board.narration
    assert len(board.narration.split()) == 150
    assert board.narration.count(".") <= 3
    assert sum(scene.duration_seconds for scene in board.scenes) == 60


def test_storyboard_rejects_overlap() -> None:
    scene = Scene(
        start_seconds=0,
        duration_seconds=30,
        scene_type="title",
        heading="A",
        body="B",
        visual=Visual(kind="gradient", data_reference="x"),
    )
    with pytest.raises(ValidationError):
        Storyboard(
            title="x",
            period_type="daily",
            summary="x",
            narration="word " * 150,
            scenes=[scene, scene, scene],
        )


def test_story_detail_switches_between_counts_and_real_signals() -> None:
    summary = _personal_summary()
    aggregate = disclosure_payload(summary, story_detail="aggregate")
    personal = disclosure_payload(summary, story_detail="personal")
    assert aggregate == external_disclosure_summary(summary)
    assert "highlights" not in aggregate
    assert aggregate["highlight_count"] == 3
    highlights = personal["highlights"]
    assert isinstance(highlights, list) and len(highlights) == 3
    encoded = json.dumps(personal)
    assert '"label": "Steps"' in encoded
    assert "8,123 steps" in encoded
    assert "chart_values" in encoded
    with pytest.raises(ValueError):
        disclosure_payload(summary, story_detail="everything")


def test_personal_payload_strips_identifiers_timestamps_and_coordinates() -> None:
    summary = {
        "period": "daily",
        "discovered_sensor_count": 1,
        "highlights": [
            {
                "label": "Phone",
                "current": "sensor.phone_screen_time at 2026-08-05T06:15:00+00:00",
                "detail": "seen near 51.50735, -0.12776 at 07:42",
                "story_score": 91.5,
                "entity_id": "sensor.phone_screen_time",
                "latitude": 51.50735,
                "last_updated": "2026-08-05T06:15:00+00:00",
                "chart_values": [1.0, 2.0],
            }
        ],
    }
    payload = disclosure_payload(summary, story_detail="personal")
    encoded = json.dumps(payload)
    assert "sensor." not in encoded
    assert "entity_id" not in encoded
    assert "story_score" not in encoded
    assert "latitude" not in encoded
    assert "last_updated" not in encoded
    assert "2026-08-05" not in encoded
    assert "51.50735" not in encoded
    assert "07:42" not in encoded
    highlights = payload["highlights"]
    assert isinstance(highlights, list)
    assert highlights[0]["chart_values"] == [1.0, 2.0]


def _manifest(root: Path, period: str, name: str) -> BrowserVideo:
    now = datetime.now(UTC)
    folder = root / period / "2026"
    if period == "daily":
        folder /= "07"
    folder.mkdir(parents=True)
    for suffix in ("mp4", "webp", "vtt"):
        (folder / f"{name}.{suffix}").write_bytes(b"safe")
    video = BrowserVideo(
        id=name,
        type=period,
        title="Synthetic",
        description="Synthetic fixture",
        created_at=now,
        period_start=now - timedelta(days=1),
        period_end=now,
        duration_seconds=60,
        video_filename=f"{name}.mp4",
        thumbnail_filename=f"{name}.webp",
        captions_filename=f"{name}.vtt",
        generation_status="complete",
    )
    atomic_json(folder / f"{name}.json", video.model_dump(mode="json"))
    return video


def test_indexes_ignore_incomplete_and_corrupt_bundles(tmp_path: Path) -> None:
    _manifest(tmp_path, "daily", "daily-2026-07-10")
    _manifest(tmp_path, "weekly", "weekly-2026-w28")
    bad = tmp_path / "weekly" / "bad.json"
    bad.parent.mkdir(exist_ok=True)
    bad.write_text("not json", encoding="utf-8")
    counts = rebuild_indexes(tmp_path)
    assert counts == {"daily": 1, "weekly": 1}
    catalog = json.loads((tmp_path / "indexes" / "all.json").read_text())
    assert len(catalog) == 2
    assert {item["relative_directory"] for item in catalog} == {
        "daily/2026/07",
        "weekly/2026",
    }


def test_indexes_reject_misnamed_metadata_and_period_mismatch(tmp_path: Path) -> None:
    video = _manifest(tmp_path, "daily", "daily-2026-07-10")
    folder = tmp_path / "daily" / "2026" / "07"
    (folder / f"{video.id}.json").rename(folder / "unexpected-name.json")
    wrong_period = _manifest(tmp_path, "weekly", "weekly-2026-w28")
    payload = wrong_period.model_dump(mode="json") | {"type": "daily"}
    atomic_json(tmp_path / "weekly" / "2026" / f"{wrong_period.id}.json", payload)
    assert rebuild_indexes(tmp_path) == {"daily": 0, "weekly": 0}
    assert json.loads((tmp_path / "indexes" / "all.json").read_text()) == []


def test_render_lock_prevents_duplicates(tmp_path: Path) -> None:
    with render_lock(tmp_path):
        with pytest.raises(RuntimeError):
            with render_lock(tmp_path):
                pass


def test_config_rejects_arbitrary_fields() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"unknown": True})


def test_history_client_uses_period_aggregates_not_current_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        content = b"bounded"

        def json(self) -> list[list[dict[str, object]]]:
            return [
                [
                    {"entity_id": "sensor.example_steps", "state": "10"},
                    {"state": "20"},
                ]
            ]

    seen: dict[str, object] = {}
    client = object.__new__(HomeAssistantClient)

    def fake_get(path: str, *, params: dict[str, str]) -> Response:
        seen.update({"path": path, "params": params})
        return Response()

    monkeypatch.setattr(client, "_get", fake_get)
    history = client.fetch_allowlisted_history(
        ["sensor.example_steps"], period="daily", daily_hours=24, weekly_days=7
    )
    assert history == {"sensor.example_steps": ["10", "20"]}
    assert "/history/period/" in str(seen["path"])
    assert "/states/" not in str(seen["path"])


def test_sensor_discovery_reads_all_sensor_domains_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        content = b"bounded"

        def json(self) -> list[dict[str, object]]:
            return [
                {
                    "entity_id": "sensor.steps",
                    "state": "8123",
                    "attributes": {
                        "friendly_name": "My Steps",
                        "unit_of_measurement": "steps",
                        "device_class": "distance",
                    },
                },
                {
                    "entity_id": "binary_sensor.front_door",
                    "state": "off",
                    "attributes": {"friendly_name": "Front Door", "device_class": "door"},
                },
                {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
            ]

    client = object.__new__(HomeAssistantClient)
    seen: dict[str, object] = {}

    def fake_get(path: str, *, params: dict[str, str] | None = None) -> Response:
        seen.update({"path": path, "params": params})
        return Response()

    monkeypatch.setattr(client, "_get", fake_get)
    snapshots = client.fetch_sensor_snapshots()
    assert list(snapshots) == ["binary_sensor.front_door", "sensor.steps"]
    assert snapshots["sensor.steps"].name == "My Steps"
    assert snapshots["sensor.steps"].unit == "steps"
    assert seen == {"path": "/states", "params": None}


def test_sensor_discovery_enforces_complete_read_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        content = b"bounded"

        def json(self) -> list[dict[str, object]]:
            return [
                {"entity_id": f"sensor.item_{index}", "state": "1", "attributes": {}}
                for index in range(3)
            ]

    client = object.__new__(HomeAssistantClient)
    monkeypatch.setattr(client, "_get", lambda *_args, **_kwargs: Response())
    with pytest.raises(RuntimeError, match="above the configured safety cap"):
        client.fetch_sensor_snapshots(max_entities=2)


def test_empty_allowlist_never_calls_history_api(monkeypatch: pytest.MonkeyPatch) -> None:
    client = object.__new__(HomeAssistantClient)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("history API must not be called for an empty allowlist")

    monkeypatch.setattr(client, "_get", forbidden)
    assert client.fetch_allowlisted_history([], period="daily", daily_hours=24, weekly_days=7) == {}


def test_history_client_caps_observations(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        content = b"bounded"

        def json(self) -> list[list[dict[str, object]]]:
            return [[{"entity_id": "sensor.example", "state": str(index)} for index in range(100)]]

    client = object.__new__(HomeAssistantClient)
    monkeypatch.setattr(client, "_get", lambda *_args, **_kwargs: Response())
    history = client.fetch_allowlisted_history(
        ["sensor.example"],
        period="daily",
        daily_hours=24,
        weekly_days=7,
        max_observations_per_entity=8,
    )
    assert len(history["sensor.example"]) == 8
    assert history["sensor.example"][-1] == "99"


def test_history_client_splits_oversized_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def __init__(self, entities: list[str], oversized: bool) -> None:
            self.content = b"x" * (20 if oversized else 2)
            self.entities = entities

        def json(self) -> list[list[dict[str, object]]]:
            return [[{"entity_id": entity_id, "state": "1"}] for entity_id in self.entities]

    calls: list[list[str]] = []
    client = object.__new__(HomeAssistantClient)

    def fake_get(_path: str, *, params: dict[str, str]) -> Response:
        entities = params["filter_entity_id"].split(",")
        calls.append(entities)
        return Response(entities, oversized=len(entities) > 1)

    monkeypatch.setattr(client, "_get", fake_get)
    history = client.fetch_allowlisted_history(
        ["sensor.one", "sensor.two"],
        period="daily",
        daily_hours=24,
        weekly_days=7,
        max_response_bytes=10,
        batch_size=2,
    )
    assert calls == [["sensor.one", "sensor.two"], ["sensor.one"], ["sensor.two"]]
    assert history == {"sensor.one": ["1"], "sensor.two": ["1"]}


def test_prepare_addon_and_schedule_are_private_and_deterministic(tmp_path: Path) -> None:
    options_path = tmp_path / "options.json"
    config_path = tmp_path / "private" / "config.yaml"
    schedule_path = tmp_path / "private" / "schedule.json"
    options_path.write_text(
        json.dumps(
            {
                "allow_external_tts": True,
                "entity_allowlist": ["sensor.example"],
                "daily_time": "06:15",
                "weekly_time": "06:30",
            }
        ),
        encoding="utf-8",
    )
    prepared = prepare_addon(options_path, config_path, schedule_path)
    settings = load_settings(config_path)
    assert prepared.allow_external_tts is True
    assert settings.tts.requested_voice_id == "en-GB-LibbyNeural"
    assert settings.tts.allow_external_egress is True
    assert settings.data.entity_allowlist == ["sensor.example"]
    assert settings.data.auto_discover_sensors is True
    due, key = should_run(datetime(2026, 7, 10, 6, 15, tzinfo=UTC), "06:15", "")
    assert due is True
    assert should_run(datetime(2026, 7, 10, 6, 15, tzinfo=UTC), "06:15", key)[0] is False


def test_catalog_ids_are_namespaced_so_two_addons_can_share_one_directory() -> None:
    from video_runner.render import catalog_identifier

    moment = datetime(2026, 8, 5, 6, 15, tzinfo=UTC)
    claude_daily = catalog_identifier(PeriodType.DAILY, moment, "claude")
    legacy_daily = catalog_identifier(PeriodType.DAILY, moment, "")
    assert claude_daily == "daily-claude-2026-08-05"
    assert legacy_daily == "daily-2026-08-05"
    assert claude_daily != legacy_daily
    assert catalog_identifier(PeriodType.WEEKLY, moment, "claude") == "weekly-claude-2026-w32"
    assert catalog_identifier(PeriodType.WEEKLY, moment, "") == "weekly-2026-w32"
    # Every produced id must satisfy the BrowserVideo id constraint.
    for namespace in ("claude", "", "a" * 32):
        for period in (PeriodType.DAILY, PeriodType.WEEKLY):
            identifier = catalog_identifier(period, moment, namespace)
            assert re.fullmatch(r"[a-z0-9-]{5,80}", identifier), identifier


def test_video_id_namespace_is_validated_at_config_load() -> None:
    assert GenerationConfig().video_id_namespace == "claude"
    assert GenerationConfig(video_id_namespace="").video_id_namespace == ""
    for bad in ("Claude", "claude_1", "-claude", "claude-", "cl aude", "a" * 33):
        with pytest.raises(ValidationError):
            GenerationConfig(video_id_namespace=bad)
        with pytest.raises(ValidationError):
            SchedulerOptions.model_validate({"video_id_namespace": bad})


def test_prepare_addon_builds_the_claude_provider_from_options(tmp_path: Path) -> None:
    options_path = tmp_path / "options.json"
    config_path = tmp_path / "private" / "config.yaml"
    options_path.write_text(
        json.dumps(
            {
                "storyboard_provider": "claude",
                "claude_model": "claude-opus-5",
                "claude_config_dir": "/homeassistant/.claude-video-runner",
                "claude_timeout_seconds": 420,
                "story_detail": "personal",
            }
        ),
        encoding="utf-8",
    )
    prepare_addon(options_path, config_path, tmp_path / "private" / "schedule.json")
    generation = load_settings(config_path).generation
    assert generation.provider == "claude"
    assert generation.model == "claude-opus-5"
    assert generation.timeout_seconds == 420
    assert generation.story_detail == "personal"
    assert generation.claude_config_dir == Path("/homeassistant/.claude-video-runner")


def test_scheduler_options_reject_out_of_bounds_and_relative_paths() -> None:
    for payload in (
        {"storyboard_provider": "openai"},
        {"story_detail": "everything"},
        {"claude_timeout_seconds": 30},
        {"claude_timeout_seconds": 1200},
        {"claude_config_dir": "relative/path"},
        {"claude_model": ""},
    ):
        with pytest.raises(ValidationError):
            SchedulerOptions.model_validate(payload)


def test_a_failed_generation_keeps_the_scheduler_alive_and_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        video_directory=tmp_path / "share",
        private_data_directory=tmp_path / "private",
    )
    calls: list[str] = []

    def flaky(_config: Path, *arguments: str) -> None:
        calls.append(arguments[-1])
        if arguments[-1] == "daily":
            raise RuntimeError("video generation command failed with exit code 137")

    monkeypatch.setattr("video_runner.scheduler._run_child", flaky)
    config = tmp_path / "config.yaml"
    config.write_text("{}", encoding="utf-8")
    schedule: dict[str, object] = {"generate_personal_on_start": True}
    _run_startup_generation(settings, schedule, config)
    # A failed daily must not skip the weekly, and must not raise.
    assert calls == ["daily", "weekly"]
    status = read_generation_status(settings.private_data_directory)
    assert status["daily"]["ok"] is False  # type: ignore[index]
    assert "137" in str(status["daily"]["reason"])  # type: ignore[index]
    assert status["weekly"]["ok"] is True  # type: ignore[index]
    # The version marker is withheld so the next start retries.
    assert not (settings.private_data_directory / "personalization-version.json").exists()
    _run_startup_generation(settings, schedule, config)
    assert calls == ["daily", "weekly", "daily", "weekly"]


def test_doctor_reports_claude_readiness_without_echoing_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    (config_dir / ".credentials.json").write_text('{"secret":"super-secret-oauth"}', "utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        f"video_directory: {(tmp_path / 'share').as_posix()}\n"
        f"private_data_directory: {(tmp_path / 'private').as_posix()}\n"
        "generation:\n"
        "  provider: claude\n"
        f"  claude_config_dir: {config_dir.as_posix()}\n"
        "  model: claude-opus-5\n",
        encoding="utf-8",
    )
    (tmp_path / "share").mkdir()
    monkeypatch.setattr("video_runner.cli.shutil.which", lambda name: f"/usr/bin/{name}")
    result = CliRunner().invoke(
        app,
        ["doctor", "--config", str(config), "--no-require-supervisor-token", "--require-claude"],
        env={"VIDEO_RUNNER_TEST_MODE": "1"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["claude_cli_present"] is True
    assert payload["claude_credentials_present"] is True
    assert payload["claude_config_dir_writable"] is True
    assert payload["claude_model"] == "claude-opus-5"
    assert payload["storyboard_provider"] == "claude"
    assert "super-secret-oauth" not in result.output


def test_doctor_fails_when_claude_is_required_but_not_logged_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        f"video_directory: {(tmp_path / 'share').as_posix()}\n"
        f"private_data_directory: {(tmp_path / 'private').as_posix()}\n"
        "generation:\n"
        "  provider: claude\n"
        f"  claude_config_dir: {(tmp_path / 'missing').as_posix()}\n"
        "  offline_fallback: false\n",
        encoding="utf-8",
    )
    (tmp_path / "share").mkdir()
    monkeypatch.setattr("video_runner.cli.shutil.which", lambda name: f"/usr/bin/{name}")
    result = CliRunner().invoke(
        app,
        ["doctor", "--config", str(config), "--no-require-supervisor-token"],
        env={"VIDEO_RUNNER_TEST_MODE": "1"},
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["claude_credentials_present"] is False
    assert payload["ready"] is False


def test_preview_prompt_shows_the_exact_disclosure_before_enabling(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        f"video_directory: {(tmp_path / 'share').as_posix()}\n"
        f"private_data_directory: {(tmp_path / 'private').as_posix()}\n"
        "generation:\n"
        "  provider: claude\n"
        "  story_detail: personal\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app,
        ["preview-prompt", "--config", str(config), "--synthetic"],
        env={"VIDEO_RUNNER_TEST_MODE": "1"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["story_detail"] == "personal"
    assert "8,123 steps" in payload["prompt"]
    assert "145 and 160 words" in payload["system_prompt"]
    # The argv is auditable without repeating the whole payload twice.
    assert "<PROMPT>" in payload["argv"]
    assert "<SYSTEM_PROMPT>" in payload["argv"]
    assert "sensor." not in payload["prompt"]


def test_generate_blocks_raw_values_only_when_they_could_leave_the_device(
    tmp_path: Path,
) -> None:
    base = (
        f"video_directory: {(tmp_path / 'share').as_posix()}\n"
        f"private_data_directory: {(tmp_path / 'private').as_posix()}\n"
    )
    conflicting = tmp_path / "conflict.yaml"
    conflicting.write_text(
        base
        + "data:\n  include_raw_values_in_external_requests: true\n"
        + "tts:\n  allow_external_egress: true\n",
        encoding="utf-8",
    )
    (tmp_path / "share").mkdir()
    result = CliRunner().invoke(
        app,
        ["generate", "--config", str(conflicting), "--synthetic", "--mock-tts"],
        env={"VIDEO_RUNNER_TEST_MODE": "1"},
    )
    assert result.exit_code != 0
    assert "third-party speech service" in result.output.replace("\n", "")


def test_startup_generates_personal_daily_and_weekly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        video_directory=tmp_path / "share",
        private_data_directory=tmp_path / "private",
    )
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "video_runner.scheduler._run_child",
        lambda _config, *arguments: calls.append(arguments),
    )
    config = tmp_path / "config.yaml"
    config.write_text("{}", encoding="utf-8")
    schedule: dict[str, object] = {"generate_personal_on_start": True}
    _run_startup_generation(settings, schedule, config)
    _run_startup_generation(settings, schedule, config)
    assert calls == [
        ("generate", "--period", "daily"),
        ("generate", "--period", "weekly"),
    ]


def test_exact_libby_mapping_and_no_silent_substitution(monkeypatch: pytest.MonkeyPatch) -> None:
    async def voices() -> list[dict[str, str]]:
        return [{"ShortName": "en-GB-LibbyNeural", "Locale": "en-GB", "Gender": "Female"}]

    monkeypatch.setattr("video_runner.tts.edge_tts.list_voices", voices)
    assert asyncio.run(resolve_voice(TTSConfig())) == "en-GB-LibbyNeural"
    with pytest.raises(RuntimeError):
        asyncio.run(resolve_voice(TTSConfig(requested_voice_id="en-GB-SoniaNeural")))


def test_libby_uses_natural_rate_and_rejects_speedup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, str] = {}

    async def voice(_config: TTSConfig) -> str:
        return "en-GB-LibbyNeural"

    class Communicate:
        def __init__(self, _text: str, *, voice: str, rate: str) -> None:
            seen.update({"voice": voice, "rate": rate})

        async def save(self, path: str) -> None:
            Path(path).write_bytes(b"synthetic")

    monkeypatch.setattr("video_runner.tts.resolve_voice", voice)
    monkeypatch.setattr("video_runner.tts.edge_tts.Communicate", Communicate)
    assert synthesize_edge("safe generic narration", tmp_path / "speech.mp3", TTSConfig()) == (
        "en-GB-LibbyNeural"
    )
    assert seen == {"voice": "en-GB-LibbyNeural", "rate": "+0%"}
    with pytest.raises(ValidationError):
        TTSConfig(speaking_rate=1.01)
