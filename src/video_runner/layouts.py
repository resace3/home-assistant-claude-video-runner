from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageFont

from .charts import comparison_bars, progress_ring, seven_day_bars, sparkline
from .config import RenderConfig
from .motion import clamp, ease_in_out, ease_out_cubic, lerp, pulse
from .schemas import Scene
from .themes import Color, Theme, theme_for

SAFE_MARGIN_X = 56
PLAYER_SAFE_BOTTOM = 150


def font(size: int, *, bold: bool = False) -> Any:
    names = (
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for name in names:
        if Path(name).exists():
            return ImageFont.truetype(name, size=size)
    return ImageFont.load_default()


def wrap_text(
    draw: ImageDraw.ImageDraw, text: str, face: Any, width: int, max_lines: int = 3
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=face) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines


def _blend(left: Color, right: Color, amount: float) -> Color:
    return tuple(int(lerp(a, b, amount)) for a, b in zip(left, right, strict=True))  # type: ignore[return-value]


def _background(config: RenderConfig, theme: Theme, seconds: float) -> Image.Image:
    image = Image.new("RGB", (config.width, config.height), theme.background)
    draw = ImageDraw.Draw(image, "RGBA")
    for index in range(48):
        y0 = index * config.height // 48
        y1 = (index + 1) * config.height // 48 + 1
        color = _blend(theme.background, theme.surface, index / 65)
        draw.rectangle((0, y0, config.width, y1), fill=(*color, 255))
    orbit = seconds * 0.16
    cx = int(config.width * (0.2 + 0.14 * math.sin(orbit)))
    cy = int(config.height * (0.18 + 0.07 * math.cos(orbit * 0.8)))
    radius = int(config.width * 0.48)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(*theme.accent, 20))
    cx2 = int(config.width * (0.84 + 0.08 * math.cos(orbit * 0.7)))
    cy2 = int(config.height * (0.70 + 0.06 * math.sin(orbit)))
    draw.ellipse(
        (cx2 - radius, cy2 - radius, cx2 + radius, cy2 + radius), fill=(*theme.accent_alt, 16)
    )
    return image


def _panel(
    draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], theme: Theme, alpha: int = 230
) -> None:
    draw.rounded_rectangle(
        box, radius=30, fill=(*theme.surface, alpha), outline=(*theme.muted, 72), width=2
    )


def _heading(
    draw: ImageDraw.ImageDraw, scene: Scene, theme: Theme, y: int, reveal: float, width: int
) -> int:
    face = font(max(38, width // 13), bold=True)
    x = int(58 + (1 - ease_out_cubic(reveal)) * 54)
    lines = wrap_text(draw, scene.heading, face, width - 116, 2)
    for line in lines:
        draw.text((x, y), line, font=face, fill=theme.text)
        y += int(face.size * 1.12)
    draw.rounded_rectangle((x, y + 8, int(x + 145 * reveal), y + 16), radius=4, fill=theme.accent)
    return y + 42


def _caption(
    draw: ImageDraw.ImageDraw, scene: Scene, theme: Theme, config: RenderConfig, seconds: float
) -> None:
    if config.captions_style == "off" or not scene.caption:
        return
    amount = ease_out_cubic(clamp((seconds - 0.35) / 0.6))
    face = font(max(24, config.width // 28), bold=True)
    lines = wrap_text(draw, scene.caption, face, config.width - 150, 2)
    height = 42 + len(lines) * int(face.size * 1.25)
    top = config.height - 150 - height
    draw.rounded_rectangle(
        (56, top + int((1 - amount) * 24), config.width - 56, config.height - 150),
        radius=24,
        fill=(4, 7, 16, int(210 * amount)),
        outline=(*theme.accent, int(100 * amount)),
        width=2,
    )
    y = top + 20 + int((1 - amount) * 24)
    for line in lines:
        draw.text((78, y), line, font=face, fill=theme.text)
        y += int(face.size * 1.25)


def _metric_value(
    draw: ImageDraw.ImageDraw,
    payload: dict[str, object],
    theme: Theme,
    box: tuple[int, int, int, int],
    progress: float,
) -> None:
    left, top, right, bottom = box
    _panel(draw, box, theme)
    value = payload.get("numeric_value")
    baseline = payload.get("baseline")
    unit = str(payload.get("unit", ""))
    shown = str(payload.get("current", "Available"))
    if isinstance(value, int | float):
        start = float(baseline) if isinstance(baseline, int | float) else 0.0
        animated = lerp(start, float(value), ease_out_cubic(progress))
        shown = (
            f"{animated:,.0f}"
            if abs(animated) >= 100
            else f"{animated:.1f}".rstrip("0").rstrip(".")
        )
        shown = f"{shown}{'' if unit in {'%', '°C', '°F'} else ' '}{unit}".strip()
    value_face = font(70, bold=True)
    label_face = font(26)
    draw.text((left + 34, top + 32), shown[:18], font=value_face, fill=theme.text)
    draw.text(
        (left + 36, bottom - 58),
        str(payload.get("comparison", "Personal baseline"))[:42],
        font=label_face,
        fill=theme.muted,
    )


def render_scene_frame(
    scene: Scene, config: RenderConfig, seconds: float, global_seconds: float
) -> NDArray[np.uint8]:
    payload = scene.visual.payload
    category = str(payload.get("category", scene.accent))
    theme = theme_for(category, config.theme)
    image = _background(config, theme, global_seconds)
    draw = ImageDraw.Draw(image, "RGBA")
    reveal = ease_out_cubic(clamp(seconds / 0.85))
    y = _heading(draw, scene, theme, 92, reveal, config.width)
    kind = scene.visual.kind
    chart_progress = clamp((seconds - 0.55) / max(1.2, scene.duration_seconds * 0.55))

    if kind == "hook":
        series_value = payload.get("series", [])
        series = (
            [float(v) for v in series_value if isinstance(v, int | float)]
            if isinstance(series_value, list)
            else []
        )
        progress_ring(draw, (config.width // 2, 590), 155, ease_out_cubic(chart_progress), theme)
        sparkline(draw, (100, 780, config.width - 100, 940), series, chart_progress, theme, width=8)
        badge = str(payload.get("badge", "Your clearest signal"))
        badge_face = font(25, bold=True)
        draw.rounded_rectangle(
            (70, y + 10, 70 + int(340 * reveal), y + 66), radius=28, fill=(*theme.accent, 48)
        )
        draw.text((96, y + 24), badge[:32], font=badge_face, fill=theme.accent)
    elif kind == "metric_grid":
        cards = payload.get("cards", [])
        if isinstance(cards, list):
            card_height = 205
            for index, card in enumerate(cards[:3]):
                if not isinstance(card, dict):
                    continue
                top = (
                    y
                    + index * (card_height + 22)
                    + int((1 - ease_out_cubic(clamp(chart_progress - index * 0.12))) * 50)
                )
                box = (56, top, config.width - 56, top + card_height)
                _panel(draw, box, theme)
                label_face = font(27, bold=True)
                value_face = font(50, bold=True)
                draw.text(
                    (84, top + 30),
                    str(card.get("label", "Signal"))[:28],
                    font=label_face,
                    fill=theme.muted,
                )
                draw.text(
                    (84, top + 78),
                    str(card.get("current", "Available"))[:18],
                    font=value_face,
                    fill=theme.text,
                )
                delta = str(card.get("comparison", "Personal range"))
                draw.text((84, top + 145), delta[:44], font=font(23), fill=theme.accent_alt)
    elif kind == "progress_ring":
        box = (56, y + 16, config.width - 56, 930)
        _panel(draw, box, theme)
        progress_ring(
            draw, (config.width // 2, y + 300), 170, ease_out_cubic(chart_progress), theme
        )
        current = str(payload.get("current", "Available"))
        value_face = font(58, bold=True)
        value_width = draw.textlength(current, font=value_face)
        draw.text(
            ((config.width - value_width) / 2, y + 260), current, font=value_face, fill=theme.text
        )
        comparison = str(payload.get("comparison", scene.body))
        comp_face = font(30, bold=True)
        lines = wrap_text(draw, comparison, comp_face, config.width - 200, 3)
        comp_y = y + 540
        for line in lines:
            line_width = draw.textlength(line, font=comp_face)
            draw.text(
                ((config.width - line_width) / 2, comp_y),
                line,
                font=comp_face,
                fill=theme.accent_alt,
            )
            comp_y += int(comp_face.size * 1.3)
    elif kind in {"sparkline", "chart", "timeline"}:
        box = (56, y + 8, config.width - 56, 930)
        _panel(draw, box, theme)
        _metric_value(
            draw, payload, theme, (84, y + 38, config.width - 84, y + 250), chart_progress
        )
        series_value = payload.get("series", [])
        series = (
            [float(v) for v in series_value if isinstance(v, int | float)]
            if isinstance(series_value, list)
            else []
        )
        sparkline(
            draw, (106, y + 330, config.width - 106, 850), series, chart_progress, theme, width=8
        )
    elif kind == "seven_day":
        box = (56, y + 16, config.width - 56, 930)
        _panel(draw, box, theme)
        series_value = payload.get("series", [])
        series = (
            [float(v) for v in series_value if isinstance(v, int | float)]
            if isinstance(series_value, list)
            else []
        )
        seven_day_bars(
            draw,
            (92, y + 120, config.width - 92, 820),
            series,
            chart_progress,
            theme,
            font(22, bold=True),
        )
        draw.text(
            (94, 850),
            str(payload.get("comparison", "Seven-day pattern"))[:46],
            font=font(25, bold=True),
            fill=theme.accent,
        )
    elif kind == "comparison":
        box = (56, y + 16, config.width - 56, 930)
        _panel(draw, box, theme)
        labels_value = payload.get("labels", [])
        values_value = payload.get("values", [])
        labels = [str(v) for v in labels_value] if isinstance(labels_value, list) else []
        values = (
            [float(v) for v in values_value if isinstance(v, int | float)]
            if isinstance(values_value, list)
            else []
        )
        comparison_bars(
            draw,
            (92, y + 120, config.width - 92, 820),
            labels,
            values,
            chart_progress,
            theme,
            font(27, bold=True),
        )
    elif kind in {"recommendation", "closing", "data_quality", "gradient", "icon_grid"}:
        amount = ease_in_out(clamp((seconds - 0.3) / 1.1))
        box = (68, y + 50, config.width - 68, 880)
        _panel(draw, box, theme)
        icon_y = y + 150
        radius = int(70 + 10 * pulse(global_seconds))
        draw.ellipse(
            (
                config.width // 2 - radius,
                icon_y - radius,
                config.width // 2 + radius,
                icon_y + radius,
            ),
            fill=(*theme.accent, 42),
            outline=theme.accent,
            width=7,
        )
        symbol = ">" if kind == "recommendation" else "OK"
        symbol_face = font(66 if kind == "recommendation" else 42, bold=True)
        symbol_box = draw.textbbox((0, 0), symbol, font=symbol_face)
        symbol_width = symbol_box[2] - symbol_box[0]
        draw.text(
            ((config.width - symbol_width) // 2, icon_y - 48),
            symbol,
            font=symbol_face,
            fill=theme.text,
        )
        body_face = font(38 if kind == "closing" else 34, bold=True)
        lines = wrap_text(draw, scene.body, body_face, config.width - 220, 4)
        body_y = icon_y + 145 + int((1 - amount) * 30)
        for line in lines:
            line_width = draw.textlength(line, font=body_face)
            draw.text(
                ((config.width - line_width) / 2, body_y), line, font=body_face, fill=theme.text
            )
            body_y += int(body_face.size * 1.25)
    else:
        _metric_value(draw, payload, theme, (70, y + 60, config.width - 70, 800), chart_progress)

    _caption(draw, scene, theme, config, seconds)
    notice_face = font(18)
    draw.text(
        (58, config.height - 62),
        "Private reflection · Not medical advice",
        font=notice_face,
        fill=(*theme.muted, 190),
    )
    fade_out = clamp((scene.duration_seconds - seconds) / 0.45)
    if fade_out < 1:
        overlay = Image.new("RGB", image.size, theme.background)
        image = Image.blend(overlay, image, fade_out)
    return np.asarray(image, dtype=np.uint8)
