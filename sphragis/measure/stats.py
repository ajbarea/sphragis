"""Pairs cluster bootstrap over changes, and the RQ1 pass rule expressed as code."""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean

Estimator = Callable[[Sequence["Cluster"]], float]


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


def change_averaged_difference(clusters: Sequence[Cluster]) -> float:
    """Each change's own difference, averaged over changes: every change counts once.

    The other estimand. `paired_difference` pools examples, so a change carrying forty
    hunks weighs forty times one carrying a single hunk, while the bootstrap resamples
    whole changes and so treats both as one draw. The statistic and its resampling unit
    disagree, which is a coherence problem rather than a matter of taste.

    Neither is obviously right. RQ1 is a claim about refinements, and a refinement is an
    example; but a large change is not forty times more interesting than a small one, and
    the measured gap between the two estimands reached 118% of a 0.08 effect at nineteen
    changes. Which one binds the gate is a Stage 1 registration, so both are computed and
    the report names the one it registered.
    """
    per_change = [
        fmean(cluster.treatment) - fmean(cluster.control)
        for cluster in clusters
        if cluster.treatment and cluster.control
    ]
    return fmean(per_change) if per_change else 0.0


ESTIMATORS: Mapping[str, Estimator] = {
    "pooled": paired_difference,
    "change_averaged": change_averaged_difference,
}


# Measured false-positive rate of this function under a true null (both arms at the same
# exact-match rate), by `scripts/interval_calibration.py` into
# `datasets/results/interval-calibration.json`: 10 changes 7.0%, 19 changes 5.7%,
# 30 changes 5.5%, 45 changes 5.9%, 91 changes 4.7%, 200 changes 4.8%, against a nominal 5%
# (standard error about 0.6 points). Anti-conservative where the clusters are few and
# nominal from about 90 upward. The rate the null is drawn at decides this: at 19 changes it
# reads 3.1% at the base arm's 0.05 accuracy and 5.7% at the matched adapter's 0.32, so the
# figure to quote is the one measured where the gate operates. The gate reads one side,
# nominal alpha 0.025, so these rates roughly halve for it. The floor rules out the
# degenerate end: at one cluster every resample is that cluster, so the interval has zero
# width and always excludes zero.
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
    estimator: Estimator = paired_difference,
) -> dict[str, float]:
    """Percentile interval for the paired difference, resampling whole changes.

    `estimator` defaults to the pooled difference, so the registered behaviour does not
    move when the other estimand is computed beside it.
    """
    if len(clusters) < min_clusters:
        raise ValueError(
            f"cluster_bootstrap needs at least {min_clusters} clusters, got {len(clusters)}: "
            "below that the interval is anti-conservative, and at one cluster it has zero "
            "width and always excludes zero"
        )
    rng = random.Random(seed)
    n = len(clusters)
    draws = sorted(
        estimator([clusters[rng.randrange(n)] for _ in range(n)]) for _ in range(resamples)
    )
    low_rank, high_rank = percentile_ranks(resamples, confidence)
    low, high = draws[low_rank], draws[high_rank]
    return {
        "estimate": estimator(clusters),
        "low": low,
        "high": high,
        "clusters": float(n),
        "resamples": float(resamples),
    }


def require_crossed(runs: Sequence[Sequence[Cluster]]) -> None:
    """Refuse seed runs that did not score the same changes on the same examples.

    The crossed bootstrap takes one set of change indices and applies it to every seed, so
    index i has to be the same change, with the same number of examples, in each run.
    Anything else would pair one seed's change with another's and average across them.
    """
    if len(runs) < 2:
        raise ValueError(
            f"a crossed bootstrap needs at least two seed runs, got {len(runs)}: with one, "
            "there is no seed variance to carry and cluster_bootstrap is the interval"
        )
    first = [(c.change_id, len(c.treatment), len(c.control)) for c in runs[0]]
    for position, run in enumerate(runs[1:], start=1):
        shape = [(c.change_id, len(c.treatment), len(c.control)) for c in run]
        if shape != first:
            raise ValueError(
                f"seed run {position} does not score the same changes and examples as run 0; "
                "a crossed bootstrap needs every seed evaluated on the identical window"
            )


def crossed_bootstrap(
    runs: Sequence[Sequence[Cluster]],
    *,
    seed: int,
    resamples: int = 10_000,
    confidence: float = 0.95,
    min_clusters: int = MIN_CLUSTERS,
    estimator: Estimator = paired_difference,
) -> dict[str, float]:
    """Percentile interval for the seed-averaged difference, resampling seeds and changes.

    `cluster_bootstrap` holds one trained pair of adapters fixed and resamples changes, so
    its interval is conditional on that training run. A seed that shifts the contrast on
    every change at once, a seed main effect, is invisible to it. Seed-by-change variation is
    not: it shows up as spread between changes, so the single-seed interval already carries it.

    Seeds and changes are crossed, not nested: every seed scores every change. Resampling
    changes and then seeds within each change would treat a seed's shift as independent per
    change and average it away. This is Owen's pigeonhole bootstrap (Ann. Appl. Stat., 2007):
    one draw of seeds and one of changes, independently with replacement, and the statistic
    over their intersection. No bootstrap is exact for crossed data (McCullagh, Bernoulli,
    2000); this one is mildly conservative, overstating the interaction term threefold.

    At S seeds the seed component's bootstrap variance carries the factor (S - 1) / S, so
    it understates seed variance at three seeds by a third. `scripts/crossed_coverage.py`
    measures what that does to coverage.

    The estimate is the mean over seeds. Under either registered estimator, the statistic on
    a resampled set of changes pooled across seeds equals the mean of the per-seed statistics,
    because every seed contributes the same examples.
    """
    require_crossed(runs)
    n = len(runs[0])
    if n < min_clusters:
        raise ValueError(
            f"crossed_bootstrap needs at least {min_clusters} clusters, got {n}: below that "
            "the interval is anti-conservative"
        )
    rng = random.Random(seed)
    s = len(runs)
    draws = []
    for _ in range(resamples):
        seeds = [rng.randrange(s) for _ in range(s)]
        changes = [rng.randrange(n) for _ in range(n)]
        draws.append(fmean(estimator([runs[k][i] for i in changes]) for k in seeds))
    draws.sort()
    low_rank, high_rank = percentile_ranks(resamples, confidence)
    return {
        "estimate": fmean(estimator(run) for run in runs),
        "low": draws[low_rank],
        "high": draws[high_rank],
        "clusters": float(n),
        "seeds": float(s),
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


Strata = Sequence[Sequence[Sequence[Cluster]]]


def stratified_crossed_draws(
    contrasts: Mapping[str, Strata],
    *,
    seed: int,
    resamples: int = 10_000,
    min_clusters: int = MIN_CLUSTERS,
    estimator: Estimator = paired_difference,
) -> tuple[dict[str, float], dict[str, list[float]]]:
    """Estimates and bootstrap draws for contrasts over equally weighted strata, on shared draws.

    Each contrast is indexed `[stratum][seed][cluster]`. A stratum is one half of an
    organization. The statistic is the mean over strata of the seed-averaged estimator, so each
    half counts once whatever its size, and changes are resampled within their own stratum so a
    replicate never moves weight between halves.

    Every contrast is evaluated on the SAME seed and change draws. Contrasts that share an arm,
    as the half-split and organization contrasts share the sibling adapter with opposite signs,
    are negatively coupled, and only shared draws give their joint distribution.
    """
    if not contrasts:
        raise ValueError("no contrasts to bootstrap")
    names = list(contrasts)
    reference = contrasts[names[0]]
    for name in names:
        strata = contrasts[name]
        if len(strata) != len(reference):
            raise ValueError(
                f"contrast {name!r} has {len(strata)} strata, expected {len(reference)}"
            )
        for position, runs in enumerate(strata):
            require_crossed(runs)
            if len(runs) != len(reference[0]):
                raise ValueError(
                    f"contrast {name!r} has a different seed count in stratum {position}; every "
                    "stratum is resampled with one draw of seeds"
                )
            shape = [(c.change_id, len(c.treatment)) for c in runs[0]]
            if shape != [(c.change_id, len(c.treatment)) for c in reference[position][0]]:
                raise ValueError(
                    f"contrast {name!r} scores different changes in stratum {position}; shared "
                    "draws need every contrast over the identical examples"
                )
            if len(runs[0]) < min_clusters:
                raise ValueError(
                    f"stratum {position} has {len(runs[0])} clusters, below the floor of "
                    f"{min_clusters}"
                )
    rng = random.Random(seed)
    s = len(reference[0])
    sizes = [len(runs[0]) for runs in reference]
    draws: dict[str, list[float]] = {name: [] for name in names}
    for _ in range(resamples):
        seeds = [rng.randrange(s) for _ in range(s)]
        changes = [[rng.randrange(n) for _ in range(n)] for n in sizes]
        for name in names:
            draws[name].append(
                fmean(
                    fmean(estimator([runs[k][i] for i in changes[h]]) for k in seeds)
                    for h, runs in enumerate(contrasts[name])
                )
            )
    estimates = {
        name: fmean(fmean(estimator(run) for run in runs) for runs in contrasts[name])
        for name in names
    }
    return estimates, draws


def percentile_interval(draws: Sequence[float], confidence: float) -> tuple[float, float]:
    """The percentile interval of a set of bootstrap draws."""
    ordered = sorted(draws)
    low_rank, high_rank = percentile_ranks(len(ordered), confidence)
    return ordered[low_rank], ordered[high_rank]
