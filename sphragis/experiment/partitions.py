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
import re
from collections.abc import Mapping, Sequence
from statistics import NormalDist, stdev, variance
from typing import Any

from sphragis.experiment.across import one_sided_p
from sphragis.experiment.cells import (
    K_COVERAGE,
    REGISTERED_SPREAD_TARGET,
    SPREAD_TARGETS,
    by_level,
    is_count,
    level_key,
    same_calibration,
)
from sphragis.experiment.decomposition import SESOI, project_clusters, read_intervals
from sphragis.experiment.neutral import source_root, source_windows
from sphragis.measure.stats import (
    equal_halves,
    one_sided_alpha,
    partitioned_crossed_draws,
    percentile_interval,
)

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
# The K coverage rule (research log 2026-10-09, committed before the grid ran): the reproducibility
# K sizes agreement between aggregations, not the interval's coverage, so K rises to the first of
# these at which a simulated null, at the bound K was sized on, passes no more often than nominal
# at every Holm level, each rate over this many null studies.
K_GRID = (10, 12, 15, 20, 25, 30, 35, 40)
COVERAGE_NULL_TRIALS = 4_000
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


def spread_targets(pilot: Mapping[str, Any]) -> dict[str, float]:
    """The per-run spreads a partition simulation is calibrated to, from a development pilot.

    Each point is named for what it is; a test read checks its simulation's against the ones
    recomputed from the pilot its K came from, so both rest on the same pilot.
    """
    estimates = list(pilot["per_run"]) + list(pilot["runs_left_out"])
    targets = {
        "pilot_lower_90": sd_bound(estimates, 0.90, upper=False),
        "pilot_estimate": stdev(estimates),
        "sizing_bound_90": pilot["sizing"]["sd_upper"],
        "pilot_upper_99": sd_bound(estimates, 0.99, upper=True),
    }
    return {name: targets[name] for name in SPREAD_TARGETS}


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
    runs = min(K_MAX, max(K_MIN, math.ceil(raw)))
    # At a clamped K the (xi, beta) agreement the formula sizes for is not what K gives: above
    # K_MAX two draws agree less often, below K_MIN more.
    clamped = "max" if math.ceil(raw) > K_MAX else "min" if math.ceil(raw) < K_MIN else None
    return {
        "pilot_runs": n,
        "sd": s,
        "sd_upper": upper,
        "confidence": confidence,
        "formula": raw,
        "runs": runs,
        "clamped": clamped,
    }


def pilot_sizing(
    artifact: Mapping[str, Any], name: str, *, org: str, require_org: bool = False
) -> int:
    """The K an organization's pilot sized: `sizing.runs` of a reading over all its runs.

    Every reading re-derives `sizing` from the runs it read, so a reading at a K taken from
    elsewhere (its `k_source` names that file) carries a `sizing` that is not the organization's
    K, and a test-window reading's would be sized on confirmatory data; both are refused, as is
    another organization's pilot. A pilot written before readings recorded their window and
    organization is a development-window reading; with `require_org` (a test-window read) it must
    name its organization, so it is regenerated first. A K coverage artifact (`k_coverage.py`)
    carries the K the coverage rule took from that pilot.
    """
    if artifact.get("kind") == K_COVERAGE:
        if "org" not in artifact:
            raise ValueError(f"{name} does not name its organization")
        if artifact["org"] != org:
            raise ValueError(f"{name} is {artifact.get('org')!r}'s K, not {org}'s")
        if not is_count(artifact.get("runs")):
            raise ValueError(f"{name}: no runs to read K from, got {artifact.get('runs')!r}")
        return artifact["runs"]
    if require_org and "org" not in artifact:
        raise ValueError(f"{name} does not name its organization; rerun it to size a test read")
    source = artifact.get("k_source", "all runs")
    if source != "all runs":
        raise ValueError(f"{name} read its K from {source!r}; K comes from the pilot itself")
    if artifact.get("window", "development") != "development":
        raise ValueError(f"{name} read the {artifact['window']} window; K comes from the pilot")
    if artifact.get("org", org) != org:
        raise ValueError(f"{name} is {artifact['org']}'s pilot, not {org}'s")
    runs = artifact.get("sizing", {}).get("runs")
    if not is_count(runs):
        raise ValueError(f"{name}: no sizing.runs to read K from, got {runs!r}")
    return runs


def coverage_runs(
    pilot: Mapping[str, Any],
    grid: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    org: str,
    name: str,
) -> dict[str, Any]:
    """K under the coverage rule: the larger of the pilot's K and the first grid K whose null holds.

    `grid` pairs each simulation's file with its artifact, at most one per K in `K_GRID` and every
    K up to the first that holds, each of `org` on the pilot's metric, calibrated on this pilot,
    at one projected size and with `COVERAGE_NULL_TRIALS` null studies; anything else is refused,
    as is a grid in which no K holds. The null's rates are read at `REGISTERED_SPREAD_TARGET` and
    each pilot level.
    """
    reproducibility = pilot_sizing(pilot, name, org=org, require_org=True)
    targets = spread_targets(pilot)
    metric = pilot.get("metric", "exact_match")
    levels = [level_key(c) for c in pilot["levels"]]
    nominal = {c: one_sided_alpha(c) for c in levels}
    rates: dict[int, dict[float, float]] = {}
    files: dict[int, str] = {}
    sizes = set()
    for file, simulation in grid:
        k = simulation.get("runs")
        if simulation.get("org") != org:
            raise ValueError(f"{file} simulates {simulation.get('org')}, not {org}")
        if simulation.get("metric", "exact_match") != metric:
            raise ValueError(
                f"{file} is on {simulation.get('metric', 'exact_match')}, not {metric}"
            )
        if not same_calibration(simulation.get("spread_targets"), targets):
            raise ValueError(f"{file} was calibrated on another pilot than {name}")
        if simulation.get("null_trials") != COVERAGE_NULL_TRIALS:
            raise ValueError(
                f"{file}: {simulation.get('null_trials')} null studies, not {COVERAGE_NULL_TRIALS}"
            )
        if k not in K_GRID or k in rates:
            raise ValueError(f"{file}: K = {k!r} is not a grid point not already read")
        sizes.add(simulation.get("planned_changes"))
        read = by_level(simulation["by_target"][REGISTERED_SPREAD_TARGET]["null_false_positive"])
        if set(read) != set(levels):
            raise ValueError(f"{file}: null rates at {sorted(read)}, the pilot reads {levels}")
        rates[k], files[k] = read, file
    if len(sizes) > 1:
        raise ValueError(
            f"the grid was simulated at different projected sizes: {sorted(sizes, key=str)}"
        )
    # The first K that holds, read in order; every K below it must have been simulated, since
    # the rule takes the smallest, and those above it decide nothing.
    holds = {k: all(read[c] <= nominal[c] for c in levels) for k, read in rates.items()}
    coverage = None
    for k in K_GRID:
        if k not in rates:
            raise ValueError(f"the grid has no simulation at K = {k}, below any K that holds")
        if holds[k]:
            coverage = k
            break
    if coverage is None:
        raise ValueError(f"no K in {K_GRID} holds the null at nominal {nominal}")
    return {
        "reproducibility_runs": reproducibility,
        "coverage_runs": coverage,
        "runs": max(reproducibility, coverage),
        "nominal": {str(c): a for c, a in nominal.items()},
        "grid": [
            {
                "runs": k,
                "file": files[k],
                "null_false_positive": {str(c): r for c, r in rates[k].items()},
                "within_nominal": holds[k],
            }
            for k in sorted(rates)
        ],
        "spread_targets": targets,
    }


def partition_run_windows(
    run: Mapping[str, Any], *, position: int, admissible: Sequence[int], train_size: int
) -> set[str]:
    """The windows of the `position`-th partition run (from 1), refused unless it is that run.

    A run must not have halted, must be trained at the list's `train_size` with training seed
    `position`, and must have been built from the `position`-th admissible partition, read from
    each half's corpus root, `corpus-partition-<org>-p<seed>[-<tags>]`.
    """
    if "halted" in run:
        raise ValueError(f"halted at {run['halted']}; the apparatus failed, not read")
    sources = [c["source"] for c in run["corpora"].values()]
    names = {str(source_root(source)) for source in sources}
    roots = {re.search(r"/corpus-partition-[^/]*?-p(\d+)(?:-[^/]*)?$", name) for name in names}
    found = {int(root.group(1)) if root else None for root in roots}
    partition = found.pop() if len(found) == 1 else None
    if run.get("train_size") != train_size:
        raise ValueError(f"trained at {run.get('train_size')}, not the list's {train_size}")
    if run["seeds"] != [position]:
        raise ValueError(f"run {position} must use training seed {position}, has {run['seeds']}")
    if partition != admissible[position - 1]:
        raise ValueError(
            f"run {position} must use admissible partition {admissible[position - 1]}, "
            f"built from {', '.join(sources)}"
        )
    # Every half's source, so a run with one half on the test window is a test-window read.
    return {source_windows(source) for source in sources}


def eval_ids(results: Results, condition: str) -> set[str]:
    """Every example a run's arms of `condition` scored: its two halves' windows together."""
    ids = {
        row["id"]
        for arm, rows in results.items()
        if arm.startswith(f"{condition}:")
        for row in rows
    }
    if not ids:
        raise ValueError(f"a run with no {condition} arms")
    return ids


def on_common_examples(
    runs: Sequence[Results], *, condition: str
) -> tuple[list[dict[str, list[Any]]], dict[str, int]]:
    """Every run's arms cut to the examples every run's `condition` arms scored.

    Refuses runs that lose more than `MAX_DROPPED_SHARE` of their examples that way.
    """
    per_run = [eval_ids(results, condition) for results in runs]
    union = set().union(*per_run)
    common = set.intersection(*per_run)
    dropped = len(union) - len(common)
    if union and dropped / len(union) > MAX_DROPPED_SHARE:
        raise ValueError(
            f"{dropped} of {len(union)} examples are missing from some run, above the "
            f"{MAX_DROPPED_SHARE:.0%} the design allows; the runs were not built from one "
            "deduplicated organization"
        )
    cut = [
        {arm: [row for row in rows if row["id"] in common] for arm, rows in results.items()}
        for results in runs
    ]
    return cut, {"examples": len(common), "dropped": dropped}


def common_runs(
    runs: Sequence[tuple[Results, int]], *, org: str, metric: str = "exact_match"
) -> tuple[list[list[list[Any]]], dict[str, int]]:
    """Each run's own-against-sibling clusters on the examples every run scored."""
    cut, examples = on_common_examples([results for results, _ in runs], condition="adapter")
    clusters = [
        project_clusters(results, org=org, seed=seed, metric=metric)
        for results, (_, seed) in zip(cut, runs, strict=True)
    ]
    return clusters, examples


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
    sesoi: float = SESOI,
) -> dict[str, Any]:
    """One organization's H1 cell over its first `runs_fixed` partition runs, in admissible order.

    `sesoi` is the metric's own: the readings and the reproducibility margin compare against it.

    Runs computed past K are reported, each on its own examples, and never enter the cell: not
    its estimate, not its interval, not the set of examples it is read on.
    """
    if runs_fixed < 2:
        raise ValueError(f"a cell needs at least two runs, got runs_fixed={runs_fixed}")
    if resamples < 2:
        raise ValueError(f"a cell needs at least two bootstrap draws, got resamples={resamples}")
    if len(runs) < runs_fixed:
        raise ValueError(f"{len(runs)} runs computed, fewer than the {runs_fixed} fixed")
    k = runs_fixed
    clusters, examples = common_runs(runs[:k], org=org, metric=metric)
    per_run = [equal_halves(run) for run in clusters]
    left_out = [equal_halves(common_runs([run], org=org, metric=metric)[0][0]) for run in runs[k:]]
    estimate, draws = partitioned_crossed_draws(clusters, seed=bootstrap_seed, resamples=resamples)
    intervals = {c: percentile_interval(draws, c) for c in levels}
    registered = by_level(bounds) if bounds else {}
    return {
        "org": org,
        "bootstrap_seed": bootstrap_seed,
        "estimate": estimate,
        "per_run": per_run,
        "runs": k,
        "changes": sum(len(half) for half in clusters[0]),
        "reproducibility": reproducibility(per_run, xi=sesoi),
        "runs_computed": len(runs),
        "runs_left_out": left_out,
        **read_intervals(intervals, registered, sesoi=sesoi),
        # The inputs of the readings across organizations, beside the pass rule.
        "p_one_sided": one_sided_p(draws),
        "bootstrap_se": stdev(draws),
        "resamples": resamples,
        # The bounds the verdicts were read under, so a gate can check them against its own.
        "bounds": {c: registered.get(level_key(c)) for c in levels},
        **examples,
    }
