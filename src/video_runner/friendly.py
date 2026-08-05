from __future__ import annotations

import re

FRIENDLY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sleep.*minutes.*asleep", re.I), "Time asleep"),
    (re.compile(r"sleep.*minutes.*awake", re.I), "Time awake overnight"),
    (re.compile(r"sleep.*time.*bed", re.I), "Time in bed"),
    (re.compile(r"sleep.*efficiency", re.I), "Sleep efficiency"),
    (re.compile(r"resting.*heart.*rate", re.I), "Resting heart rate"),
    (re.compile(r"steps?", re.I), "Steps"),
    (re.compile(r"distance", re.I), "Distance"),
    (re.compile(r"minutes.*very.*active", re.I), "High-intensity movement"),
    (re.compile(r"minutes.*fairly.*active", re.I), "Moderate movement"),
    (re.compile(r"minutes.*lightly.*active", re.I), "Light movement"),
    (re.compile(r"minutes.*sedentary", re.I), "Sedentary time"),
    (re.compile(r"awakenings", re.I), "Nighttime awakenings"),
    (re.compile(r"weight", re.I), "Weight"),
    (re.compile(r"water", re.I), "Water"),
    (re.compile(r"temperature", re.I), "Temperature"),
    (re.compile(r"illuminance", re.I), "Light level"),
    (re.compile(r"battery", re.I), "Battery"),
)


def friendly_display_name(value: str) -> str:
    """Translate a technical or device-prefixed label without exposing entity identifiers."""
    text = re.sub(r"[_-]+", " ", value).strip()
    for pattern, replacement in FRIENDLY_PATTERNS:
        if pattern.search(text):
            return replacement
    words = [word for word in text.split() if word.lower() not in {"sensor", "binary"}]
    return " ".join(words[-5:]).title()[:52] or "Personal signal"


def semantic_category(label: str, device_class: str) -> str:
    text = f"{label} {device_class}".lower()
    if any(term in text for term in ("sleep", "awake", "bed", "awakening")):
        return "sleep"
    if any(term in text for term in ("step", "activity", "distance", "movement", "sedentary")):
        return "movement"
    if any(term in text for term in ("heart", "pulse", "blood", "weight")):
        return "recovery"
    if any(term in text for term in ("focus", "screen", "app", "meditation")):
        return "focus"
    if any(term in text for term in ("temperature", "humidity", "light", "illuminance")):
        return "environment"
    return "routine"
