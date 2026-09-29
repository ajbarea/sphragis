"""Operating characteristics of H1 over repeated partitions, by simulation.

Each simulated study draws the test window's changes from a pilot run's own-against-sibling
clusters (sign-flip null, then a lift, as `decomposition_sensitivity.py` does), each change keeping
its project. Runs are then added one at a time, each on the next of a random order over a pool of
admissible partitions built as the runs build them (`partition_pool`: the organization
deduplicated, projects assigned in a seeded order on training-window counts, `split_criteria` at
the fixed size), and scores the changes once with its own run shift N(0, sigma_run) and
seed-by-change churn (`seed_runs`, one seed). Runs stop by `stopping_rule` between K_INIT and
K_MAX, and the cell is read off `partitioned_crossed_draws`, the interval the gate reads. A second
sequence over disjoint partitions on the same changes measures reproducibility: how often two
aggregations differ by more than XI.

Reported per sigma_run: the null's one-sided false-positive rate at each Holm level, the detectable
effect at `--target` power by bisection on the lift, how often a null reads bounded below it, the
stopping K, and the reproducibility failure rate.

    uv run --no-sync --no-active python scripts/partition_sensitivity.py \\
        --placebo datasets/results/rq1-placebo-openstack-v3.json \\
        --corpus datasets/gerrit --org openstack --size 1635 \\
        --reference datasets/results/split-criteria-openstack.json --size-floor 1850 \\
        --sigma-run 0.011 0.015 0.025 --out datasets/results/partition-sensitivity-openstack.json
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import fmean

from sphragis.corpus.cli import WINDOWS
from sphragis.corpus.halves import assign, project_counts, split_criteria
from sphragis.corpus.halves import halves as build_halves
from sphragis.corpus.load import refined_examples
from sphragis.corpus.pipeline import run_dedup, run_split
from sphragis.experiment.decomposition import SESOI, halves, holm_levels
from sphragis.experiment.partitions import K_INIT, K_MAX, XI, stopping_rule
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
parser.add_argument("--size", type=int, required=True, help="the test window's planned changes")
parser.add_argument("--sigma-run", type=float, nargs="+", required=True)
parser.add_argument("--redraw", type=float, default=0.055)
parser.add_argument("--target", type=float, default=0.928)
parser.add_argument("--trials", type=int, default=300, help="per bisection step")
parser.add_argument("--null-trials", type=int, default=1000, help="for the null's error rates")
parser.add_argument("--resamples", type=int, default=1000)
parser.add_argument("--steps", type=int, default=7)
parser.add_argument("--seed", type=int, default=43)
parser.add_argument("--workers", type=int, default=6)
parser.add_argument("--reference", type=Path, required=True, help="a split-criteria artifact")
parser.add_argument("--size-floor", type=int, required=True, help="the fixed training size N")
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
    truth: Pool, order: list[int], *, sigma_run: float, redraw: float, rng: random.Random
) -> list:
    """Runs added in `order` of the pooled partitions until the stopping rule fires, or K_MAX."""
    runs = []
    for position in order:
        runs.append(_run(truth, _PARTITIONS[position], sigma_run=sigma_run, redraw=redraw, rng=rng))
        if len(runs) >= K_INIT:
            rule = stopping_rule([equal_halves(r) for r in runs])
            if rule["stop"] or rule["at_cap"]:
                return runs
    raise ValueError(f"only {len(order)} partitions for a sequence that may need {K_MAX}")


def trial(job: tuple) -> dict:
    pool, size, lift, sigma_run, redraw, resamples, seed, levels, reproducibility = job
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
    runs = _sequence(truth, order[:K_MAX], sigma_run=sigma_run, redraw=redraw, rng=rng)
    # Its own stream, so the bootstrap never replays the draws that built the truth.
    estimate, draws = partitioned_crossed_draws(
        runs, seed=random.Random(f"bootstrap-{seed}").randrange(2**31), resamples=resamples
    )
    intervals = {c: percentile_interval(draws, c) for c in levels}
    out = {
        "estimate": estimate,
        "runs": len(runs),
        "supported": {c: lo > 0.0 for c, (lo, _) in intervals.items()},
        "high": {c: hi for c, (_, hi) in intervals.items()},
        "absent": {c: lo > -SESOI and hi < SESOI for c, (lo, hi) in intervals.items()},
    }
    if reproducibility:
        again = _sequence(truth, order[K_MAX:], sigma_run=sigma_run, redraw=redraw, rng=rng)
        out["disagree"] = abs(fmean(equal_halves(r) for r in again) - estimate) > XI
    return out


def _trials(
    executor, pool, args, lift, sigma_run, levels, reproducibility=False, count=None
) -> list[dict]:
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
            reproducibility,
        )
        for t in range(count or args.trials)
    ]
    return list(executor.map(trial, jobs, chunksize=1))


def main() -> None:
    args = parser.parse_args()
    pool = pilot_pool(args.placebo, args.corpus, args.org)
    partitions, tried = partition_pool(
        args.corpus, args.org, args.reference, args.size_floor, args.partitions
    )
    missing = {p for _, p in pool} - set(partitions[0])
    if missing:
        raise SystemExit(f"pilot changes from projects no partition assigns: {sorted(missing)}")
    levels = holm_levels(2)
    report: dict = {
        "placebo": str(args.placebo),
        "org": args.org,
        "planned_changes": args.size,
        "pilot_changes": len(pool),
        "projects": len({p for _, p in pool}),
        "target_marginal_power": args.target,
        "sesoi": SESOI,
        "xi": XI,
        "k_init": K_INIT,
        "k_max": K_MAX,
        "levels": levels,
        "trials": args.trials,
        "null_trials": args.null_trials,
        "resamples": args.resamples,
        "redraw": args.redraw,
        "size_floor": args.size_floor,
        "partitions_pooled": len(partitions),
        "partition_seeds_tried": tried,
        "by_sigma_run": {},
    }
    # The detectable effect as the registered sensitivity reads it: the realised difference per
    # half, averaged, here over the first pooled partition's halves.
    first = partitions[0]
    pilot_halves = [[c for c, p in pool if first[p] == side] for side in (0, 1)]
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(partitions,)) as executor:
        for sigma_run in args.sigma_run:
            null = _trials(
                executor, pool, args, 0.0, sigma_run, levels, True, count=args.null_trials
            )
            entry: dict = {
                "null_false_positive": {c: fmean(t["supported"][c] for t in null) for c in levels},
                "null_reads_absent": {c: fmean(t["absent"][c] for t in null) for c in levels},
                "runs": {
                    "mean": fmean(t["runs"] for t in null),
                    "max": max(t["runs"] for t in null),
                    "at_cap": fmean(t["runs"] >= K_MAX for t in null),
                },
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
                    f"sigma_run {sigma_run} at {level}: detects {effect:+.4f}; null FP "
                    f"{entry['null_false_positive'][level]:.3f}, bounded "
                    f"{entry['by_level'][level]['null_reads_bounded']:.3f}; runs "
                    f"{entry['runs']['mean']:.1f} (cap {entry['runs']['at_cap']:.2f}); "
                    f"reproducibility failure {entry['reproducibility_failure']:.3f}",
                    flush=True,
                )
            report["by_sigma_run"][str(sigma_run)] = entry
    report["provenance"] = provenance_header()
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
