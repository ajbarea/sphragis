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


def cluster_bootstrap(
    clusters: Sequence[Cluster],
    *,
    seed: int,
    resamples: int = 10_000,
    confidence: float = 0.95,
) -> dict[str, float]:
    """Percentile interval for the paired difference, resampling whole changes."""
    if not clusters:
        raise ValueError("cluster_bootstrap needs at least one cluster")
    rng = random.Random(seed)
    n = len(clusters)
    draws = sorted(
        paired_difference([clusters[rng.randrange(n)] for _ in range(n)]) for _ in range(resamples)
    )
    tail = (1.0 - confidence) / 2.0
    low = draws[max(0, int(tail * resamples) - 1)]
    high = draws[min(resamples - 1, int((1.0 - tail) * resamples))]
    return {
        "estimate": paired_difference(clusters),
        "low": low,
        "high": high,
        "resamples": float(resamples),
    }


def excludes_zero(interval: Mapping[str, float]) -> bool:
    """True when the whole interval sits on one side of zero."""
    return interval["low"] > 0.0 or interval["high"] < 0.0


def gate_verdict(per_org: Mapping[str, Mapping[str, float]]) -> str:
    """The pre-registered RQ1 pass rule. Fixed before the experiment; do not reinterpret."""
    if len(per_org) != 2:
        raise ValueError("the RQ1 gate is defined over exactly two organizations")
    passing = sum(1 for interval in per_org.values() if excludes_zero(interval))
    if passing == 2:
        return "pass"
    if passing == 0:
        return "fail"
    return "mixed"
