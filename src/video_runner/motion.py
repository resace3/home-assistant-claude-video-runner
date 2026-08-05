from __future__ import annotations

import math


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def ease_out_cubic(value: float) -> float:
    value = clamp(value)
    return 1 - (1 - value) ** 3


def ease_in_out(value: float) -> float:
    value = clamp(value)
    return 0.5 - math.cos(value * math.pi) / 2


def pulse(seconds: float, period: float = 2.4) -> float:
    return 0.5 + 0.5 * math.sin(seconds * math.tau / period)


def lerp(start: float, end: float, amount: float) -> float:
    return start + (end - start) * clamp(amount)
