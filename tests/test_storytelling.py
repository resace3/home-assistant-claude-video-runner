from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from video_runner.audio import normalize_and_mix, procedural_ambient
from video_runner.captions import phrase_chunks, write_vtt
from video_runner.config import RenderConfig
from video_runner.friendly import friendly_display_name, semantic_category
from video_runner.layouts import (
    PLAYER_SAFE_BOTTOM,
    SAFE_MARGIN_X,
    font,
    render_scene_frame,
    wrap_text,
)
from video_runner.model_policy import personalized_storyboard
from video_runner.motion import ease_in_out, ease_out_cubic
from video_runner.schemas import PeriodType, Scene, Visual
from video_runner.story_selection import select_story
from video_runner.themes import theme_for


def story_summary() -> dict[str, object]:
    names = (
        ("sleep_minutes_asleep", "345 min", "sleep", [320, 338, 350, 342, 360, 345], 7.8),
        ("sleep_minutes_awake", "32 min", "sleep", [44, 39, 36, 31, 29, 32], -14.0),
        ("steps", "8,123 steps", "distance", [2200, 3100, 4700, 6400, 7800, 8123], 22.0),
        ("resting_heart_rate", "61 bpm", "heart", [63, 62, 61, 60, 61], -3.2),
        ("meditation_minutes", "18 min", "duration", [0, 10, 12, 15, 18], 20.0),
    )
    highlights: list[dict[str, object]] = []
    for index, (label, current, device_class, values, delta) in enumerate(names):
        highlights.append(
            {
                "label": label,
                "current": current,
                "detail": "Changed gradually across the period",
                "device_class": device_class,
                "observations": len(values),
                "numeric_value": values[-1],
                "baseline": float(np.median(values)),
                "delta_percent": delta,
                "trend": "steady" if abs(delta) < 4 else "changed",
                "unit": current.split()[-1],
                "chart_values": values,
                "story_score": 100 - index,
            }
        )
    return {
        "discovered_sensor_count": 18,
        "usable_sensor_count": 16,
        "history_sensor_count": 14,
        "highlights": highlights,
    }


def test_friendly_mapping_and_semantic_categories() -> None:
    # Person-prefixed entity labels must still resolve. The prefix here is
    # deliberately synthetic: real entity identifiers never enter this repository.
    assert friendly_display_name("person_a_sleep_minutes_asleep") == "Time asleep"
    assert friendly_display_name("sensor_resting_heart_rate") == "Resting heart rate"
    assert friendly_display_name("my_custom_signal") == "My Custom Signal"
    assert semantic_category("Time asleep", "duration") == "sleep"
    assert semantic_category("Steps", "distance") == "movement"
    assert semantic_category("Resting heart rate", "heart") == "recovery"


def test_story_selection_is_ranked_diverse_and_deterministic() -> None:
    first = select_story(story_summary())
    second = select_story(story_summary())
    assert first == second
    assert first is not None
    assert first.primary.label == "Steps"
    assert len(first.headlines) == 3
    assert len({item.category for item in first.headlines}) >= 2
    assert first.comparison is not None


def test_daily_and_weekly_storyboards_are_distinct_coherent_and_private() -> None:
    daily = personalized_storyboard(PeriodType.DAILY, story_summary())
    weekly = personalized_storyboard(PeriodType.WEEKLY, story_summary())
    assert len(daily.scenes) == len(weekly.scenes) == 7
    assert daily.scenes[0].start_seconds == weekly.scenes[0].start_seconds == 0
    assert daily.scenes[0].duration_seconds == weekly.scenes[0].duration_seconds == 4
    assert len({scene.visual.kind for scene in daily.scenes}) == 7
    assert "seven_day" in {scene.visual.kind for scene in weekly.scenes}
    assert "seven_day" not in {scene.visual.kind for scene in daily.scenes}
    assert len({scene.scene_id for scene in daily.scenes}) == 7
    assert sum(scene.duration_seconds for scene in daily.scenes) == 60
    assert sum(scene.duration_seconds for scene in weekly.scenes) == 60
    assert len(daily.narration.split()) == len(weekly.narration.split()) == 150
    encoded = json.dumps(daily.model_dump(mode="json"))
    assert "sensor." not in encoded
    assert "entity_id" not in encoded
    assert "8,123" in encoded
    assert "8,123" not in daily.narration


def test_sparse_story_acknowledges_limits_without_duplicate_headline_cards() -> None:
    summary = story_summary()
    summary["highlights"] = [summary["highlights"][0]]  # type: ignore[index]
    board = personalized_storyboard(PeriodType.DAILY, summary)
    glance = next(scene for scene in board.scenes if scene.scene_id == "daily-glance")
    cards = glance.visual.payload["cards"]
    assert isinstance(cards, list) and len(cards) == 1
    assert any(scene.visual.kind == "data_quality" for scene in board.scenes)
    assert "Only one useful category" in " ".join(scene.body for scene in board.scenes)


def _scene(kind: str) -> Scene:
    payload: dict[str, object] = {
        "category": "sleep",
        "current": "345 min",
        "comparison": "12% above your period baseline",
        "numeric_value": 345,
        "baseline": 310,
        "unit": "min",
        "series": [300, 320, 315, 340, 330, 345, 350],
        "cards": [
            {"label": "Time asleep", "current": "345 min", "comparison": "Above baseline"},
            {"label": "Time awake", "current": "32 min", "comparison": "Near baseline"},
            {"label": "Efficiency", "current": "92%", "comparison": "Steady"},
        ],
        "labels": ["Time asleep", "Time awake"],
        "values": [345, 32],
    }
    return Scene(
        scene_id=f"test-{kind.replace('_', '-')}",
        start_seconds=0,
        duration_seconds=8,
        scene_type="metric",
        heading="A personal pattern",
        body="One short, grounded observation.",
        visual=Visual(kind=kind, data_reference="synthetic", payload=payload),
        caption="A readable phrase-level caption",
    )


@pytest.mark.parametrize(
    "kind",
    [
        "hook",
        "metric_grid",
        "sparkline",
        "seven_day",
        "comparison",
        "progress_ring",
        "recommendation",
        "closing",
        "data_quality",
    ],
)
def test_visual_components_render_nonblank_safe_frames(kind: str) -> None:
    config = RenderConfig(width=360, height=640, fps=24)
    frame = render_scene_frame(_scene(kind), config, 2.4, 5.0)
    assert frame.shape == (640, 360, 3)
    assert frame.dtype == np.uint8
    assert float(frame.std()) > 12
    assert SAFE_MARGIN_X < config.width // 2
    assert PLAYER_SAFE_BOTTOM >= 140


def test_motion_changes_meaningfully_within_a_scene() -> None:
    config = RenderConfig(width=360, height=640, fps=24)
    scene = _scene("sparkline")
    frames = [render_scene_frame(scene, config, second, second) for second in (0.2, 1.2, 2.2, 3.2)]
    differences = [
        float(np.mean(np.abs(left.astype(int) - right.astype(int))))
        for left, right in zip(frames, frames[1:], strict=False)
    ]
    assert min(differences) > 0.15
    assert ease_out_cubic(0.5) > 0.5
    assert 0 < ease_in_out(0.5) < 1


def test_text_wrap_stays_short_and_inside_requested_width() -> None:
    image = Image.new("RGB", (360, 640))
    draw = ImageDraw.Draw(image)
    face = font(28)
    lines = wrap_text(
        draw, "A deliberately long personal reflection for a narrow mobile frame", face, 250, 3
    )
    assert 1 < len(lines) <= 3
    assert all(draw.textlength(line, font=face) <= 250 for line in lines)


def test_phrase_captions_are_short_and_valid(tmp_path: Path) -> None:
    narration = (
        "This phrase is short; this second phrase stays readable, and the ending remains calm."
    )
    chunks = phrase_chunks(narration)
    assert all(len(chunk.split()) <= 7 for chunk in chunks)
    path = tmp_path / "captions.vtt"
    write_vtt(path, narration, 8.0)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("WEBVTT")
    assert "00:00:00.000 -->" in text
    assert "sensor." not in text


def test_procedural_audio_is_deterministic_faded_and_bounded(tmp_path: Path) -> None:
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    procedural_ambient(first, 1.0, [0.2], sample_rate=8000)
    procedural_ambient(second, 1.0, [0.2], sample_rate=8000)
    assert first.read_bytes() == second.read_bytes()
    with wave.open(str(first), "rb") as stream:
        samples = np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2")
    assert samples[0] == 0
    assert abs(int(samples[-1])) < 200
    assert int(np.max(np.abs(samples))) < 32767


def test_audio_mix_uses_quiet_bed_and_loudness_normalization(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[str] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen.extend(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("video_runner.audio.subprocess.run", fake_run)
    normalize_and_mix(
        tmp_path / "source.mp4", tmp_path / "out.mp4", ambient=tmp_path / "bed.wav", duration=60
    )
    command = " ".join(seen)
    assert "volume=0.055" in command
    assert "loudnorm=I=-16:TP=-1.5" in command
    assert "+faststart" in command


def test_adaptive_themes_are_semantic_not_alarming() -> None:
    assert theme_for("sleep").accent != theme_for("movement").accent
    assert theme_for("recovery").background != (255, 0, 0)
    assert theme_for("sleep", "teal") == theme_for("movement")
