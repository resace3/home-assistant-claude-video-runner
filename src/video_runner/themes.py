from __future__ import annotations

from dataclasses import dataclass

Color = tuple[int, int, int]


@dataclass(frozen=True)
class Theme:
    background: Color
    surface: Color
    surface_alt: Color
    text: Color
    muted: Color
    accent: Color
    accent_alt: Color
    caution: Color


THEMES: dict[str, Theme] = {
    "sleep": Theme(
        (10, 14, 36),
        (22, 28, 58),
        (31, 37, 76),
        (246, 247, 255),
        (174, 184, 214),
        (137, 117, 255),
        (89, 202, 255),
        (242, 184, 74),
    ),
    "movement": Theme(
        (7, 25, 29),
        (14, 48, 52),
        (18, 62, 64),
        (242, 255, 253),
        (168, 211, 205),
        (57, 220, 170),
        (56, 188, 248),
        (243, 190, 83),
    ),
    "recovery": Theme(
        (31, 14, 22),
        (56, 25, 39),
        (75, 31, 48),
        (255, 247, 250),
        (222, 181, 195),
        (255, 111, 145),
        (255, 176, 111),
        (239, 187, 82),
    ),
    "focus": Theme(
        (9, 24, 43),
        (17, 45, 74),
        (22, 59, 94),
        (245, 251, 255),
        (169, 199, 222),
        (70, 164, 255),
        (79, 224, 213),
        (242, 187, 79),
    ),
    "environment": Theme(
        (15, 24, 28),
        (28, 45, 49),
        (35, 58, 61),
        (248, 253, 252),
        (186, 207, 202),
        (89, 211, 188),
        (140, 183, 255),
        (241, 184, 76),
    ),
    "routine": Theme(
        (18, 20, 30),
        (34, 38, 55),
        (45, 50, 70),
        (249, 250, 255),
        (187, 193, 211),
        (104, 190, 255),
        (130, 226, 190),
        (242, 184, 74),
    ),
}


def theme_for(category: str, override: str = "adaptive") -> Theme:
    if override == "indigo":
        return THEMES["sleep"]
    if override == "teal":
        return THEMES["movement"]
    if override == "slate":
        return THEMES["routine"]
    return THEMES.get(category, THEMES["routine"])
