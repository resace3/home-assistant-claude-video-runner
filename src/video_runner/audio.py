from __future__ import annotations

import math
import struct
import subprocess
import wave
from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe


def procedural_ambient(
    path: Path,
    duration: float,
    cue_times: list[float],
    *,
    sample_rate: int = 24_000,
    music_enabled: bool = True,
) -> None:
    """Create a deterministic, copyright-free ambient bed with gentle insight chimes."""
    frames = int(duration * sample_rate)
    fade = max(1, int(sample_rate * 1.8))
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        for index in range(frames):
            seconds = index / sample_rate
            envelope = min(1.0, index / fade, (frames - index) / fade)
            ambient = (
                (
                    math.sin(math.tau * 110.0 * seconds) * 0.45
                    + math.sin(math.tau * 164.81 * seconds) * 0.28
                    + math.sin(math.tau * 220.0 * seconds) * 0.16
                )
                if music_enabled
                else 0.0
            )
            chime = 0.0
            for cue in cue_times:
                offset = seconds - cue
                if 0 <= offset <= 0.7:
                    chime += math.sin(math.tau * 659.25 * offset) * math.exp(-5.0 * offset)
            value = int(max(-1.0, min(1.0, ambient * 0.12 + chime * 0.18)) * envelope * 32767)
            stream.writeframesraw(struct.pack("<h", value))


def normalize_and_mix(
    source: Path,
    destination: Path,
    *,
    ambient: Path | None,
    duration: float,
) -> None:
    command = [get_ffmpeg_exe(), "-y", "-i", str(source)]
    if ambient is not None:
        command.extend(["-i", str(ambient)])
        audio_filter = (
            "[0:a]volume=1.0[voice];"
            f"[1:a]volume=0.055,afade=t=in:st=0:d=1.5,afade=t=out:st={max(0.0, duration - 2):.3f}:d=2[bed];"
            "[voice][bed]amix=inputs=2:duration=first:dropout_transition=2,"
            "loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
        )
        command.extend(
            [
                "-filter_complex",
                audio_filter,
                "-map",
                "0:v:0",
                "-map",
                "[aout]",
            ]
        )
    else:
        command.extend(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"])
    command.extend(
        [
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(destination),
        ]
    )
    completed = subprocess.run(command, capture_output=True, text=True, timeout=240, check=False)
    if completed.returncode:
        raise ValueError("EBU R128 audio production mix failed")
