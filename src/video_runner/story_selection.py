from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .friendly import friendly_display_name, semantic_category


@dataclass(frozen=True)
class MetricInsight:
    label: str
    current: str
    detail: str
    category: str
    trend: str
    score: float
    value: float | None
    baseline: float | None
    delta_percent: float | None
    series: tuple[float, ...]
    unit: str
    observations: int

    @property
    def comparison(self) -> str:
        if self.delta_percent is None:
            return self.detail
        magnitude = abs(self.delta_percent)
        if magnitude < 4:
            return "Close to your usual range"
        direction = "above" if self.delta_percent > 0 else "below"
        return f"{magnitude:.0f}% {direction} your period baseline"


@dataclass(frozen=True)
class StorySelection:
    primary: MetricInsight
    positive: MetricInsight | None
    unusual: MetricInsight | None
    comparison: tuple[MetricInsight, MetricInsight] | None
    headlines: tuple[MetricInsight, ...]
    all_metrics: tuple[MetricInsight, ...]
    sparse: bool


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def metric_from_highlight(item: dict[str, object]) -> MetricInsight:
    label = friendly_display_name(str(item.get("label", "Personal signal")))
    device_class = str(item.get("device_class", "sensor"))
    series_value = item.get("chart_values", [])
    series = (
        tuple(number for value in series_value if (number := _number(value)) is not None)
        if isinstance(series_value, list)
        else ()
    )
    delta = _number(item.get("delta_percent"))
    observations_value = item.get("observations", 0)
    observations = observations_value if isinstance(observations_value, int) else 0
    quality = min(20.0, observations / 4)
    change = min(45.0, abs(delta or 0.0))
    diversity = 12.0 if series else 0.0
    return MetricInsight(
        label=label,
        current=str(item.get("current", "Available"))[:32],
        detail=str(item.get("detail", "Current reading available"))[:120],
        category=semantic_category(label, device_class),
        trend=str(item.get("trend", "steady")),
        score=(_number(item.get("story_score")) or 0.0) + quality + change + diversity,
        value=_number(item.get("numeric_value")),
        baseline=_number(item.get("baseline")),
        delta_percent=delta,
        series=series,
        unit=str(item.get("unit", ""))[:12],
        observations=observations,
    )


def select_story(summary: dict[str, object]) -> StorySelection | None:
    raw = summary.get("highlights", [])
    if not isinstance(raw, list):
        return None
    metrics = [metric_from_highlight(item) for item in raw if isinstance(item, dict)]
    if not metrics:
        return None
    ordered = sorted(metrics, key=lambda item: (-item.score, item.label))
    primary = ordered[0]
    positive = next(
        (item for item in ordered if item.delta_percent is not None and item.delta_percent >= 4),
        None,
    )
    unusual = max(
        (item for item in ordered if item.delta_percent is not None),
        key=lambda item: abs(item.delta_percent or 0),
        default=None,
    )
    pair: tuple[MetricInsight, MetricInsight] | None = None
    for left in ordered:
        for right in ordered:
            if left != right and left.category == right.category:
                pair = (left, right)
                break
        if pair:
            break
    headlines: list[MetricInsight] = []
    categories: set[str] = set()
    for item in ordered:
        if item.category not in categories or len(headlines) < 2:
            headlines.append(item)
            categories.add(item.category)
        if len(headlines) == 3:
            break
    return StorySelection(
        primary=primary,
        positive=positive,
        unusual=unusual,
        comparison=pair,
        headlines=tuple(headlines),
        all_metrics=tuple(ordered),
        sparse=len(ordered) < 3,
    )
