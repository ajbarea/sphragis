"""Pilot power analysis: the smallest organization-specific gain the test window could detect.

The pilot supplies the variance model. A candidate lift is applied to the treatment arm, the
interval is bootstrapped, and power is the fraction of simulated studies whose interval
supports the directional claim. Bisection gives the minimum detectable difference.

Two things the design of record asks for that the first version did not deliver:

- **The planned window's size, not the pilot's.** Section 4 asks for the minimum detectable
  difference "for the test window's change count". Simulating at `len(clusters)` answered
  it for the 19-change pilot instead.
- **A difference in exact match, not a lift probability.** The simulated lift flips a
  failing example to a success with probability `lift`, so the exact-match difference it
  produces is `lift x (share of failures)`: a lift of 0.20 realised +0.146. The returned
  figure is the realised difference, because that is the unit section 5 of the Stage 1
  report states.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean

from sphragis.measure.stats import Cluster, cluster_bootstrap, paired_difference, supports_direction


def _shift(cluster: Cluster, lift: float, rng: random.Random) -> Cluster:
    lifted = tuple(
        1.0 if value < 1.0 and rng.random() < lift else value for value in cluster.treatment
    )
    return Cluster(cluster.change_id, lifted, cluster.control)


def _resolve_size(clusters: Sequence[Cluster], n_changes: int | None) -> int:
    if not clusters:
        raise ValueError("power analysis needs at least one pilot cluster")
    return len(clusters) if n_changes is None else n_changes


def realised_difference(
    clusters: Sequence[Cluster], *, lift: float, seed: int, draws: int = 200
) -> float:
    """The mean exact-match difference a lift produces on this pilot, in exact-match points."""
    rng = random.Random(seed)
    return fmean(
        paired_difference([_shift(c, lift, rng) for c in clusters]) - paired_difference(clusters)
        for _ in range(draws)
    )


def simulate_power(
    clusters: Sequence[Cluster],
    *,
    lift: float,
    seed: int,
    n_changes: int | None = None,
    trials: int = 200,
    resamples: int = 200,
) -> float:
    """Fraction of simulated studies of `n_changes` changes that support the direction."""
    n = _resolve_size(clusters, n_changes)
    rng = random.Random(seed)
    detected = 0
    for trial in range(trials):
        sample = [_shift(clusters[rng.randrange(len(clusters))], lift, rng) for _ in range(n)]
        interval = cluster_bootstrap(sample, seed=seed + trial, resamples=resamples)
        detected += supports_direction(interval)
    return detected / trials


@dataclass(frozen=True)
class DetectableDifference:
    """The answer section 5 of the Stage 1 report needs, in the unit it states."""

    difference: float
    lift: float
    n_changes: int
    reached_target: bool


def minimum_detectable_effect(
    clusters: Sequence[Cluster],
    *,
    seed: int,
    n_changes: int | None = None,
    target_power: float = 0.8,
    trials: int = 200,
    resamples: int = 200,
    tolerance: float = 0.01,
) -> DetectableDifference:
    """Bisect on the lift until simulated power reaches `target_power`.

    `reached_target` is False when even a lift of 1.0 falls short. The earlier version
    returned its initial upper bound in that case, indistinguishable from a real result.
    """
    n = _resolve_size(clusters, n_changes)
    reached = (
        simulate_power(
            clusters, lift=1.0, seed=seed, n_changes=n, trials=trials, resamples=resamples
        )
        >= target_power
    )
    low, high = 0.0, 1.0
    while reached and high - low > tolerance:
        mid = (low + high) / 2.0
        power = simulate_power(
            clusters, lift=mid, seed=seed, n_changes=n, trials=trials, resamples=resamples
        )
        if power >= target_power:
            high = mid
        else:
            low = mid
    return DetectableDifference(
        difference=realised_difference(clusters, lift=high, seed=seed),
        lift=high,
        n_changes=n,
        reached_target=reached,
    )
