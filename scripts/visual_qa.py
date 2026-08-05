from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import numpy as np
from imageio_ffmpeg import get_ffmpeg_exe
from moviepy import VideoFileClip
from PIL import Image, ImageDraw, ImageFont


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _hash(frame: np.ndarray) -> np.ndarray:
    image = Image.fromarray(frame).convert("L").resize((16, 16))
    values = np.asarray(image, dtype=np.float32)
    return values > float(values.mean())


def _distinct_count(hashes: list[np.ndarray], distance: int = 18) -> int:
    representatives: list[np.ndarray] = []
    for candidate in hashes:
        if all(int(np.count_nonzero(candidate != item)) >= distance for item in representatives):
            representatives.append(candidate)
    return len(representatives)


def _audio_metrics(path: Path) -> tuple[float | None, float]:
    completed = subprocess.run(
        [
            get_ffmpeg_exe(),
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "ebur128=peak=true,silencedetect=noise=-50dB:d=0.5",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    loudness = re.findall(r"I:\s*(-?[0-9.]+) LUFS", completed.stderr)
    silences = [
        float(value) for value in re.findall(r"silence_duration:\s*([0-9.]+)", completed.stderr)
    ]
    return (float(loudness[-1]) if loudness else None, max(silences, default=0.0))


def analyze(path: Path, output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    with VideoFileClip(str(path)) as clip:
        duration = float(clip.duration)
        three_second_times = [
            min(duration - 0.05, 0.1 + index * 3) for index in range(int(duration // 3) + 1)
        ]
        one_second_times = [min(duration - 0.05, 0.1 + index) for index in range(int(duration))]
        frames = [clip.get_frame(value).astype(np.uint8) for value in three_second_times]
        cadence_frames = [clip.get_frame(value).astype(np.uint8) for value in one_second_times]
        fps = float(clip.fps)
        width, height = (int(value) for value in clip.size)
    differences = [
        float(np.mean(np.abs(left.astype(np.int16) - right.astype(np.int16))))
        for left, right in zip(cadence_frames, cadence_frames[1:], strict=False)
    ]
    longest_negligible = 0
    current = 0
    for difference in differences:
        current = current + 1 if difference < 0.15 else 0
        longest_negligible = max(longest_negligible, current)
    thumbs = [Image.fromarray(frame).resize((180, 320)) for frame in frames]
    columns = 5
    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * 190 + 10, rows * 350 + 10), (8, 10, 18))
    draw = ImageDraw.Draw(sheet)
    label_font = _font(18)
    for index, thumb in enumerate(thumbs):
        x = 10 + (index % columns) * 190
        y = 10 + (index // columns) * 350
        sheet.paste(thumb, (x, y))
        draw.text((x, y + 324), f"{three_second_times[index]:.1f}s", font=label_font, fill="white")
    contact_sheet = output_dir / f"{path.stem}-contact-sheet.jpg"
    sheet.save(contact_sheet, quality=88)
    loudness, longest_silence = _audio_metrics(path)
    return {
        "path": str(path),
        "duration_seconds": round(duration, 3),
        "resolution": [width, height],
        "fps": round(fps, 3),
        "file_size_bytes": path.stat().st_size,
        "sampled_frames": len(frames),
        "distinct_compositions_estimate": _distinct_count([_hash(frame) for frame in frames]),
        "mean_one_second_frame_difference": round(float(np.mean(differences)), 3),
        "minimum_one_second_frame_difference": round(min(differences), 3),
        "longest_negligible_change_seconds": longest_negligible,
        "integrated_loudness_lufs": loudness,
        "longest_silence_seconds": round(longest_silence, 3),
        "contact_sheet": str(contact_sheet),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("videos", nargs="+", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    report = [analyze(path.resolve(), args.output_dir.resolve()) for path in args.videos]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
