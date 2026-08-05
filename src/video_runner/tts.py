from __future__ import annotations

import asyncio
import math
import struct
import wave
from pathlib import Path

import edge_tts

from .config import TTSConfig

VOICE_LABELS = {"Libby, British Warm": "en-GB-LibbyNeural"}


async def resolve_voice(config: TTSConfig) -> str:
    if config.provider != "edge":
        raise RuntimeError("Only the edge provider is currently implemented")
    expected_id = VOICE_LABELS.get(config.requested_voice_name)
    if expected_id is None or config.requested_voice_id != expected_id:
        raise RuntimeError(
            "The requested display label and provider voice identifier are not an approved exact mapping"
        )
    voices = await edge_tts.list_voices()
    exact = next(
        (
            v
            for v in voices
            if v.get("ShortName") == config.requested_voice_id
            and v.get("Locale") == "en-GB"
            and v.get("Gender") == "Female"
        ),
        None,
    )
    if exact:
        return str(exact["ShortName"])
    if config.allow_fallback and config.fallback_voice_name:
        fallback = next(
            (v for v in voices if v.get("ShortName") == config.fallback_voice_name), None
        )
        if fallback:
            return str(fallback["ShortName"])
    raise RuntimeError(
        f"Requested voice {config.requested_voice_name!r} is unavailable; no substitution was made"
    )


def synthesize_edge(text: str, output: Path, config: TTSConfig) -> str:
    async def run() -> str:
        voice = await resolve_voice(config)
        rate_percent = round((config.speaking_rate - 1.0) * 100)
        if rate_percent > 0:
            raise RuntimeError("Narration is never sped up to force-fit a scene")
        # Edge's +0% rate is the provider's natural 1.0x speaking rate.
        communicate = edge_tts.Communicate(text, voice=voice, rate=f"{rate_percent:+d}%")
        await communicate.save(str(output))
        return voice

    return asyncio.run(run())


def synthesize_test_tone(output: Path, duration_seconds: float = 60.0) -> str:
    sample_rate = 24_000
    amplitude = 3200
    with wave.open(str(output), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        for sample in range(int(sample_rate * duration_seconds)):
            value = int(amplitude * math.sin(2 * math.pi * 220 * sample / sample_rate))
            stream.writeframesraw(struct.pack("<h", value))
    return "synthetic-test-tone"
