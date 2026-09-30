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
the detectable effect at `--target` power by bisection on the lift, how often a null reads bounded
below it, and the reproducibility failure rate. `partition-sensitivity-openstack-stopping.json` is
the data-dependent stopping rule this design replaced, as its provenance's commit ran it.

    uv run --no-sync --no-active python scripts/partition_sensitivity.py \\
        --placebo datasets/results/rq1-placebo-openstack-v3.json \\
        --corpus datasets/gerrit --org openstack \\
        --pilot datasets/results/partition-pilot-openstack.json \\
        --planned datasets/results/decomposition-sensitivity-v3.json \\
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
from sphragis.corpus.halves import assign, project_counts, split_criteria
from sphragis.corpus.halves import halves as build_halves
from sphragis.corpus.load import refined_examples
from sphragis.corpus.pipeline import run_dedup, run_split
from sphragis.experiment.decomposition import SESOI, halves, holm_levels
from sphragis.experiment.partitions import K_MAX, K_MIN, XI, sd_bound
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
    "--planned", type=Path, required=True, help="a decomposition-sensitivity artifact: test size"
)
parser.add_argument(
    "--admissible", type=Path, required=True, help="the admissible list: N and the reference"
)
# Seed-by-change churn: the share of an arm's outcomes a run redraws, calibrated against the two
# identical nulls marker-0 and sym-0 (`crossed_coverage.py --calibrate`, research log 2026-09-23).
parser.add_argument("--redraw", type=float, default=0.055)
# Marginal power per cell: 0.928 ** 3 = 0.80 joint across three independent cells.
parser.add_argument("--target", type=float, default=0.928)
# Monte Carlo error of a power estimate near 0.928 at 300 trials: about 0.015.
parser.add_argument("--trials", type=int, default=300, help="per bisection step")
# Monte Carlo error of a false-positive rate near 0.0125 at 1,000 trials: about 0.0035.
parser.add_argument("--null-trials", type=int, default=1000, help="for the null's error rates")
parser.add_argument("--resamples", type=int, default=1000, help="bootstrap draws per trial")
# Bisection on the lift over [0, 0.3]: seven halvings resolve it to 0.3 / 128, about 0.0023.
parser.add_argument("--steps", type=int, default=7)
# Trials per point of the sigma_run calibration, and its bisection steps over [0, 0.06].
parser.add_argument("--calibration-trials", type=int, default=100)
parser.add_argument("--calibration-steps", type=int, default=10)
parser.add_argument("--seed", type=int, default=43)
parser.add_argument("--workers", type=int, default=6)
# Enough admissible partitions that two disjoint sets of K_MAX are drawn from many combinations.
parser.add_argument("--partitions", type=int, default=400, help="admissible partitions pooled")
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
    corpus: Path, org: str, reference: Path, size_floor: int, count: int
) -> tuple[list[dict[str, int]], int]:
    """The first `count` admissible partitions, built as the runs build them, and seeds tried.

    The organization deduplicated once, projects assigned in each seed's order on training-window
    counts, and `split_criteria` read at the fixed size against the reference's ceiling. Each
    half's own dedup is skipped: after the organization's it removed nothing in any real run.
    """
    rows = run_dedup(refined_examples(corpus, org))[0]
    train = run_split(rows, dict(WINDOWS))[0]["train"]
    ref = json.loads(reference.read_text())["halves"]
    counts = dict(project_counts(rows, WINDOWS["train"]))
    pool: list[dict[str, int]] = []
    seed = 0
    while len(pool) < count:
        seed += 1
        own = build_halves(rows, train, WINDOWS["train"], seed)
        if split_criteria(own, ref, size_floor=size_floor)["qualifies"]:
            pool.append(assign(counts, order_seed=seed))
    return pool, seed


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
    truth: Pool, order: list[int], k: int, *, sigma_run: float, redraw: float, rng: random.Random
) -> list:
    """K runs, on the first K partitions of `order`."""
    return [
        _run(truth, _PARTITIONS[p], sigma_run=sigma_run, redraw=redraw, rng=rng) for p in order[:k]
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
    runs = _sequence(truth, order[:K_MAX], k, sigma_run=sigma_run, redraw=redraw, rng=rng)
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
        again = _sequence(truth, order[K_MAX:], k, sigma_run=sigma_run, redraw=redraw, rng=rng)
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
    runs = _sequence(truth, order, k, sigma_run=sigma_run, redraw=redraw, rng=rng)
    return stdev(equal_halves(r) for r in runs)


def calibrate(executor, pool, args, target: float, size: int) -> dict:
    """The run shift sigma_run at which K null runs on `size` changes spread by `target`.

    A run's estimate already varies with its partition and with churn; sigma_run is what is
    added on top. Bisection on the mean spread over `--calibration-trials` studies; when the
    spread with no added shift already exceeds the target, sigma_run is 0 and that is recorded.
    """

    def mean_spread(sigma_run: float) -> float:
        jobs = [
            (pool, size, sigma_run, args.redraw, args.runs, args.seed + 10_000 + t)
            for t in range(args.calibration_trials)
        ]
        return fmean(executor.map(spread, jobs, chunksize=1))

    at_zero = mean_spread(0.0)
    if at_zero >= target:
        return {"target": target, "sigma_run": 0.0, "spread": at_zero, "floor": True}
    low, high = 0.0, 0.06
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


def main() -> None:
    args = parser.parse_args()
    pilot = json.loads(args.pilot.read_text())
    admissible = json.loads(args.admissible.read_text())
    planned = json.loads(args.planned.read_text())["cells"][args.org]["planned_changes_per_half"]
    args.runs, args.size = pilot["sizing"]["runs"], sum(planned)
    estimates = pilot["per_run"] + pilot["runs_left_out"]
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
    partitions, tried = partition_pool(
        args.corpus,
        args.org,
        Path(admissible["reference"]),
        admissible["size_floor"],
        args.partitions,
    )
    missing = {p for _, p in pool} - set(partitions[0])
    if missing:
        raise SystemExit(f"pilot changes from projects no partition assigns: {sorted(missing)}")
    levels = holm_levels(2)
    report: dict = {
        "placebo": str(args.placebo),
        "org": args.org,
        "inputs": {
            "runs": f"{args.pilot}: sizing.runs",
            "spread_targets": f"{args.pilot}: per_run and runs_left_out, chi-squared bounds",
            "calibration_changes": f"{args.pilot}: changes",
            "planned_changes": f"{args.planned}: cells.{args.org}.planned_changes_per_half, summed",
            "size_floor": f"{args.admissible}: size_floor",
            "reference": f"{args.admissible}: reference",
        },
        "planned_changes": args.size,
        "pilot_changes": len(pool),
        "projects": len({p for _, p in pool}),
        "target_marginal_power": args.target,
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
    # half, averaged, here over the first pooled partition's halves.
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
            for level in levels:
                low, high = 0.0, 0.3
                for _ in range(args.steps):
                    mid = (low + high) / 2
                    power = fmean(
                        t["supported"][level]
                        for t in _trials(executor, pool, args, mid, sigma_run, [level])
                    )
                    if power >= args.target:
                        high = mid
                    else:
                        low = mid
                effect = fmean(
                    realised_difference(h, lift=high, seed=args.seed, draws=300)
                    for h in pilot_halves
                )
                entry["by_level"][level] = {
                    "lift": high,
                    "minimum_detectable_effect": effect,
                    "null_reads_bounded": fmean(t["high"][level] < effect for t in null),
                }
                print(
                    f"{label} (sigma_run {sigma_run:.4f}) at {level}: detects {effect:+.4f}; "
                    f"null FP "
                    f"{entry['null_false_positive'][level]:.3f}, bounded "
                    f"{entry['by_level'][level]['null_reads_bounded']:.3f}; "
                    f"reproducibility failure {entry['reproducibility_failure']:.3f}",
                    flush=True,
                )
            report["by_target"][label] = entry
    report["provenance"] = provenance_header()
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
