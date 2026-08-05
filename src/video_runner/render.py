from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict

import numpy as np
from imageio_ffmpeg import get_ffmpeg_exe
from moviepy import AudioFileClip, VideoClip, VideoFileClip, concatenate_videoclips
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageFont

from .audio import normalize_and_mix, procedural_ambient
from .captions import write_vtt
from .config import RenderConfig, Settings
from .layouts import render_scene_frame
from .schemas import BrowserVideo, PeriodType, PrivateAudit, Scene, Storyboard
from .storage import atomic_json, rebuild_indexes, render_lock
from .tts import synthesize_edge, synthesize_test_tone

MIN_NARRATION_WPM = 145.0
MAX_NARRATION_WPM = 160.0


class MediaProbe(TypedDict):
    duration_seconds: float
    width: int
    height: int
    video_codec: str
    audio_codec: str
    pixel_format: str


def narration_words_per_minute(text: str, duration_seconds: float) -> float:
    if duration_seconds <= 0:
        raise ValueError("narration duration must be positive")
    return len(text.split()) / duration_seconds * 60


def _animated_clip(
    scene: Scene,
    config: RenderConfig,
    *,
    scale: float,
    global_start: float,
) -> VideoClip:
    cache_bucket = -1
    cache_frame: NDArray[np.uint8] | None = None

    def frame_function(seconds: float) -> NDArray[np.uint8]:
        nonlocal cache_bucket, cache_frame
        bucket = max(0, int(seconds * config.motion_sample_fps))
        if cache_frame is None or bucket != cache_bucket:
            scaled_seconds = bucket / config.motion_sample_fps
            scene_seconds = min(scene.duration_seconds, scaled_seconds / scale)
            cache_frame = render_scene_frame(
                scene,
                config,
                scene_seconds,
                global_start + scene_seconds,
            )
            cache_bucket = bucket
        return cache_frame

    return VideoClip(
        frame_function=frame_function,
        duration=scene.duration_seconds * scale,
    )


def _font(size: int) -> Any:
    candidates = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
    )
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, face: Any, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=face) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _scene_image(path: Path, heading: str, body: str, config: RenderConfig, index: int) -> None:
    palette = ((26, 31, 66), (17, 60, 72), (52, 35, 72), (31, 54, 48), (64, 42, 28), (31, 45, 70))
    image = Image.new("RGB", (config.width, config.height), palette[index % len(palette)])
    draw = ImageDraw.Draw(image)
    heading_font = _font(max(16, config.width // 18))
    body_font = _font(max(11, config.width // 34))
    small_font = _font(max(8, config.width // 55))
    margin = config.width // 10
    draw.rounded_rectangle(
        (margin, config.height // 8, config.width - margin, config.height * 7 // 8),
        radius=max(12, config.width // 20),
        fill=(8, 12, 24, 190),
        outline=(153, 211, 255),
        width=2,
    )
    y = float(config.height * 0.26)
    for line in _wrap(draw, heading, heading_font, config.width - 4 * margin):
        draw.text((2 * margin, y), line, font=heading_font, fill="white")
        y += heading_font.size * 1.15
    y += max(10, config.width // 30)
    for line in _wrap(draw, body, body_font, config.width - 4 * margin):
        draw.text((2 * margin, y), line, font=body_font, fill=(220, 230, 240))
        y += body_font.size * 1.3
    draw.text(
        (2 * margin, int(config.height * 0.82)),
        "Private reflection • Not medical advice",
        font=small_font,
        fill=(175, 193, 210),
    )
    image.save(path, "PNG", optimize=True)


def _captions(path: Path, narration: str, duration: float) -> None:
    words = narration.split()
    chunks = [words[index : index + 12] for index in range(0, len(words), 12)]
    cue = duration / len(chunks)

    def stamp(seconds: float) -> str:
        milliseconds = int(seconds * 1000)
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, millis = divmod(remainder, 1000)
        return f"{hours:02}:{minutes:02}:{secs:02}.{millis:03}"

    lines = ["WEBVTT", ""]
    for index, chunk in enumerate(chunks):
        start = index * cue
        end = min(duration, (index + 1) * cue)
        lines.extend((f"{stamp(start)} --> {stamp(end)}", " ".join(chunk), ""))
    path.write_text("\n".join(lines), encoding="utf-8")


def _thumbnail(source: Path, destination: Path) -> None:
    with Image.open(source) as image:
        image.thumbnail((540, 960))
        image.save(destination, "WEBP", quality=82, method=6)


def catalog_identifier(period_type: PeriodType, now: datetime, namespace: str) -> str:
    """Build the stable catalog id, namespaced so two add-ons can share one directory.

    The sidecar and index entry are keyed on this id, so without a namespace a second
    add-on publishing on the same date atomically replaces the first one's entry and
    orphans its media files.
    """
    prefix = f"{namespace}-" if namespace else ""
    if period_type == PeriodType.DAILY:
        return f"daily-{prefix}{now:%Y-%m-%d}"
    return f"weekly-{prefix}{now:%G}-w{now:%V}"


def render_storyboard(
    storyboard: Storyboard,
    settings: Settings,
    *,
    use_test_tone: bool = False,
    private_audit: PrivateAudit | None = None,
    lock_already_held: bool = False,
) -> BrowserVideo:
    root = settings.video_directory
    now = datetime.now(UTC)
    identifier = catalog_identifier(
        storyboard.period_type, now, settings.generation.video_id_namespace
    )
    if storyboard.period_type == PeriodType.DAILY:
        folder = root / "daily" / f"{now:%Y}" / f"{now:%m}"
        period_start = now - timedelta(days=1)
    else:
        folder = root / "weekly" / f"{now:%G}"
        period_start = now - timedelta(days=7)
    # Assets are immutable per render. The stable metadata sidecar is switched atomically only
    # after the complete new bundle validates, so a crash cannot expose mixed old/new assets.
    revision = now.strftime("%Y%m%dT%H%M%S%fZ")
    basename = f"{identifier}-{revision}"
    (root / "temporary").mkdir(parents=True, exist_ok=True)
    lock_context = nullcontext() if lock_already_held else render_lock(root)
    with (
        lock_context,
        tempfile.TemporaryDirectory(
            prefix="video-render-", dir=root / "temporary"
        ) as temporary_name,
    ):
        temporary = Path(temporary_name)
        audio_path = temporary / ("narration.wav" if use_test_tone else "narration.mp3")
        if use_test_tone or settings.tts.provider == "mock":
            voice = synthesize_test_tone(audio_path, 60)
        else:
            if not settings.tts.allow_external_egress:
                raise RuntimeError(
                    "External TTS is disabled; review the narration disclosure and opt in explicitly"
                )
            voice = synthesize_edge(storyboard.narration, audio_path, settings.tts)
        scene_clips: list[VideoClip] = []
        audio: AudioFileClip | None = None
        video = None
        output = temporary / f"{basename}.mp4"
        first_frame = temporary / "first-frame.png"
        try:
            audio = AudioFileClip(str(audio_path))
            target_duration = float(audio.duration)
            if not 55 <= target_duration <= 65:
                raise ValueError(
                    "narration duration must already be within 55-65 seconds; audio is never trimmed or sped up"
                )
            if not use_test_tone and settings.tts.provider != "mock":
                narration_wpm = narration_words_per_minute(storyboard.narration, target_duration)
                if not MIN_NARRATION_WPM <= narration_wpm <= MAX_NARRATION_WPM:
                    raise ValueError(
                        "Libby narration must be 145-160 WPM at natural 1.0x; "
                        "shorten the script or extend scenes instead of changing playback speed"
                    )
            scale = target_duration / sum(scene.duration_seconds for scene in storyboard.scenes)
            global_start = 0.0
            for scene in storyboard.scenes:
                scene_clips.append(
                    _animated_clip(
                        scene,
                        settings.render,
                        scale=scale,
                        global_start=global_start,
                    )
                )
                global_start += scene.duration_seconds
            Image.fromarray(
                render_scene_frame(storyboard.scenes[0], settings.render, 0.15, 0.15)
            ).save(first_frame, "PNG", optimize=True)
            video = concatenate_videoclips(scene_clips, method="compose").with_audio(audio)
            video.write_videofile(
                str(output),
                fps=settings.render.fps,
                codec="libx264",
                audio_codec="aac",
                temp_audiofile=str(temporary / f"{basename}.audio.m4a"),
                preset=settings.render.preset,
                threads=2,
                logger=None,
                ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
            )
        finally:
            if video is not None:
                video.close()
            if audio is not None:
                audio.close()
            for clip in scene_clips:
                clip.close()
        normalized = temporary / f"{basename}.normalized.mp4"
        ambient_path: Path | None = None
        if settings.render.music_enabled or settings.render.sound_effects_enabled:
            ambient_path = temporary / "procedural-ambient.wav"
            cues = [
                scene.start_seconds * scale
                for scene in storyboard.scenes
                if settings.render.sound_effects_enabled and scene.audio_cue != "none"
            ]
            procedural_ambient(
                ambient_path,
                target_duration,
                cues,
                music_enabled=settings.render.music_enabled,
            )
        normalize_and_mix(
            output,
            normalized,
            ambient=ambient_path,
            duration=target_duration,
        )
        os.replace(normalized, output)
        _decode_validate(output, settings.render.width, settings.render.height)
        captions = temporary / f"{basename}.vtt"
        thumbnail = temporary / f"{basename}.webp"
        write_vtt(captions, storyboard.narration, target_duration)
        _thumbnail(first_frame, thumbnail)
        browser = BrowserVideo(
            id=identifier,
            type=storyboard.period_type,
            title=storyboard.title,
            # Truncated defensively. The schema already bounds this, but the video
            # is fully rendered by now and must never be discarded over a summary
            # that is merely too long to display.
            description=storyboard.summary[:240],
            created_at=now,
            period_start=period_start,
            period_end=now,
            duration_seconds=round(target_duration, 3),
            video_filename=output.name,
            thumbnail_filename=thumbnail.name,
            captions_filename=captions.name,
            generation_status="complete",
        )
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o755)
        for source in (output, thumbnail, captions):
            shutil.move(str(source), folder / source.name)
            (folder / source.name).chmod(0o644)
        metadata_path = folder / f"{identifier}.json"
        atomic_json(metadata_path, browser.model_dump(mode="json"))
        metadata_path.chmod(0o644)
        if private_audit:
            audit = private_audit.model_copy(update={"video_id": identifier})
            audit_payload = audit.model_dump(mode="json") | {
                "voice_id": voice,
                "sha256": hashlib.sha256((folder / output.name).read_bytes()).hexdigest(),
            }
            atomic_json(
                settings.private_data_directory / "audit" / f"{basename}.json", audit_payload
            )
        rebuild_indexes(root)
        return browser


def _ffprobe_executable() -> str:
    executable = shutil.which("ffprobe")
    if executable:
        return executable
    configured_ffmpeg = Path(get_ffmpeg_exe())
    names = ("ffprobe.exe", "ffprobe") if os.name == "nt" else ("ffprobe",)
    for name in names:
        candidate = configured_ffmpeg.with_name(name)
        if candidate.is_file():
            return str(candidate)
    raise ValueError(
        "ffprobe is required for media validation; install the ffmpeg package and rerun doctor"
    )


def _probe_media(path: Path) -> MediaProbe:
    completed = subprocess.run(
        [
            _ffprobe_executable(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode:
        raise ValueError("ffprobe could not inspect the rendered video")
    try:
        payload = json.loads(completed.stdout)
        streams = payload["streams"]
        video_stream = next(stream for stream in streams if stream.get("codec_type") == "video")
        audio_stream = next(stream for stream in streams if stream.get("codec_type") == "audio")
        duration = float(payload.get("format", {}).get("duration") or video_stream["duration"])
        return {
            "duration_seconds": duration,
            "width": int(video_stream["width"]),
            "height": int(video_stream["height"]),
            "video_codec": str(video_stream["codec_name"]),
            "audio_codec": str(audio_stream["codec_name"]),
            "pixel_format": str(video_stream["pix_fmt"]),
        }
    except (
        AttributeError,
        KeyError,
        StopIteration,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("ffprobe returned incomplete or invalid stream metadata") from exc


def _decode_validate(
    path: Path, expected_width: int | None = None, expected_height: int | None = None
) -> dict[str, object]:
    if not path.is_file() or path.stat().st_size < 1024:
        raise ValueError("video output is missing or too small")
    probe = _probe_media(path)
    with VideoFileClip(str(path)) as clip:
        moviepy_duration = float(clip.duration)
        if not 55 <= moviepy_duration <= 65:
            raise ValueError("video duration is outside 55-65 seconds")
        if clip.audio is None:
            raise ValueError("video has no audio stream")
        moviepy_size = tuple(int(value) for value in clip.size)
    duration = float(probe["duration_seconds"])
    width, height = int(probe["width"]), int(probe["height"])
    if not 55 <= duration <= 65:
        raise ValueError("ffprobe duration is outside 55-65 seconds")
    if abs(moviepy_duration - duration) > 0.25:
        raise ValueError("MoviePy and ffprobe disagree about video duration")
    if moviepy_size != (width, height):
        raise ValueError("MoviePy and ffprobe disagree about video resolution")
    if expected_width is not None and expected_height is not None:
        if (width, height) != (expected_width, expected_height):
            raise ValueError("video resolution does not match configuration")
    if (
        probe["video_codec"] != "h264"
        or probe["audio_codec"] != "aac"
        or probe["pixel_format"] != "yuv420p"
    ):
        raise ValueError("output must contain H.264 video, AAC audio, and yuv420p pixels")
    command = [get_ffmpeg_exe(), "-v", "error", "-i", str(path), "-f", "null", "-"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    if completed.returncode:
        raise ValueError("ffmpeg decode validation failed")
    with path.open("rb") as stream:
        atoms = stream.read(2_000_000)
    moov, mdat = atoms.find(b"moov"), atoms.find(b"mdat")
    if moov < 0 or mdat < 0 or moov > mdat:
        raise ValueError("MP4 is not fast-start optimized")
    silence_result = subprocess.run(
        [
            get_ffmpeg_exe(),
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "silencedetect=noise=-50dB:d=2",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if silence_result.returncode:
        raise ValueError("audio silence analysis failed")
    silence = silence_result.stderr
    if any(float(value) > 2.5 for value in re.findall(r"silence_duration:\s*([0-9.]+)", silence)):
        raise ValueError("audio contains a long pause")
    return {
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "video_codec": "h264",
        "audio_codec": "aac",
        "pixel_format": "yuv420p",
        "faststart": True,
        "audio": True,
        "validation_backend": "moviepy+ffprobe+ffmpeg-decode",
    }


def validate_output(path: Path) -> dict[str, object]:
    result = _decode_validate(path)
    result["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result
