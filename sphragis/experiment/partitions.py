"""H1 over repeated partitions: the half-split contrast averaged over admissible partitions.

A run is one partition of an organization's projects (`assign(..., order_seed=s)`, admitted by
`split_criteria`) at its own training seed, scored own half against sibling half on every held-out
change. The estimand is the mean over partitions and seeds of the equally weighted half-split
contrast; the interval crosses runs and changes (`partitioned_crossed_draws`). The number of runs K
is fixed before the test window is read, from the organization's development-window pilot, by
Ritzwoller and Romano's sizing formula for reproducible aggregation. Design of record:
`docs/superpowers/specs/2026-09-29-repeated-partitions-design.md`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from statistics import NormalDist, stdev, variance
from typing import Any

from sphragis.experiment.decomposition import (
    SESOI,
    cell_verdict,
    project_clusters,
    within_sesoi,
)
from sphragis.measure.stats import equal_halves, partitioned_crossed_draws, percentile_interval

# Two independent aggregations agree within XI with probability about 1 - BETA (Ritzwoller and
# Romano, arXiv:2311.14204). XI is the SESOI: a disagreement smaller than the smallest effect of
# interest changes no reading. BETA is their recommended default ("suitable for most
# applications", section 5).
XI = SESOI
BETA = 0.05
# K is sized on the one-sided 90% upper bound of the pilot's per-run spread: a pilot that
# understates the spread undersizes K with probability 0.10. A choice, registered with the rule.
SIZING_CONFIDENCE = 0.90
# Ritzwoller and Romano's least recommended burn-in (their section 5): below 10 runs the variance
# estimate is too noisy to size on.
K_MIN = 10
# A cost cap: a run took 1.44 GH200-hours on average over the 24 OpenStack pilot jobs (1.30 to
# 1.56, sacct; research log 2026-09-29), so 40 runs is about 58 GPU-hours an organization. The
# admissible list is this long.
K_MAX = 40
# Every run scores the organization's deduplicated held-out examples. Only the per-half
# boilerplate stage can still remove one in one partition and not another; above this share of
# examples missing from any run, the runs are refused rather than read on a shrunken set. A
# choice: 1% is five of the pilot's 501 examples, and no real run has dropped one.
MAX_DROPPED_SHARE = 0.01

Results = Mapping[str, Sequence[Mapping[str, Any]]]


def reproducibility(estimates: Sequence[float], *, xi: float = XI, beta: float = BETA) -> dict:
    """Whether the mean of these per-run estimates is (xi, beta)-reproducible.

    Ritzwoller and Romano's criterion: the plug-in variance of the mean, s^2 / K, at most
    0.5 * (xi / z_{1 - beta/2})^2. Reported beside a reading; it never decides K, since stopping
    when the runs happen to agree narrows the interval with them.
    """
    k = len(estimates)
    if k < 2:
        raise ValueError(f"reproducibility needs at least two runs, got {k}")
    v_hat = variance(estimates) / k
    cv = 0.5 * (xi / NormalDist().inv_cdf(1 - beta / 2)) ** 2
    return {"runs": k, "variance_of_mean": v_hat, "critical_value": cv, "holds": v_hat <= cv}


def _lower_gamma_regularized(a: float, x: float) -> float:
    """P(a, x), the regularized lower incomplete gamma function: series, else continued fraction."""
    if x <= 0:
        return 0.0
    log_front = a * math.log(x) - x - math.lgamma(a)
    if x < a + 1:
        term = total = 1.0 / a
        n = a
        while abs(term) > abs(total) * 1e-15:
            n += 1
            term *= x / n
            total += term
        return total * math.exp(log_front)
    b, c, d = x + 1 - a, 1e300, 1 / (x + 1 - a)
    h = d
    for i in range(1, 10_000):
        an = -i * (i - a)
        b += 2
        d = 1 / (an * d + b if abs(an * d + b) > 1e-300 else 1e-300)
        c = an / c + b if abs(an / c + b) > 1e-300 else 1e-300
        h *= d * c
        if abs(d * c - 1) < 1e-15:
            break
    return 1 - math.exp(log_front) * h


def chi2_quantile(p: float, df: int) -> float:
    """The p quantile of the chi-squared distribution with df degrees of freedom, 0 < p < 1."""
    if not 0.0 < p < 1.0 or df < 1:
        raise ValueError(f"chi2_quantile needs 0 < p < 1 and df >= 1, got p={p}, df={df}")
    low, high = 0.0, max(1.0, df * 10.0)
    while _lower_gamma_regularized(df / 2, high / 2) < p:
        low, high = high, high * 2
    for _ in range(200):
        mid = (low + high) / 2
        if _lower_gamma_regularized(df / 2, mid / 2) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def sd_bound(pilot: Sequence[float], confidence: float, *, upper: bool) -> float:
    """A one-sided `confidence` bound on the standard deviation of the pilot's per-run estimates.

    Chi-squared with n - 1 degrees of freedom: the upper bound divides by the 1 - confidence
    quantile, the lower by the confidence quantile.
    """
    n = len(pilot)
    if n < 2:
        raise ValueError(f"a bound needs at least two pilot runs, got {n}")
    q = chi2_quantile(1 - confidence if upper else confidence, n - 1)
    return stdev(pilot) * math.sqrt((n - 1) / q)


def runs_needed(
    pilot: Sequence[float],
    *,
    xi: float = XI,
    beta: float = BETA,
    confidence: float = SIZING_CONFIDENCE,
) -> dict:
    """K for an organization, from its development-window pilot's per-run estimates.

    Ritzwoller and Romano's sizing formula (their eq. 5.3), K = 2 v (z_{1 - beta/2} / xi)^2, with
    v the per-run variance taken at the upper `confidence` bound of the pilot's standard
    deviation (chi-squared, n - 1 degrees of freedom), clamped to [K_MIN, K_MAX].
    """
    n = len(pilot)
    if n < 2:
        raise ValueError(f"sizing needs at least two pilot runs, got {n}")
    s = stdev(pilot)
    upper = sd_bound(pilot, confidence, upper=True)
    z = NormalDist().inv_cdf(1 - beta / 2)
    raw = 2 * upper**2 * (z / xi) ** 2
    return {
        "pilot_runs": n,
        "sd": s,
        "sd_upper": upper,
        "confidence": confidence,
        "formula": raw,
        "runs": min(K_MAX, max(K_MIN, math.ceil(raw))),
    }


def sensitivity_bounds(
    sensitivity: Mapping[str, Any], target: str, levels: Sequence[float], *, cells: int
) -> dict[float, float]:
    """Each Holm level's registered bound: the detectable effect the simulation found at `target`
    for an H1 intersecting `cells` organizations' cells.

    `target` names a point on the pilot's run spread (`partition_sensitivity.py`'s
    `spread_targets`), so the bound says which spread it assumes; `cells` sets the per-cell power
    that gives H1 its registered power.
    """
    by_target = sensitivity["by_target"]
    if target not in by_target:
        raise ValueError(f"no spread target {target!r}; the artifact has {sorted(by_target)}")
    bounds = {}
    for c in levels:
        by_cells = by_target[target]["by_level"][str(c)]["by_cells"]
        if str(cells) not in by_cells:
            raise ValueError(
                f"no bound for {cells} H1 cell(s); the artifact has {sorted(by_cells)}"
            )
        bounds[c] = by_cells[str(cells)]["minimum_detectable_effect"]
    return bounds


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
    runs_fixed: int,
    levels: Sequence[float],
    bounds: Mapping[float, float] | None,
    bootstrap_seed: int,
    resamples: int = 10_000,
    metric: str = "exact_match",
) -> dict[str, Any]:
    """One organization's H1 cell over its first `runs_fixed` partition runs, in admissible order.

    Runs computed past K are reported, each on its own examples, and never enter the cell: not
    its estimate, not its interval, not the set of examples it is read on.
    """
    if runs_fixed < 2:
        raise ValueError(f"a cell needs at least two runs, got runs_fixed={runs_fixed}")
    if len(runs) < runs_fixed:
        raise ValueError(f"{len(runs)} runs computed, fewer than the {runs_fixed} fixed")
    k = runs_fixed
    clusters, examples = common_runs(runs[:k], org=org, metric=metric)
    per_run = [equal_halves(run) for run in clusters]
    left_out = [equal_halves(common_runs([run], org=org, metric=metric)[0][0]) for run in runs[k:]]
    estimate, draws = partitioned_crossed_draws(clusters, seed=bootstrap_seed, resamples=resamples)
    intervals = {c: percentile_interval(draws, c) for c in levels}
    return {
        "estimate": estimate,
        "per_run": per_run,
        "runs": k,
        "changes": sum(len(half) for half in clusters[0]),
        "reproducibility": reproducibility(per_run),
        "runs_computed": len(runs),
        "runs_left_out": left_out,
        "intervals": {c: {"low": lo, "high": hi} for c, (lo, hi) in intervals.items()},
        "verdicts": {
            c: cell_verdict(lo, hi, bound=bounds.get(c) if bounds else None)
            for c, (lo, hi) in intervals.items()
        },
        "within_sesoi": {c: within_sesoi(lo, hi) for c, (lo, hi) in intervals.items()},
        **examples,
    }
