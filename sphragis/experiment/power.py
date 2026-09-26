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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean, median

from sphragis.measure.stats import (
    Cluster,
    cluster_bootstrap,
    crossed_bootstrap,
    paired_difference,
    percentile_interval,
    stratified_crossed_draws,
    supports_direction,
)


def _null(cluster: Cluster, rng: random.Random) -> Cluster:
    """The cluster with its arms swapped half the time: same variance, no direction.

    Resampling the pilot as observed carried the pilot's own difference into the simulated
    study as if it were the null. On the equalized RQ1 pilot, OpenStack's observed +0.037
    made the "minimum detectable difference" at 880 changes +0.0067, a 29-fold drop from 18
    changes where sampling noise alone predicts about 7-fold; Qt's observed -0.038 would have
    inflated its figure the same way. A random arm swap per change is the sign-flip null: it
    keeps each change's paired variance and removes whichever way the pilot happened to lean.
    """
    if rng.random() < 0.5:
        return Cluster(cluster.change_id, cluster.control, cluster.treatment)
    return cluster


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
    total = 0.0
    for _ in range(draws):
        null = [_null(c, rng) for c in clusters]
        total += paired_difference([_shift(c, lift, rng) for c in null]) - paired_difference(null)
    return total / draws


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
        sample = [
            _shift(_null(clusters[rng.randrange(len(clusters))], rng), lift, rng) for _ in range(n)
        ]
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


def lift_for_effect(
    clusters: Sequence[Cluster], effect: float, *, seed: int, draws: int = 400
) -> float:
    """The lift whose realised difference on this pilot is `effect`, by bisection.

    By bisection rather than stepping: a 0.02 step overshot Qt's +0.0110 to +0.0138, a quarter
    high and across its detection threshold, which once reported the gate's power as 0.91 for an
    effect that was not the one observed.
    """
    low, high = 0.0, 1.0
    for _ in range(24):
        mid = (low + high) / 2
        if realised_difference(clusters, lift=mid, seed=seed, draws=draws) < effect:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def _nudge(cluster: Cluster, delta: float, rng: random.Random) -> Cluster:
    """Move the treatment arm's rate: raise failures with probability delta, or lower
    successes with probability -delta."""
    if delta >= 0:
        moved = tuple(1.0 if v < 1.0 and rng.random() < delta else v for v in cluster.treatment)
    else:
        moved = tuple(0.0 if v > 0.0 and rng.random() < -delta else v for v in cluster.treatment)
    return Cluster(cluster.change_id, moved, cluster.control)


def _redraw(values: tuple[float, ...], rate: float, fraction: float, rng: random.Random):
    return tuple(
        (1.0 if rng.random() < rate else 0.0) if rng.random() < fraction else v for v in values
    )


def seed_runs(
    truth: Sequence[Cluster],
    *,
    seeds: int,
    sigma_b: float,
    redraw: float,
    rng: random.Random,
) -> list[list[Cluster]]:
    """One study's changes as scored at several seeds: crossed, with churn and a seed effect.

    Every seed scores the same changes and examples. Each redraws a fraction `redraw` of each
    arm's outcomes around that change's arm rate, which is seed-by-change churn, and moves its
    treatment arm so its contrast shifts by a draw from N(0, sigma_b), the seed main effect.
    A lift raises only failures and a drop lowers only successes, so the nudge is scaled by the
    share of each the treatment arm holds.
    """
    treated = [v for c in truth for v in c.treatment]
    share = fmean(treated) if treated else 0.0
    runs = []
    for _ in range(seeds):
        shift = rng.gauss(0.0, sigma_b) if sigma_b > 0 else 0.0
        if shift >= 0:
            delta = shift / (1.0 - share) if share < 1.0 else 0.0
        else:
            delta = shift / share if share > 0.0 else 0.0
        delta = max(-1.0, min(1.0, delta))
        run = []
        for cluster in truth:
            t_rate = fmean(cluster.treatment) if cluster.treatment else 0.0
            c_rate = fmean(cluster.control) if cluster.control else 0.0
            churned = Cluster(
                cluster.change_id,
                _redraw(cluster.treatment, t_rate, redraw, rng),
                _redraw(cluster.control, c_rate, redraw, rng),
            )
            run.append(_nudge(churned, delta, rng))
        runs.append(run)
    return runs


@dataclass(frozen=True)
class SeedTrial:
    """Whether one simulated multi-seed study supports the direction, under each rule."""

    crossed: bool
    median_seed: bool
    between_seed_variance: float


def seed_trial(
    clusters: Sequence[Cluster],
    *,
    lift: float,
    n_changes: int,
    seeds: int,
    sigma_b: float,
    redraw: float,
    resamples: int,
    seed: int,
) -> SeedTrial:
    """One simulated study at `seeds` seeds, read by the crossed and the median-seed rule."""
    rng = random.Random(seed)
    truth = [
        _shift(_null(clusters[rng.randrange(len(clusters))], rng), lift, rng)
        for _ in range(n_changes)
    ]
    runs = seed_runs(truth, seeds=seeds, sigma_b=sigma_b, redraw=redraw, rng=rng)
    crossed = crossed_bootstrap(runs, seed=seed, resamples=resamples)
    intervals = [cluster_bootstrap(r, seed=seed, resamples=resamples) for r in runs]
    middle = median(i["estimate"] for i in intervals)
    binding = min(intervals, key=lambda i: abs(i["estimate"] - middle))
    estimates = [i["estimate"] for i in intervals]
    centre = fmean(estimates)
    return SeedTrial(
        crossed=supports_direction(crossed),
        median_seed=supports_direction(binding),
        between_seed_variance=sum((e - centre) ** 2 for e in estimates) / (len(estimates) - 1),
    )


@dataclass(frozen=True)
class StratifiedTrial:
    """One simulated decomposition cell: its interval at each level, read two ways."""

    supported: Mapping[float, bool]
    absent: Mapping[float, bool]
    high: Mapping[float, float]


def stratified_seed_trial(
    halves: Sequence[Sequence[Cluster]],
    *,
    lift: float,
    n_changes: Sequence[int],
    seeds: int,
    sigma_b: float,
    redraw: float,
    resamples: int,
    seed: int,
    confidences: Sequence[float],
    sesoi: float,
) -> StratifiedTrial:
    """One simulated cell of the decomposition gate: equally weighted halves, crossed seeds.

    Each half is drawn from its own pilot clusters under the sign-flip null, lifted, and scored
    at `seeds` seeds with its own seed shifts, then read off `stratified_crossed_draws`, the
    interval the gate reads. A cell is supported when the lower bound is above zero and absent
    when the whole interval sits inside the SESOI band. Seed shifts are drawn independently per
    half, where in H1 one adapter's shift enters the two halves with opposite signs; the
    coverage simulation's `--flip-second` regime is what measures that structure.
    """
    if len(halves) != len(n_changes):
        raise ValueError("one planned size per half")
    rng = random.Random(seed)
    strata = []
    for clusters, n in zip(halves, n_changes, strict=True):
        truth = [
            _shift(_null(clusters[rng.randrange(len(clusters))], rng), lift, rng) for _ in range(n)
        ]
        strata.append(seed_runs(truth, seeds=seeds, sigma_b=sigma_b, redraw=redraw, rng=rng))
    _, draws = stratified_crossed_draws({"cell": strata}, seed=seed, resamples=resamples)
    intervals = {c: percentile_interval(draws["cell"], c) for c in confidences}
    return StratifiedTrial(
        supported={c: low > 0.0 for c, (low, _) in intervals.items()},
        absent={c: -sesoi < low and high < sesoi for c, (low, high) in intervals.items()},
        high={c: high for c, (_, high) in intervals.items()},
    )
