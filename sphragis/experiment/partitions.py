"""H1 over repeated partitions: the half-split contrast averaged over admissible partitions.

A run is one partition of an organization's projects (`assign(..., order_seed=s)`, admitted by
`split_criteria`) at its own training seed, scored own half against sibling half on every held-out
change. The estimand is the mean over partitions and seeds of the equally weighted half-split
contrast; the interval crosses runs and changes (`partitioned_crossed_draws`), and the number of
runs is set by Ritzwoller and Romano's reproducible-aggregation rule. Design of record:
`docs/superpowers/specs/2026-09-29-repeated-partitions-design.md`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import NormalDist, variance
from typing import Any

from sphragis.experiment.decomposition import (
    SESOI,
    cell_verdict,
    project_clusters,
    within_sesoi,
)
from sphragis.measure.stats import equal_halves, partitioned_crossed_draws, percentile_interval

# Two independent aggregations agree within XI with probability about 1 - BETA (Ritzwoller and
# Romano, arXiv:2311.14204, Algorithm 1). XI is the SESOI: a disagreement smaller than the
# smallest effect of interest changes no reading.
XI = SESOI
BETA = 0.05
K_INIT = 8
K_MAX = 40
# Every run scores the organization's deduplicated held-out examples. Only the per-half
# boilerplate stage can still remove one in one partition and not another; above this share of
# examples missing from any run, the runs are refused rather than read on a shrunken set.
MAX_DROPPED_SHARE = 0.01

Results = Mapping[str, Sequence[Mapping[str, Any]]]


def stopping_rule(estimates: Sequence[float], *, xi: float = XI, beta: float = BETA) -> dict:
    """Whether the mean of these per-run estimates is (xi, beta)-reproducible yet.

    Algorithm 1: stop once the plug-in variance of the mean, s^2 / K, is at most
    0.5 * (xi / z_{1 - beta/2})^2, and never before K_INIT runs.
    """
    k = len(estimates)
    if k < 2:
        raise ValueError(f"the stopping rule needs at least two runs, got {k}")
    v_hat = variance(estimates) / k
    cv = 0.5 * (xi / NormalDist().inv_cdf(1 - beta / 2)) ** 2
    return {
        "runs": k,
        "variance_of_mean": v_hat,
        "critical_value": cv,
        "stop": k >= K_INIT and v_hat <= cv,
        "at_cap": k >= K_MAX,
    }


def _eval_ids(results: Results) -> set[str]:
    """Every example a run's adapters scored: its two halves' windows together."""
    ids = {row["id"] for arm, rows in results.items() if arm.startswith("adapter:") for row in rows}
    if not ids:
        raise ValueError("a run with no adapter arms")
    return ids


def common_runs(
    runs: Sequence[tuple[Results, int]], *, org: str, metric: str = "exact_match"
) -> tuple[list[list[list[Any]]], dict[str, int]]:
    """Each run's own-against-sibling clusters on the examples every run scored."""
    per_run = [_eval_ids(results) for results, _ in runs]
    union = set().union(*per_run)
    common = set.intersection(*per_run)
    dropped = len(union) - len(common)
    if union and dropped / len(union) > MAX_DROPPED_SHARE:
        raise ValueError(
            f"{dropped} of {len(union)} examples are missing from some run, above the "
            f"{MAX_DROPPED_SHARE:.0%} the design allows; the runs were not built from one "
            "deduplicated organization"
        )
    clusters = [
        project_clusters(
            {arm: [row for row in rows if row["id"] in common] for arm, rows in results.items()},
            org=org,
            seed=seed,
            metric=metric,
        )
        for results, seed in runs
    ]
    return clusters, {"examples": len(common), "dropped": dropped}


def h1_over_partitions(
    runs: Sequence[tuple[Results, int]],
    *,
    org: str,
    levels: Sequence[float],
    bounds: Mapping[float, float] | None,
    bootstrap_seed: int,
    resamples: int = 10_000,
    metric: str = "exact_match",
) -> dict[str, Any]:
    """One organization's H1 cell over its partition runs: estimate, intervals, verdicts."""
    clusters, examples = common_runs(runs, org=org, metric=metric)
    estimate, draws = partitioned_crossed_draws(clusters, seed=bootstrap_seed, resamples=resamples)
    per_run = [equal_halves(run) for run in clusters]
    intervals = {c: percentile_interval(draws, c) for c in levels}
    return {
        "estimate": estimate,
        "per_run": per_run,
        "stopping": stopping_rule(per_run),
        "intervals": {c: {"low": lo, "high": hi} for c, (lo, hi) in intervals.items()},
        "verdicts": {
            c: cell_verdict(lo, hi, bound=bounds.get(c) if bounds else None)
            for c, (lo, hi) in intervals.items()
        },
        "within_sesoi": {c: within_sesoi(lo, hi) for c, (lo, hi) in intervals.items()},
        **examples,
    }
