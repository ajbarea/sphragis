"""Pairs cluster bootstrap over changes, and the RQ1 pass rule expressed as code."""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean


@dataclass(frozen=True)
class Cluster:
    """One change: its per-example outcomes under each arm, on the same test items."""

    change_id: str
    treatment: tuple[float, ...]
    control: tuple[float, ...]


def paired_difference(clusters: Sequence[Cluster]) -> float:
    """Mean treatment outcome minus mean control outcome, pooled over examples."""
    treatment = [value for cluster in clusters for value in cluster.treatment]
    control = [value for cluster in clusters for value in cluster.control]
    if not treatment or not control:
        return 0.0
    return fmean(treatment) - fmean(control)


# Measured false-positive rate under a true null, 4000 trials per point, R=4000:
# 5 changes 10.9%, 10 changes 7.4%, 19 changes 6.1%, 30 changes 5.9%, 45 changes 4.7%,
# 91 changes 4.5%, 200 changes 4.9%, against a nominal 5%. The interval is anti-conservative
# on few clusters and nominal from roughly 30. The floor below rules out the degenerate
# end -- at one cluster every resample is that cluster, so the interval has zero width and
# always excludes zero -- and the pilot's 19 changes are reported with the 6.1% noted.
MIN_CLUSTERS = 10


def percentile_ranks(resamples: int, confidence: float) -> tuple[int, int]:
    """Zero-based ranks of the percentile interval's bounds, excluding equal tails.

    Separate so the arithmetic is testable without running a bootstrap. `int(tail *
    resamples)` truncated the float error in (1.0 - 0.90) / 2.0 == 0.04999999999999999
    down to rank 499 instead of 500, which moved the lower bound without moving the upper
    and left 499 draws below the interval against 500 above. The registered 0.95 was
    unaffected; 0.90 and 0.80 were not.
    """
    excluded = round(resamples * (1.0 - confidence) / 2.0)
    return max(0, excluded - 1), min(resamples - 1, resamples - excluded)


def cluster_bootstrap(
    clusters: Sequence[Cluster],
    *,
    seed: int,
    resamples: int = 10_000,
    confidence: float = 0.95,
    min_clusters: int = MIN_CLUSTERS,
) -> dict[str, float]:
    """Percentile interval for the paired difference, resampling whole changes."""
    if len(clusters) < min_clusters:
        raise ValueError(
            f"cluster_bootstrap needs at least {min_clusters} clusters, got {len(clusters)}: "
            "below that the interval is anti-conservative, and at one cluster it has zero "
            "width and always excludes zero"
        )
    rng = random.Random(seed)
    n = len(clusters)
    draws = sorted(
        paired_difference([clusters[rng.randrange(n)] for _ in range(n)]) for _ in range(resamples)
    )
    low_rank, high_rank = percentile_ranks(resamples, confidence)
    low, high = draws[low_rank], draws[high_rank]
    return {
        "estimate": paired_difference(clusters),
        "low": low,
        "high": high,
        "clusters": float(n),
        "resamples": float(resamples),
    }


def excludes_zero(interval: Mapping[str, float]) -> bool:
    """True when the whole interval sits on one side of zero, either side.

    Reported, and deliberately NOT what the gate reads. See `supports_direction`.
    """
    return interval["low"] > 0.0 or interval["high"] < 0.0


def supports_direction(interval: Mapping[str, float]) -> bool:
    """True when the whole interval sits ABOVE zero.

    RQ1 is a directional claim: an adapter trained on an organization does better on that
    organization's held-out refinements than one trained elsewhere. An interval lying
    entirely BELOW zero is the strongest possible refutation of that claim, and
    `excludes_zero` calls it a pass -- so a study whose two organizations both showed the
    effect reversed would have been reported as supporting the hypothesis.

    Reading the lower bound of a two-sided 95% interval is a one-sided test at alpha
    0.025, so this is stricter than the two-sided rule it replaces, not a loosening. The
    Stage 1 report has to state the one-sided reading, because it sets the nominal alpha.
    """
    return interval["low"] > 0.0


def gate_verdict(per_org: Mapping[str, Mapping[str, float]]) -> str:
    """The pre-registered RQ1 pass rule. Fixed before the experiment; do not reinterpret."""
    if len(per_org) != 2:
        raise ValueError("the RQ1 gate is defined over exactly two organizations")
    passing = sum(1 for interval in per_org.values() if supports_direction(interval))
    if passing == 2:
        return "pass"
    if passing == 0:
        return "fail"
    return "mixed"
