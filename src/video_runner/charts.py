from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PIL import ImageDraw

from .motion import clamp, ease_out_cubic, lerp
from .themes import Theme


def sparkline(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    values: Sequence[float],
    progress: float,
    theme: Theme,
    *,
    width: int = 7,
) -> None:
    left, top, right, bottom = box
    if len(values) < 2:
        draw.line(
            (left, (top + bottom) // 2, right, (top + bottom) // 2), fill=theme.muted, width=3
        )
        return
    low, high = min(values), max(values)
    span = max(high - low, 1e-6)
    points = [
        (
            int(lerp(left, right, index / (len(values) - 1))),
            int(lerp(bottom, top, (value - low) / span)),
        )
        for index, value in enumerate(values)
    ]
    visible = max(2, min(len(points), int(ease_out_cubic(progress) * len(points)) + 1))
    draw.line(points[:visible], fill=theme.accent, width=width, joint="curve")
    for x, y in points[:visible]:
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=theme.text)


def progress_ring(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    radius: int,
    progress: float,
    theme: Theme,
) -> None:
    cx, cy = center
    bounds = (cx - radius, cy - radius, cx + radius, cy + radius)
    draw.arc(bounds, 0, 359, fill=theme.surface_alt, width=max(10, radius // 8))
    draw.arc(
        bounds,
        -90,
        -90 + int(359 * clamp(progress)),
        fill=theme.accent,
        width=max(10, radius // 8),
    )


def comparison_bars(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    labels: Sequence[str],
    values: Sequence[float],
    progress: float,
    theme: Theme,
    font: Any,
) -> None:
    left, top, right, bottom = box
    maximum = max((abs(value) for value in values), default=1.0) or 1.0
    row = max(70, (bottom - top) // max(1, len(values)))
    for index, (label, value) in enumerate(zip(labels, values, strict=False)):
        y = top + index * row
        draw.text((left, y), label[:28], font=font, fill=theme.text)
        track_top = y + int(font.size * 1.45)
        draw.rounded_rectangle(
            (left, track_top, right, track_top + 18), radius=9, fill=theme.surface_alt
        )
        amount = ease_out_cubic(clamp(progress - index * 0.12)) * abs(value) / maximum
        draw.rounded_rectangle(
            (left, track_top, int(lerp(left + 8, right, amount)), track_top + 18),
            radius=9,
            fill=theme.accent if index == 0 else theme.accent_alt,
        )


def seven_day_bars(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    values: Sequence[float],
    progress: float,
    theme: Theme,
    font: Any,
) -> None:
    left, top, right, bottom = box
    points = list(values[-7:])
    if not points:
        points = [0.45, 0.55, 0.5, 0.62, 0.58, 0.7, 0.66]
    while len(points) < 7:
        points.insert(0, points[0])
    low, high = min(points), max(points)
    span = max(high - low, 1e-6)
    gap = 14
    bar_width = (right - left - gap * 6) // 7
    labels = ("M", "T", "W", "T", "F", "S", "S")
    for index, value in enumerate(points):
        x = left + index * (bar_width + gap)
        normalized = 0.28 + 0.72 * (value - low) / span
        amount = ease_out_cubic(clamp(progress - index * 0.07))
        height = int((bottom - top - 42) * normalized * amount)
        draw.rounded_rectangle(
            (x, bottom - 32 - height, x + bar_width, bottom - 32),
            radius=max(6, bar_width // 4),
            fill=theme.accent if index == 6 else theme.accent_alt,
        )
        draw.text((x + bar_width // 3, bottom - 24), labels[index], font=font, fill=theme.muted)
