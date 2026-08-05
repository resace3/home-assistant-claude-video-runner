from __future__ import annotations

import logging
import os
import re
import statistics
from collections.abc import Mapping
from pathlib import Path

TOKEN_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|cookie|token)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\b[A-Za-z0-9_-]{40,}\b"),
)
MIN_AGGREGATE_SAMPLES = 8


def _observation_band(count: int) -> str:
    if count < 16:
        return "8-15"
    if count < 32:
        return "16-31"
    if count < 64:
        return "32-63"
    return "64+"


def _trend_band(values: list[float]) -> str:
    midpoint = max(1, len(values) // 2)
    first = statistics.median(values[:midpoint])
    second = statistics.median(values[midpoint:])
    scale = max(abs(statistics.median(values)), 1.0)
    relative_change = (second - first) / scale
    if relative_change >= 0.05:
        return "increasing"
    if relative_change <= -0.05:
        return "decreasing"
    return "steady"


def _variability_band(values: list[float]) -> str:
    quartiles = statistics.quantiles(values, n=4, method="inclusive")
    relative_iqr = (quartiles[2] - quartiles[0]) / max(abs(statistics.median(values)), 1.0)
    if relative_iqr < 0.05:
        return "low"
    if relative_iqr < 0.20:
        return "moderate"
    return "high"


def redact(value: object, sensitive_values: tuple[str, ...] = ()) -> str:
    text = str(value)
    for secret in sensitive_values:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    for pattern in TOKEN_PATTERNS:
        if pattern.groups:
            text = pattern.sub(r"\1[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


class RedactingFilter(logging.Filter):
    def __init__(self, sensitive_values: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.sensitive_values = sensitive_values

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage(), self.sensitive_values)
        record.args = ()
        return True


def configure_logging(sensitive_values: tuple[str, ...] = ()) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter(sensitive_values))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def aggregate_history(histories: Mapping[str, list[object]]) -> dict[str, object]:
    """Return categorical period summaries that cannot reconstruct source readings."""
    categories: dict[str, dict[str, str]] = {}
    for index, (_, raw_values) in enumerate(histories.items(), start=1):
        values: list[float] = []
        for raw in raw_values:
            try:
                values.append(float(str(raw)))
            except (TypeError, ValueError):
                continue
        if len(values) < MIN_AGGREGATE_SAMPLES:
            continue
        categories[f"metric_{index}"] = {
            "trend": _trend_band(values),
            "variability": _variability_band(values),
            "observations": _observation_band(len(values)),
        }
    return {"schema_version": 2, "metrics": categories}


def scrub_supervisor_environment() -> None:
    """Remove the Supervisor credential before importing or calling provider code."""
    os_token = os.environ.pop("SUPERVISOR_TOKEN", None)
    if os_token:
        del os_token
    # Replace handlers so no redaction filter retains the credential in memory.
    configure_logging(())


def validate_runtime_roots(video_root: Path, private_root: Path, *, test_mode: bool) -> None:
    if test_mode:
        return
    if video_root != Path("/share/personal_video_studio"):
        raise ValueError("production video_directory must be /share/personal_video_studio")
    if private_root != Path("/data/personal_video_studio"):
        raise ValueError("production private_data_directory must be /data/personal_video_studio")
