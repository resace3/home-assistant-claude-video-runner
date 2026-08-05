from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
from moviepy import VideoFileClip
from typer.testing import CliRunner

from video_runner.cli import app
from video_runner.render import validate_output


@pytest.mark.integration
def test_real_synthetic_daily_and_weekly_movies_match_viewer_contract(tmp_path: Path) -> None:
    share = tmp_path / "share"
    private = tmp_path / "private"
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            (
                f"video_directory: {share.as_posix()}",
                f"private_data_directory: {private.as_posix()}",
                # Pin the offline provider so this exercises rendering, not the CLI.
                "generation:",
                "  provider: offline",
                "  video_id_namespace: claude",
                "render:",
                "  width: 360",
                "  height: 640",
                "  fps: 12",
                "  preset: ultrafast",
            )
        ),
        encoding="utf-8",
    )
    original_cwd = Path.cwd()
    read_only_cwd = tmp_path / "read-only-image-workdir"
    read_only_cwd.mkdir()
    read_only_cwd.chmod(0o555)
    try:
        os.chdir(read_only_cwd)
        results = [
            CliRunner().invoke(
                app,
                [
                    "generate",
                    "--config",
                    str(config),
                    "--period",
                    period,
                    "--synthetic",
                    "--mock-tts",
                ],
                env={"VIDEO_RUNNER_TEST_MODE": "1", "SUPERVISOR_TOKEN": ""},
            )
            for period in ("daily", "weekly")
        ]
    finally:
        os.chdir(original_cwd)
        read_only_cwd.chmod(0o755)
    assert all(result.exit_code == 0 for result in results), [
        (result.output, repr(result.exception)) for result in results
    ]
    catalog_path = share / "indexes" / "all.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert len(catalog) == 2
    assert {item["type"] for item in catalog} == {"daily", "weekly"}
    assert all(item["id"].split("-")[1] == "claude" for item in catalog)
    for item in catalog:
        folder = share / Path(item["relative_directory"])
        assert (folder / f"{item['id']}.json").is_file()
        assert item["video_filename"].startswith(f"{item['id']}-")
        assert (folder / item["thumbnail_filename"]).is_file()
        assert (folder / item["captions_filename"]).is_file()
        report = validate_output(folder / item["video_filename"])
        assert 55 <= report["duration_seconds"] <= 65
        assert report["audio"] is True
        assert report["validation_backend"] == "moviepy+ffprobe+ffmpeg-decode"
        with VideoFileClip(str(folder / item["video_filename"])) as clip:
            scene_frames = [
                clip.get_frame(second).astype(np.int16) for second in (2, 7, 15, 25, 35, 45, 55)
            ]
            within_scene = float(
                np.mean(
                    np.abs(
                        clip.get_frame(0.5).astype(np.int16) - clip.get_frame(2.5).astype(np.int16)
                    )
                )
            )
        structural_changes = [
            float(np.mean(np.abs(left - right)))
            for left, right in zip(scene_frames, scene_frames[1:], strict=False)
        ]
        assert min(structural_changes) > 1.0
        assert within_scene > 0.5
