"""Operating characteristics of H1 over repeated partitions, by simulation.

Each simulated study draws the test window's changes from a pilot run's own-against-sibling
clusters (sign-flip null, then a lift, as `decomposition_sensitivity.py` does), each change keeping
its project. Runs are then added one at a time: each run assigns the projects to two halves in a
fresh seeded order, each to the smaller half (`assign(..., order_seed=...)`'s rule), and scores the
changes once with its own run shift N(0, sigma_run) and seed-by-change churn (`seed_runs`, one
seed).
Runs stop by `stopping_rule` between K_INIT and K_MAX, and the cell is read off
`partitioned_crossed_draws`, the interval the gate reads. A second run sequence on the same changes
measures reproducibility: how often two aggregations differ by more than XI.

Reported per sigma_run: the null's one-sided false-positive rate at each Holm level, the detectable
effect at `--target` power by bisection on the lift, how often a null reads bounded below it, the
stopping K, and the reproducibility failure rate.

    uv run --no-sync --no-active python scripts/partition_sensitivity.py \\
        --placebo datasets/results/rq1-placebo-openstack-v3.json \\
        --corpus datasets/gerrit --org openstack --size 1635 \\
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

from sphragis.corpus.halves import assign
from sphragis.corpus.load import refined_examples
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


def _run(truth: Pool, *, sigma_run: float, redraw: float, rng: random.Random) -> list[list]:
    """One run: a fresh balanced partition of the projects, scored once with its own shift."""
    counts: dict[str, int] = defaultdict(int)
    for _, project in truth:
        counts[project] += 1
    side_of = assign(dict(counts), order_seed=rng.randrange(2**31))
    (scored,) = seed_runs([c for c, _ in truth], seeds=1, sigma_b=sigma_run, redraw=redraw, rng=rng)
    run: list[list] = [[], []]
    for cluster, (_, project) in zip(scored, truth, strict=True):
        run[side_of[project]].append(cluster)
    return run


def _sequence(truth: Pool, *, sigma_run: float, redraw: float, rng: random.Random) -> list:
    """Runs added until the stopping rule fires, or K_MAX."""
    runs = [_run(truth, sigma_run=sigma_run, redraw=redraw, rng=rng) for _ in range(K_INIT)]
    while True:
        rule = stopping_rule([equal_halves(r) for r in runs])
        if rule["stop"] or rule["at_cap"]:
            return runs
        runs.append(_run(truth, sigma_run=sigma_run, redraw=redraw, rng=rng))


def trial(job: tuple) -> dict:
    pool, size, lift, sigma_run, redraw, resamples, seed, levels, reproducibility = job
    rng = random.Random(seed)
    truth: Pool = []
    for _ in range(size):
        cluster, project = pool[rng.randrange(len(pool))]
        # Positional ids: a change drawn twice is two clusters, as the bootstrap assumes.
        drawn = _shift(_null(cluster, rng), lift, rng)
        truth.append((Cluster(f"c{len(truth)}", drawn.treatment, drawn.control), project))
    runs = _sequence(truth, sigma_run=sigma_run, redraw=redraw, rng=rng)
    estimate, draws = partitioned_crossed_draws(runs, seed=seed, resamples=resamples)
    intervals = {c: percentile_interval(draws, c) for c in levels}
    out = {
        "estimate": estimate,
        "runs": len(runs),
        "supported": {c: lo > 0.0 for c, (lo, _) in intervals.items()},
        "high": {c: hi for c, (_, hi) in intervals.items()},
        "absent": {c: lo > -SESOI and hi < SESOI for c, (lo, hi) in intervals.items()},
    }
    if reproducibility:
        again = _sequence(truth, sigma_run=sigma_run, redraw=redraw, rng=rng)
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
        "by_sigma_run": {},
    }
    clusters = [c for c, _ in pool]
    with ProcessPoolExecutor(args.workers) as executor:
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
                effect = realised_difference(clusters, lift=high, seed=args.seed, draws=300)
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
