"""The statistics drills and stats print (docs/MEASURE-SPEC.md, section 7). Standard library only.

A rate is k of n. Its 95% interval is the Wilson score interval; the difference of two rates has
Newcombe's hybrid score interval (method 10), built from the two Wilson intervals. Below MIN_N no
rate or interval is printed. Point estimates print from integer arithmetic, so equal fractions
print equally; interval bounds print as floor and ceil of the exact values, so the printed
interval always contains the exact one. Means are never printed.
"""

import math
import statistics

Z = 1.959963984540054  # the 0.975 quantile of the standard normal
MIN_N = 5
TOO_FEW = "too few to say a rate (5 or more needed)"
TOO_FEW_TO_COMPARE = "too few to compare"
DISTINGUISHABLE = "distinguishable from noise at these numbers"
NOT_DISTINGUISHABLE = "not distinguishable from noise at these numbers"


def wilson(k: int, n: int) -> tuple[float, float]:
    """The 95% Wilson score interval for k of n, clamped to [0, 1]. n must be above 0."""
    if n <= 0 or not 0 <= k <= n:
        raise ValueError(f"no interval for {k} of {n}")
    p = k / n
    z2 = Z * Z
    center = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = (Z / (1 + z2 / n)) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    # At k = 0 and k = n the exact bound is 0 or 1; floating point lands a hair off it.
    low = 0.0 if k == 0 else max(0.0, center - half)
    high = 1.0 if k == n else min(1.0, center + half)
    return low, high


def percent(k: int, n: int) -> int:
    """k of n as a whole percent, a half rounding up: (200k + n) // (2n)."""
    return (200 * k + n) // (2 * n)


def rate_parts(k: int, n: int) -> tuple[int, int, int] | None:
    """(percent, low percent, high percent) as printed, or None below MIN_N."""
    if n < MIN_N:
        return None
    low, high = wilson(k, n)
    return percent(k, n), math.floor(100 * low), math.ceil(100 * high)


def rate_text(k: int, n: int) -> str:
    """`83% (43% to 97%)`, or the "too few" text below MIN_N."""
    parts = rate_parts(k, n)
    if parts is None:
        return TOO_FEW
    return f"{parts[0]}% ({parts[1]}% to {parts[2]}%)"


def newcombe(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float]:
    """The 95% interval of k1/n1 - k2/n2 (Newcombe's method 10)."""
    p1, p2 = k1 / n1, k2 / n2
    low1, high1 = wilson(k1, n1)
    low2, high2 = wilson(k2, n2)
    d = p1 - p2
    low = d - math.sqrt((p1 - low1) ** 2 + (high2 - p2) ** 2)
    high = d + math.sqrt((high1 - p1) ** 2 + (p2 - low2) ** 2)
    return low, high


def points(k1: int, n1: int, k2: int, n2: int) -> int:
    """The difference in whole percentage points, a half rounded away from zero, in integer
    arithmetic so that equal differences print equally."""
    num = 100 * (k1 * n2 - k2 * n1)
    den = n1 * n2
    magnitude = (2 * abs(num) + den) // (2 * den)
    return magnitude if num >= 0 else -magnitude


def difference_parts(k1: int, n1: int, k2: int, n2: int) -> tuple[int, int, int, bool] | None:
    """(points, low points, high points, distinguishable), or None when either side is below
    MIN_N."""
    if n1 < MIN_N or n2 < MIN_N:
        return None
    low, high = newcombe(k1, n1, k2, n2)
    return points(k1, n1, k2, n2), math.floor(100 * low), math.ceil(100 * high), low > 0 or high < 0


def signed(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


def difference_text(k1: int, n1: int, k2: int, n2: int) -> tuple[str | None, str]:
    """(`+12 points (-15 to +37)`, phrase), or (None, "too few to compare")."""
    parts = difference_parts(k1, n1, k2, n2)
    if parts is None:
        return None, TOO_FEW_TO_COMPARE
    pts, low, high, apart = parts
    phrase = DISTINGUISHABLE if apart else NOT_DISTINGUISHABLE
    return f"{signed(pts)} points ({signed(low)} to {signed(high)})", phrase


def median_low(values) -> int:
    """The lower median: always one of the values."""
    return statistics.median_low(values)


def drill_seconds(ms: int) -> str:
    """A drill time, elapsed_ms as seconds to one decimal: `12.4 s`."""
    t = (ms + 50) // 100
    return f"{t // 10}.{t % 10} s"


def stats_seconds(seconds: int) -> str:
    """A stats time in whole seconds: `<s> s` below 60, `<m>m<ss>s` below an hour, then
    `<h>h<mm>m`, as holds prints an age."""
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
