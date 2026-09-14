"""Pilot power analysis: the smallest organization-specific gain the window could detect.

The pilot supplies the variance model. A candidate effect is added to the treatment arm,
the interval is bootstrapped, and power is the fraction of trials whose interval excludes
zero. Bisection on the effect gives the minimum detectable difference.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from sphragis.measure.stats import Cluster, cluster_bootstrap, excludes_zero


def _shift(cluster: Cluster, effect: float, rng: random.Random) -> Cluster:
    lifted = tuple(
        1.0 if value < 1.0 and rng.random() < effect else value for value in cluster.treatment
    )
    return Cluster(cluster.change_id, lifted, cluster.control)


def simulate_power(
    clusters: Sequence[Cluster],
    *,
    effect: float,
    seed: int,
    trials: int = 200,
    resamples: int = 200,
) -> float:
    """Fraction of simulated studies whose interval excludes zero at this effect."""
    if not clusters:
        raise ValueError("simulate_power needs at least one pilot cluster")
    rng = random.Random(seed)
    n = len(clusters)
    detected = 0
    for trial in range(trials):
        sample = [_shift(clusters[rng.randrange(n)], effect, rng) for _ in range(n)]
        interval = cluster_bootstrap(sample, seed=seed + trial, resamples=resamples)
        detected += excludes_zero(interval)
    return detected / trials


def minimum_detectable_effect(
    clusters: Sequence[Cluster],
    *,
    seed: int,
    target_power: float = 0.8,
    trials: int = 200,
    resamples: int = 200,
    tolerance: float = 0.01,
) -> float:
    """Bisect on the effect until simulated power reaches ``target_power``."""
    low, high = 0.0, 1.0
    while high - low > tolerance:
        mid = (low + high) / 2.0
        power = simulate_power(clusters, effect=mid, seed=seed, trials=trials, resamples=resamples)
        if power >= target_power:
            high = mid
        else:
            low = mid
    return high
