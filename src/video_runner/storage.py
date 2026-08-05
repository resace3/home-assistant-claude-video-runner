from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import TypeAdapter

from .schemas import BrowserVideo

PERIODS = ("daily", "weekly")
INDEX_DIRECTORY = "indexes"
CATALOG_INDEX_FILENAME = "all.json"


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, default=str)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def render_lock(root: Path, stale_after: int = 7200) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".render.lock"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        if time.time() - path.stat().st_mtime > stale_after:
            path.unlink(missing_ok=True)
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        else:
            raise RuntimeError("another render is already active") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)


def _viewer_compatible_bundle(
    root: Path, period: str, metadata_path: Path, video: BrowserVideo
) -> bool:
    """Check the exact filesystem contract consumed by Personal Video Studio."""
    if metadata_path.is_symlink() or metadata_path.name != f"{video.id}.json":
        return False
    if video.type.value != period:
        return False
    try:
        relative_directory = metadata_path.parent.relative_to(root)
    except ValueError:
        return False
    parts = relative_directory.parts
    expected_depth = 3 if period == "daily" else 2
    if len(parts) != expected_depth or parts[0] != period:
        return False
    if len(parts[1]) != 4 or not parts[1].isdigit():
        return False
    if period == "daily" and (
        len(parts[2]) != 2 or not parts[2].isdigit() or not 1 <= int(parts[2]) <= 12
    ):
        return False
    current = root
    for part in parts:
        current /= part
        if current.is_symlink():
            return False
    filenames = (
        video.video_filename,
        video.thumbnail_filename,
        video.captions_filename,
    )
    if len(set(filenames)) != len(filenames):
        return False
    return all(
        (metadata_path.parent / filename).is_file()
        and not (metadata_path.parent / filename).is_symlink()
        for filename in filenames
    )


def rebuild_indexes(root: Path) -> dict[str, int]:
    adapter = TypeAdapter(BrowserVideo)
    entries: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for period in PERIODS:
        period_entries: list[dict[str, object]] = []
        for path in (root / period).glob("**/*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                video = adapter.validate_python(payload)
                if video.id in seen_ids or not _viewer_compatible_bundle(root, period, path, video):
                    continue
                item = video.model_dump(mode="json")
                item["relative_directory"] = str(path.parent.relative_to(root)).replace("\\", "/")
                period_entries.append(item)
                seen_ids.add(video.id)
            except (ValueError, OSError, json.JSONDecodeError):
                continue
        period_entries.sort(
            key=lambda item: (str(item["created_at"]), str(item["id"])), reverse=True
        )
        atomic_json(root / INDEX_DIRECTORY / f"{period}.json", period_entries)
        (root / INDEX_DIRECTORY / f"{period}.json").chmod(0o644)
        entries.extend(period_entries)
    entries.sort(key=lambda item: (str(item["created_at"]), str(item["id"])), reverse=True)
    atomic_json(root / INDEX_DIRECTORY / CATALOG_INDEX_FILENAME, entries)
    (root / INDEX_DIRECTORY).chmod(0o755)
    (root / INDEX_DIRECTORY / CATALOG_INDEX_FILENAME).chmod(0o644)
    return {
        "daily": sum(x["type"] == "daily" for x in entries),
        "weekly": sum(x["type"] == "weekly" for x in entries),
    }
