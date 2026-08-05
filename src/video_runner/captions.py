from __future__ import annotations

import re
from pathlib import Path


def _stamp(seconds: float) -> str:
    milliseconds = int(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{millis:03}"


def phrase_chunks(narration: str, target_words: int = 7) -> list[str]:
    words = narration.split()
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        current.append(word)
        boundary = bool(re.search(r"[;,.!?]$", word)) and len(current) >= 4
        if len(current) >= target_words or boundary:
            chunks.append(" ".join(current))
            current = []
    if current:
        if chunks and len(current) < 3:
            chunks[-1] = f"{chunks[-1]} {' '.join(current)}"
        else:
            chunks.append(" ".join(current))
    return chunks


def write_vtt(path: Path, narration: str, duration: float) -> None:
    chunks = phrase_chunks(narration)
    weights = [max(1, len(chunk.split())) for chunk in chunks]
    total = sum(weights)
    elapsed = 0.0
    lines = ["WEBVTT", ""]
    for chunk, weight in zip(chunks, weights, strict=True):
        start = elapsed
        elapsed += duration * weight / total
        lines.extend((f"{_stamp(start)} --> {_stamp(min(duration, elapsed))}", chunk, ""))
    path.write_text("\n".join(lines), encoding="utf-8")
