"""Agreement between raters, and proportions with their intervals, for the label audit.

Cohen's kappa is agreement beyond what the two raters' own label frequencies would give by
chance (Cohen 1960). Its interval here is a percentile bootstrap over items, which keeps no
normal approximation between the reading and a small sample. The proportion interval is
Wilson's score interval, which stays inside [0, 1] and holds its coverage near 0 and 1, where
the audit's rarer classes sit.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Hashable, Mapping, Sequence


def cohen_kappa(first: Sequence[Hashable], second: Sequence[Hashable]) -> float:
    """Cohen's kappa for two raters' labels of the same items, in the same order.

    1 when the raters agree on every item, 0 at chance agreement. When both raters give one
    label to every item, chance agreement is total and kappa is undefined; that returns NaN.
    """
    if len(first) != len(second):
        raise ValueError("both raters must label the same items")
    if not first:
        raise ValueError("no items")
    n = len(first)
    observed = sum(a == b for a, b in zip(first, second, strict=True)) / n
    left, right = Counter(first), Counter(second)
    expected = sum(left[k] * right[k] for k in left.keys() | right.keys()) / (n * n)
    if expected == 1:
        return math.nan
    return (observed - expected) / (1 - expected)


def kappa_interval(
    first: Sequence[Hashable],
    second: Sequence[Hashable],
    *,
    confidence: float = 0.95,
    resamples: int = 10_000,
    seed: int = 0,
) -> tuple[float, float]:
    """A percentile bootstrap interval for Cohen's kappa, resampling items."""
    rng = random.Random(seed)
    n = len(first)
    draws = []
    for _ in range(resamples):
        picks = [rng.randrange(n) for _ in range(n)]
        value = cohen_kappa([first[i] for i in picks], [second[i] for i in picks])
        if not math.isnan(value):
            draws.append(value)
    draws.sort()
    tail = (1 - confidence) / 2
    return draws[int(tail * (len(draws) - 1))], draws[int((1 - tail) * (len(draws) - 1))]


def wilson_interval(successes: int, n: int, *, z: float = 1.959963984540054) -> tuple[float, float]:
    """Wilson's score interval for a proportion; 95% by default."""
    if n == 0:
        raise ValueError("no trials")
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    # At 0 or n successes the bound on that side is exactly 0 or 1; arithmetic leaves a hair off.
    low = 0.0 if successes == 0 else max(0.0, centre - half)
    high = 1.0 if successes == n else min(1.0, centre + half)
    return low, high


def confusion(
    first: Sequence[Hashable], second: Sequence[Hashable], labels: Sequence[Hashable]
) -> Mapping[Hashable, Mapping[Hashable, int]]:
    """Counts of (first rater's label, second rater's label), every label pair present."""
    table = {a: dict.fromkeys(labels, 0) for a in labels}
    for a, b in zip(first, second, strict=True):
        table[a][b] += 1
    return table
