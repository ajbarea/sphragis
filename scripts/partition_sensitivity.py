"""Operating characteristics of H1 over repeated partitions at the registered K, by simulation.

Each simulated study draws the test window's changes from a pilot run's own-against-sibling
clusters (sign-flip null, then a lift, as `decomposition_sensitivity.py` does), each change keeping
its project. K runs then each take the next of a random order over a pool of admissible partitions
built as the runs build them (`partition_pool`: the organization deduplicated, projects assigned
in a seeded order on training-window counts, `split_criteria` at the fixed size), and score the
changes once with seed-by-change churn and a run shift N(0, sigma_run) (`seed_runs`, one seed).
The cell is read off `partitioned_crossed_draws`, the interval the gate reads. A second set of runs
over disjoint partitions on the same changes measures reproducibility: how often two aggregations
differ by more than XI.

Every input is read from the artifact that fixed it, never typed: K from the pilot's sizing, the
test window's size from the decomposition sensitivity's projection, N and the split criteria's
reference from the admissible list. sigma_run is calibrated, not chosen: at each of four named
points on the pilot's per-run spread (its 90% lower bound, its estimate, the 90% bound K was sized
on, its 99% upper bound), the shift at which K null runs on the pilot's number of changes spread
that much. The artifact records each input's source.

Reported per point: the calibration, the null's one-sided false-positive rate at each Holm level,
the detectable effect by bisection on the lift at the per-cell power that gives H1
`--hypothesis-power` over each count of cells in `--cells`, how often a null reads bounded
below it, and the reproducibility failure rate. `partition-sensitivity-openstack-stopping.json` is
the data-dependent stopping rule this design replaced, as its provenance's commit ran it.

    uv run --no-sync --no-active python scripts/partition_sensitivity.py \\
        --placebo datasets/results/rq1-placebo-openstack-v3.json \\
        --corpus datasets/gerrit --org openstack \\
        --pilot datasets/results/partition-pilot-openstack.json \\
        --projection datasets/results/project-windows-openstack-v3.json \\
        --admissible datasets/results/admissible-partitions-openstack.json \\
        --out datasets/results/partition-sensitivity-openstack.json
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import fmean, stdev

from sphragis.corpus.cli import WINDOWS
from sphragis.corpus.halves import assign, organization_train, project_counts, split_criteria
from sphragis.corpus.halves import halves as build_halves
from sphragis.corpus.load import refined_examples
from sphragis.corpus.pipeline import run_dedup
from sphragis.experiment.decomposition import FAMILY_ALPHA, SESOI, halves
from sphragis.experiment.partitions import K_MAX, K_MIN, XI, pilot_sizing, sd_bound
from sphragis.experiment.power import _null, _shift, realised_difference, seed_runs
from sphragis.experiment.runner import to_clusters
from sphragis.measure.stats import (
    Cluster,
    equal_halves,
    partitioned_crossed_draws,
    percentile_interval,
)
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--placebo", type=Path, required=True, help="a single-seed placebo run")
parser.add_argument("--corpus", type=Path, required=True, help="the corpus root, for projects")
parser.add_argument("--org", required=True)
parser.add_argument(
    "--pilot", type=Path, required=True, help="partition_pilot's artifact: K and the run spread"
)
parser.add_argument(
    "--projection", type=Path, required=True, help="project_windows.py's artifact: test size"
)
parser.add_argument(
    "--admissible", type=Path, required=True, help="the admissible list: N and the reference"
)
# Seed-by-change churn: the share of an arm's outcomes a run redraws, calibrated against the two
# identical nulls marker-0 and sym-0 (`crossed_coverage.py --calibrate`, research log 2026-09-23).
parser.add_argument("--redraw", type=float, default=0.055)
# Power per hypothesis test: Nature's registered-report guidelines ask 0.95 or higher for every
# proposed test. H1 passes only when every admitted organization's cell does (intersection-union),
# so over k independent cells each cell needs hypothesis_power ** (1 / k).
parser.add_argument("--hypothesis-power", type=float, default=0.95)
# The admitted set is fixed at Stage 1 submission: one bound per possible count of H1 cells.
parser.add_argument("--cells", type=int, nargs="+", default=[1, 2, 3], help="H1 cells intersected")
# Monte Carlo error of a power estimate at 1,000 trials: about 0.007 near 0.95, 0.004 near 0.983.
# Trials share seeds across bisection steps, so power is compared on common random numbers.
parser.add_argument("--trials", type=int, default=1000, help="per bisection step")
# Monte Carlo error of a false-positive rate near 0.0125 at 4,000 trials: about 0.0018, a
# quarter of the nominal rate's distance to the next Holm level's.
parser.add_argument("--null-trials", type=int, default=4000, help="for the null's error rates")
# The gate's registered resample count, so the simulated interval is the one the reading uses.
parser.add_argument("--resamples", type=int, default=10_000, help="bootstrap draws per trial")
# Bisection on the lift over [0, LIFT_CEILING]: ten halvings resolve it to 0.3 / 1,024, about
# 0.0003, below the lift change that moves power by the 0.007 Monte Carlo error.
parser.add_argument("--steps", type=int, default=10)
# Studies per point of the sigma_run calibration: one study's spread over K = 24 runs has a
# standard error of about s / sqrt(2 (K - 1)), 0.0026 at 0.0177, so the mean of 100 is good to
# about 0.0003. Ten bisection steps over [0, CALIBRATION_CEILING] resolve sigma_run to 0.00006.
parser.add_argument("--calibration-trials", type=int, default=100)
parser.add_argument("--calibration-steps", type=int, default=10)
# Any fixed value, fixed so the simulation reproduces.
parser.add_argument("--seed", type=int, default=43)
parser.add_argument("--workers", type=int, default=6)
# Enough admissible partitions that two disjoint sets of K_MAX are drawn from many combinations.
parser.add_argument("--partitions", type=int, default=400, help="admissible partitions pooled")
# As `admissible_partitions.py` bounds its search: an organization that rarely qualifies is
# refused rather than searched until the allocation ends.
parser.add_argument("--max-seed", type=int, default=2_000, help="stop searching past this seed")
parser.add_argument("--out", type=Path, required=True)

Pool = list[tuple[Cluster, str]]


def pilot_pool(placebo: Path, corpus: Path, org: str) -> Pool:
    """The pilot's own-against-sibling clusters on both halves, each with its change's project."""
    results = json.loads(placebo.read_text())["results"]
    seed = json.loads(placebo.read_text())["seeds"][0]
    project_of: dict[str, set[str]] = defaultdict(set)
    for row in refined_examples(corpus, org):
        project_of[row["change_id"]].add(row["project"])
    pool: Pool = []
    first, second = halves(org)
    for window, sibling in ((first, second), (second, first)):
        own = results[f"adapter:{window}|{window}|s{seed}"]
        other = results[f"adapter:{sibling}|{window}|s{seed}"]
        for cluster in to_clusters(own, other):
            projects = project_of.get(cluster.change_id, set())
            if len(projects) != 1:
                raise SystemExit(f"change {cluster.change_id} maps to projects {projects}")
            pool.append((cluster, next(iter(projects))))
    return pool


def partition_pool(
    corpus: Path, org: str, reference: Path, size_floor: int, count: int, max_seed: int
) -> tuple[list[dict[str, int]], list[int]]:
    """The first `count` admissible partitions, built as the runs build them, and their seeds.

    The organization deduplicated once, projects assigned in each seed's order on training-window
    counts, and `split_criteria` read at the fixed size against the reference's ceiling
    (`organization_train`: after the organization's dedup a half's own removes nothing).
    """
    rows = run_dedup(refined_examples(corpus, org))[0]
    train = organization_train(rows, WINDOWS)
    ref = json.loads(reference.read_text())["halves"]
    counts = dict(project_counts(rows, WINDOWS["train"]))
    pool: list[dict[str, int]] = []
    seeds: list[int] = []
    seed = 0
    while len(pool) < count:
        seed += 1
        if seed > max_seed:
            raise SystemExit(f"{org}: {len(pool)} admissible partitions by seed {max_seed}")
        own = build_halves(rows, train, WINDOWS["train"], seed)
        if split_criteria(own, ref, size_floor=size_floor)["qualifies"]:
            pool.append(assign(counts, order_seed=seed))
            seeds.append(seed)
    return pool, seeds


_PARTITIONS: list[dict[str, int]] = []


def _init(partitions: list[dict[str, int]]) -> None:
    global _PARTITIONS
    _PARTITIONS = partitions


def _run(
    truth: Pool, side_of: dict[str, int], *, sigma_run: float, redraw: float, rng: random.Random
) -> list[list]:
    """One run: an admissible partition of the projects, scored once with its own shift."""
    (scored,) = seed_runs([c for c, _ in truth], seeds=1, sigma_b=sigma_run, redraw=redraw, rng=rng)
    run: list[list] = [[], []]
    for cluster, (_, project) in zip(scored, truth, strict=True):
        run[side_of[project]].append(cluster)
    return run


def _sequence(
    truth: Pool, order: list[int], k: int, *, sigma_run: float, redraw: float, stream: str
) -> list:
    """K runs, on the first K partitions of `order`, each scored on a stream of its own.

    A run's own stream keeps every run's shift and churn common across lifts; only within a run
    can the churn's draws diverge, where a lifted outcome changes how many are redrawn.
    """
    return [
        _run(
            truth,
            _PARTITIONS[p],
            sigma_run=sigma_run,
            redraw=redraw,
            rng=random.Random(f"{stream}-{i}"),
        )
        for i, p in enumerate(order[:k])
    ]


def trial(job: tuple) -> dict:
    pool, size, lift, sigma_run, redraw, resamples, seed, levels, k, check = job
    rng = random.Random(seed)
    truth: Pool = []
    for _ in range(size):
        cluster, project = pool[rng.randrange(len(pool))]
        # Positional ids: a change drawn twice is two clusters, as the bootstrap assumes.
        drawn = _shift(_null(cluster, rng), lift, rng)
        truth.append((Cluster(f"c{len(truth)}", drawn.treatment, drawn.control), project))
    # Two disjoint sequences of partitions: the second is the independent aggregation the
    # reproducibility check compares against.
    order = rng.sample(range(len(_PARTITIONS)), 2 * K_MAX)
    runs = _sequence(
        truth, order[:K_MAX], k, sigma_run=sigma_run, redraw=redraw, stream=f"run-{seed}"
    )
    # Its own stream, so the bootstrap never replays the draws that built the truth.
    estimate, draws = partitioned_crossed_draws(
        runs, seed=random.Random(f"bootstrap-{seed}").randrange(2**31), resamples=resamples
    )
    intervals = {c: percentile_interval(draws, c) for c in levels}
    out = {
        "estimate": estimate,
        "supported": {c: lo > 0.0 for c, (lo, _) in intervals.items()},
        "high": {c: hi for c, (_, hi) in intervals.items()},
        "absent": {c: lo > -SESOI and hi < SESOI for c, (lo, hi) in intervals.items()},
    }
    if check:
        again = _sequence(
            truth, order[K_MAX:], k, sigma_run=sigma_run, redraw=redraw, stream=f"again-{seed}"
        )
        out["disagree"] = abs(fmean(equal_halves(r) for r in again) - estimate) > XI
    return out


def spread(job: tuple) -> float:
    """The standard deviation of K null runs' estimates on one simulated set of changes."""
    pool, size, sigma_run, redraw, k, seed = job
    rng = random.Random(seed)
    truth: Pool = []
    for _ in range(size):
        cluster, project = pool[rng.randrange(len(pool))]
        drawn = _null(cluster, rng)
        truth.append((Cluster(f"c{len(truth)}", drawn.treatment, drawn.control), project))
    order = rng.sample(range(len(_PARTITIONS)), k)
    runs = _sequence(truth, order, k, sigma_run=sigma_run, redraw=redraw, stream=f"spread-{seed}")
    return stdev(equal_halves(r) for r in runs)


# Three times the largest shift the four targets have needed (about 0.02); a target it cannot
# reach is refused rather than silently capped.
CALIBRATION_CEILING = 0.06
# Calibration studies take seeds past every trial's (args.seed + t, t below the trial count).
CALIBRATION_SEED_OFFSET = 10_000
# The lift bracket's top, as `decomposition_sensitivity.py` uses: ten times the detectable lifts
# found so far (about 0.03); a target it cannot reach is refused rather than reported at the top.
LIFT_CEILING = 0.3
# Draws per realised difference, as `decomposition_sensitivity.py`, whose detectable effects
# these replace.
EFFECT_DRAWS = 300


def calibrate(executor, pool, args, target: float, size: int) -> dict:
    """The run shift sigma_run at which K null runs on `size` changes spread by `target`.

    A run's estimate already varies with its partition and with churn; sigma_run is what is
    added on top. Bisection on the mean spread over `--calibration-trials` studies; when the
    spread with no added shift already exceeds the target, sigma_run is 0 and that is recorded.
    """

    def mean_spread(sigma_run: float) -> float:
        jobs = [
            (pool, size, sigma_run, args.redraw, args.runs, args.seed + CALIBRATION_SEED_OFFSET + t)
            for t in range(args.calibration_trials)
        ]
        return fmean(executor.map(spread, jobs, chunksize=1))

    at_zero = mean_spread(0.0)
    if at_zero >= target:
        return {"target": target, "sigma_run": 0.0, "spread": at_zero, "floor": True}
    if mean_spread(CALIBRATION_CEILING) < target:
        raise SystemExit(
            f"a run spread of {target:.4f} is out of reach below sigma_run {CALIBRATION_CEILING}"
        )
    low, high = 0.0, CALIBRATION_CEILING
    for _ in range(args.calibration_steps):
        mid = (low + high) / 2
        if mean_spread(mid) < target:
            low = mid
        else:
            high = mid
    sigma_run = (low + high) / 2
    return {
        "target": target,
        "sigma_run": sigma_run,
        "spread": mean_spread(sigma_run),
        "floor": False,
        "spread_at_zero": at_zero,
    }


def _trials(executor, pool, args, lift, sigma_run, levels, check=False, count=None) -> list[dict]:
    jobs = [
        (
            pool,
            args.size,
            lift,
            sigma_run,
            args.redraw,
            args.resamples,
            args.seed + t,
            levels,
            args.runs,
            check,
        )
        for t in range(count or args.trials)
    ]
    return list(executor.map(trial, jobs, chunksize=1))


def _cached_trials(
    at_lift: dict[float, list[dict]], executor, pool, args, lift, sigma_run, levels
) -> list[dict]:
    """The trials at `lift`, every level read, simulated once per calibration point."""
    if lift not in at_lift:
        at_lift[lift] = _trials(executor, pool, args, lift, sigma_run, levels)
    return at_lift[lift]


def main() -> None:
    args = parser.parse_args()
    if min(args.cells) < 1:
        raise SystemExit("--cells counts H1 cells, at least one")
    cell_power = {str(k): args.hypothesis_power ** (1 / k) for k in sorted(set(args.cells))}
    pilot = json.loads(args.pilot.read_text())
    admissible = json.loads(args.admissible.read_text())
    projection = json.loads(args.projection.read_text())
    if projection["org"] != args.org or projection["test"] is None:
        raise SystemExit(f"{args.projection}: no test-window projection for {args.org}")
    # Whole changes, rounded down: a projection is not an observed count.
    try:
        args.runs = pilot_sizing(pilot, str(args.pilot))
    except ValueError as error:
        raise SystemExit(str(error)) from error
    args.size = int(projection["test"]["projected_changes"])
    estimates = pilot["per_run"] + pilot["runs_left_out"]
    if max(args.trials, args.null_trials) > CALIBRATION_SEED_OFFSET:
        raise SystemExit(f"trial seeds would reach the calibration's at {CALIBRATION_SEED_OFFSET}")
    if args.partitions < 2 * K_MAX:
        raise SystemExit(f"--partitions {args.partitions}: two disjoint sets of {K_MAX} are drawn")
    if not K_MIN <= args.runs <= K_MAX:
        raise SystemExit(f"the pilot's K {args.runs} lies outside [{K_MIN}, {K_MAX}]")
    # The per-run spread the simulation is calibrated to, each point named for what it is.
    targets = {
        "pilot_lower_90": sd_bound(estimates, 0.90, upper=False),
        "pilot_estimate": stdev(estimates),
        "sizing_bound_90": pilot["sizing"]["sd_upper"],
        "pilot_upper_99": sd_bound(estimates, 0.99, upper=True),
    }
    pool = pilot_pool(args.placebo, args.corpus, args.org)
    partitions, pool_seeds = partition_pool(
        args.corpus,
        args.org,
        Path(admissible["reference"]),
        admissible["size_floor"],
        args.partitions,
        args.max_seed,
    )
    # The pool is rebuilt from --corpus, so it must reproduce the committed list it extends.
    listed = admissible["admissible"]
    if pool_seeds[: len(listed)] != listed:
        raise SystemExit(f"{args.corpus} does not reproduce the admissible list {args.admissible}")
    tried = pool_seeds[-1]
    missing = {p for _, p in pool} - set(partitions[0])
    if missing:
        raise SystemExit(f"pilot changes from projects no partition assigns: {sorted(missing)}")
    # The pilot's own levels (its Holm levels, or a replication member's fixed level), so the
    # simulation reads at the levels the reading does.
    levels = pilot["levels"]
    report: dict = {
        "placebo": str(args.placebo),
        "org": args.org,
        "inputs": {
            "runs": f"{args.pilot}: sizing.runs",
            "levels": f"{args.pilot}: levels",
            "spread_targets": f"{args.pilot}: per_run and runs_left_out, chi-squared bounds",
            "calibration_changes": f"{args.pilot}: changes",
            "planned_changes": f"{args.projection}: test.projected_changes, rounded down",
            "size_floor": f"{args.admissible}: size_floor",
            "reference": f"{args.admissible}: reference",
        },
        "planned_changes": args.size,
        "pilot_changes": len(pool),
        "projects": len({p for _, p in pool}),
        "family_alpha": FAMILY_ALPHA,
        "hypothesis_power": args.hypothesis_power,
        "cell_power": cell_power,
        "sesoi": SESOI,
        "xi": XI,
        "runs": args.runs,
        "levels": levels,
        "trials": args.trials,
        "null_trials": args.null_trials,
        "resamples": args.resamples,
        "redraw": args.redraw,
        "size_floor": admissible["size_floor"],
        "partitions_pooled": len(partitions),
        "partition_seeds_tried": tried,
        "spread_targets": targets,
        "calibration_changes": pilot["changes"],
        "calibration_trials": args.calibration_trials,
        "by_target": {},
    }
    # The detectable effect as the registered sensitivity reads it: the realised difference per
    # half, averaged (`decomposition_sensitivity.py`), here over the halves of the first pooled
    # partition, which is the first admissible one.
    first = partitions[0]
    pilot_halves = [[c for c, p in pool if first[p] == side] for side in (0, 1)]
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(partitions,)) as executor:
        for label, target in targets.items():
            calibration = calibrate(executor, pool, args, target, pilot["changes"])
            sigma_run = calibration["sigma_run"]
            print(
                f"{label}: spread {target:.4f} at {pilot['changes']} changes -> sigma_run "
                f"{sigma_run:.4f} (realised {calibration['spread']:.4f})",
                flush=True,
            )
            null = _trials(
                executor, pool, args, 0.0, sigma_run, levels, True, count=args.null_trials
            )
            entry: dict = {
                "calibration": calibration,
                "null_false_positive": {c: fmean(t["supported"][c] for t in null) for c in levels},
                "null_reads_absent": {c: fmean(t["absent"][c] for t in null) for c in levels},
                "reproducibility_failure": fmean(t["disagree"] for t in null),
                "by_level": {},
            }
            # One set of trials per lift reads every level, and the bisections for every level
            # and cell count share it: their midpoints coincide until their paths part.
            at_lift: dict[float, list[dict]] = {}
            for level in levels:
                entry["by_level"][level] = {"by_cells": {}}
                for cells, target_power in cell_power.items():
                    low, high = 0.0, LIFT_CEILING
                    for _ in range(args.steps):
                        mid = (low + high) / 2
                        trials = _cached_trials(
                            at_lift, executor, pool, args, mid, sigma_run, levels
                        )
                        if fmean(t["supported"][level] for t in trials) >= target_power:
                            high = mid
                        else:
                            low = mid
                    if high == LIFT_CEILING:
                        raise SystemExit(f"{label} at {level}: power {target_power} not reached")
                    effect = fmean(
                        realised_difference(h, lift=high, seed=args.seed, draws=EFFECT_DRAWS)
                        for h in pilot_halves
                    )
                    at = {
                        "cell_power": target_power,
                        "lift": high,
                        "minimum_detectable_effect": effect,
                        "null_reads_bounded": fmean(t["high"][level] < effect for t in null),
                    }
                    entry["by_level"][level]["by_cells"][cells] = at
                    print(
                        f"{label} (sigma_run {sigma_run:.4f}) at {level}, {cells} cell(s), power "
                        f"{target_power:.4f}: detects {effect:+.4f}; null FP "
                        f"{entry['null_false_positive'][level]:.3f}, bounded "
                        f"{at['null_reads_bounded']:.3f}; reproducibility failure "
                        f"{entry['reproducibility_failure']:.3f}",
                        flush=True,
                    )
            entry["lifts_simulated"] = len(at_lift)
            report["by_target"][label] = entry
    report["provenance"] = provenance_header()
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
