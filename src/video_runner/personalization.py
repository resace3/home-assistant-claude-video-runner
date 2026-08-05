from __future__ import annotations

import math
import re
import statistics
from collections.abc import Mapping

from .friendly import friendly_display_name
from .home_assistant import SensorSnapshot
from .security import redact

MISSING_STATES = {"", "none", "null", "unknown", "unavailable"}
PRIORITY_TERMS = {
    "step": 90,
    "sleep": 85,
    "heart": 80,
    "activity": 75,
    "distance": 70,
    "weight": 65,
    "energy": 60,
    "power": 55,
    "temperature": 50,
    "humidity": 45,
    "water": 40,
    "battery": 35,
    "illuminance": 30,
}


def _clean(value: object, limit: int) -> str:
    text = re.sub(r"\s+", " ", redact(value)).strip()
    return text[:limit].rstrip()


def _number(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _format_number(value: float) -> str:
    magnitude = abs(value)
    if magnitude >= 1000:
        return f"{value:,.0f}"
    if magnitude >= 100:
        return f"{value:.0f}"
    if magnitude >= 10:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _with_unit(value: str, unit: str) -> str:
    clean_unit = _clean(unit, 16)
    if not clean_unit:
        return value
    separator = "" if clean_unit in {"%", "°C", "°F"} else " "
    return f"{value}{separator}{clean_unit}"


def _trend(values: list[float]) -> str:
    if len(values) < 2:
        return "current reading"
    first = statistics.median(values[: max(1, len(values) // 2)])
    second = statistics.median(values[max(1, len(values) // 2) :])
    scale = max(abs(statistics.median(values)), 1.0)
    relative = (second - first) / scale
    if relative >= 0.05:
        return "trending up"
    if relative <= -0.05:
        return "trending down"
    return "holding steady"


def _sample(values: list[float], limit: int = 14) -> list[float]:
    if len(values) <= limit:
        return values
    return [values[round(index * (len(values) - 1) / (limit - 1))] for index in range(limit)]


def _priority(snapshot: SensorSnapshot, history: list[object]) -> float:
    haystack = f"{snapshot.name} {snapshot.entity_id} {snapshot.device_class}".lower()
    score = float(
        max((weight for term, weight in PRIORITY_TERMS.items() if term in haystack), default=0)
    )
    numeric = [_number(value) for value in history]
    values = [value for value in numeric if value is not None]
    if _number(snapshot.state) is not None:
        score += 25
    if values:
        score += min(20, math.log2(len(values) + 1) * 3)
        if len(values) >= 2:
            scale = max(abs(statistics.median(values)), 1.0)
            score += min(20, abs(values[-1] - values[0]) / scale * 20)
    if snapshot.entity_id.startswith("binary_sensor."):
        score += 8
    return score


def _highlight(snapshot: SensorSnapshot, history: list[object]) -> dict[str, object] | None:
    if snapshot.state.strip().lower() in MISSING_STATES:
        return None
    label = friendly_display_name(_clean(snapshot.name, 62))
    current_number = _number(snapshot.state)
    current = (
        _with_unit(_format_number(current_number), snapshot.unit)
        if current_number is not None
        else _clean(snapshot.state, 30)
    )
    numeric_history = [_number(value) for value in history]
    numeric_values = [value for value in numeric_history if value is not None]
    if numeric_values:
        baseline = statistics.median(numeric_values)
        trend = _trend(numeric_values)
        low = _with_unit(_format_number(min(numeric_values)), snapshot.unit)
        high = _with_unit(_format_number(max(numeric_values)), snapshot.unit)
        if trend == "trending up":
            detail = f"Rose across the period; ranged from {low} to {high}"
        elif trend == "trending down":
            detail = f"Eased across the period; ranged from {low} to {high}"
        else:
            detail = f"Stayed close to its usual range of {low} to {high}"
        delta_percent = (
            (current_number - baseline) / abs(baseline) * 100
            if current_number is not None and abs(baseline) > 1e-9
            else None
        )
    elif history:
        normalized = [_clean(value, 30) for value in history]
        changes = sum(
            left != right for left, right in zip(normalized, normalized[1:], strict=False)
        )
        detail = f"{changes} recorded state change{'s' if changes != 1 else ''}"
        baseline = None
        delta_percent = None
        trend = "changed" if changes else "steady"
    else:
        detail = "current state available; no recorded period history"
        baseline = None
        delta_percent = None
        trend = "current"
    return {
        "label": label,
        "current": current,
        "detail": detail,
        "device_class": _clean(snapshot.device_class or "sensor", 32),
        "observations": len(history),
        "numeric_value": current_number,
        "baseline": baseline,
        "delta_percent": round(delta_percent, 2) if delta_percent is not None else None,
        "trend": trend,
        "unit": _clean(snapshot.unit, 12),
        "chart_values": [round(value, 4) for value in _sample(numeric_values)],
    }


def build_personal_summary(
    snapshots: Mapping[str, SensorSnapshot],
    histories: Mapping[str, list[object]],
    *,
    period: str,
    max_highlights: int = 5,
) -> dict[str, object]:
    """Build an on-device summary with real display facts and no credential material."""
    candidates: list[tuple[float, str, dict[str, object]]] = []
    usable_count = 0
    for entity_id, snapshot in snapshots.items():
        history = histories.get(entity_id, [])
        highlight = _highlight(snapshot, history)
        if highlight is None:
            continue
        usable_count += 1
        score = _priority(snapshot, history)
        highlight["story_score"] = round(score, 3)
        candidates.append((score, entity_id, highlight))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    highlights = [item[2] for item in candidates[:max_highlights]]
    return {
        "schema_version": 3,
        "period": period,
        "discovered_sensor_count": len(snapshots),
        "usable_sensor_count": usable_count,
        "history_sensor_count": sum(bool(values) for values in histories.values()),
        "highlights": highlights,
    }


def external_disclosure_summary(summary: Mapping[str, object]) -> dict[str, object]:
    """Return counts only; sensor names and readings never leave the local renderer."""
    highlights = summary.get("highlights")

    def count(key: str) -> int:
        value = summary.get(key, 0)
        return value if isinstance(value, int) else 0

    return {
        "schema_version": 3,
        "period": str(summary.get("period", "")),
        "discovered_sensor_count": count("discovered_sensor_count"),
        "usable_sensor_count": count("usable_sensor_count"),
        "history_sensor_count": count("history_sensor_count"),
        "highlight_count": len(highlights) if isinstance(highlights, list) else 0,
    }


DISALLOWED_PAYLOAD_KEYS = frozenset(
    {
        "attribution",
        "context_id",
        "entity",
        "entity_id",
        "gps_accuracy",
        "last_changed",
        "last_reported",
        "last_updated",
        "latitude",
        "longitude",
        "story_score",
        "unique_id",
        "user_id",
    }
)
ENTITY_ID_PATTERN = re.compile(r"\b[a-z_]{3,32}\.[a-z0-9_]{2,64}\b")
TIMESTAMP_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\b"
)
CLOCK_PATTERN = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
COORDINATE_PATTERN = re.compile(r"-?\d{1,3}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}")


def _scrub_text(value: object) -> str:
    text = redact(value)
    text = COORDINATE_PATTERN.sub("[REMOVED]", text)
    text = TIMESTAMP_PATTERN.sub("[REMOVED]", text)
    text = CLOCK_PATTERN.sub("[REMOVED]", text)
    text = ENTITY_ID_PATTERN.sub("[REMOVED]", text)
    return re.sub(r"\s+", " ", text).strip()[:160]


def _scrub(value: object) -> object:
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _scrub(item)
            for key, item in value.items()
            if str(key).lower() not in DISALLOWED_PAYLOAD_KEYS
        }
    if isinstance(value, list | tuple):
        return [_scrub(item) for item in value]
    return _scrub_text(value)


def personal_disclosure_summary(summary: Mapping[str, object]) -> dict[str, object]:
    """Return the real highlights - names, values, trends, chart series - after scrubbing."""
    highlights = summary.get("highlights")
    scrubbed = _scrub(list(highlights) if isinstance(highlights, list) else [])

    def count(key: str) -> int:
        value = summary.get(key, 0)
        return value if isinstance(value, int) else 0

    return {
        "schema_version": 3,
        "period": str(summary.get("period", "")),
        "discovered_sensor_count": count("discovered_sensor_count"),
        "usable_sensor_count": count("usable_sensor_count"),
        "history_sensor_count": count("history_sensor_count"),
        "highlights": scrubbed,
    }


def disclosure_payload(summary: Mapping[str, object], *, story_detail: str) -> dict[str, object]:
    """Pick the storyboard request payload for the configured disclosure level.

    ``aggregate`` sends counts only (:func:`external_disclosure_summary`). It is the
    conservative mode: the model learns how many signals existed and nothing about them,
    so the resulting story is generic.

    ``personal`` sends the real friendly names, formatted values, trends and chart series
    from :func:`build_personal_summary`. This is the default because it is the only mode
    that produces a video actually about the user's life. The tradeoff is defensible
    because this payload goes to the user's own Claude Code CLI running locally under
    their own subscription - not to a third-party metered API - but it is still scrubbed:
    every string passes through :func:`redact`, and entity IDs, coordinates, raw
    timestamps, wall-clock times and internal scoring keys are stripped. Narration
    produced from it must stay qualitative, because narration can reach an external TTS
    service when ``tts.allow_external_egress`` is enabled.
    """
    if story_detail == "aggregate":
        return external_disclosure_summary(summary)
    if story_detail != "personal":
        raise ValueError("story_detail must be personal or aggregate")
    return personal_disclosure_summary(summary)
