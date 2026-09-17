"""Small, dependency-free statistics used by the checks."""

from __future__ import annotations

import math
from collections.abc import Sequence

# Scales MAD to the standard deviation of a normal distribution.
MAD_SCALE = 1.4826

# Two-sided 95% z score.
Z_95 = 1.959963984540054


def median(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("median of an empty sequence")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def robust_spread(values: Sequence[float]) -> float:
    """Median absolute deviation, scaled to be comparable with a standard deviation."""
    center = median(values)
    return MAD_SCALE * median([abs(v - center) for v in values])


def wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float]:
    """Confidence interval for a proportion that stays sensible for small samples."""
    if total <= 0:
        return 0.0, 1.0
    if not 0 <= successes <= total:
        raise ValueError(f"successes={successes} outside 0..{total}")
    p = successes / total
    z2 = z * z
    denom = 1 + z2 / total
    center = (p + z2 / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total)) / denom
    # Clamp so rounding never excludes the observed share at 0 or 1.
    return max(0.0, min(p, center - half)), min(1.0, max(p, center + half))
