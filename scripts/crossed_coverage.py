"""How often does each interval exclude zero when there is nothing to find?

The registered gate reads the change bootstrap of the median seed. `crossed_bootstrap`
resamples seeds and changes together. Under a null with a seed main effect of size sigma_b,
this measures each rule's one-sided false-positive rate (the lower bound above zero, which
is what the gate reads) at three and five seeds.

The simulated study is built from a real one. Changes, with their example counts and
per-change exact-match rates, are drawn with replacement from a calibration run's null
arm, so cluster sizes and difficulty are the real ones. Each example has a stable outcome
per arm: shared between the two arms, or with probability `q` drawn separately for each,
which is what gives the contrast its between-change spread. Each seed then redraws a
fraction `f` of each arm's outcomes around the change's rate shifted by a seed-and-arm
effect, which is seed-by-change churn plus, when sigma_b > 0, a shift common to every
change. `--calibrate` prints the single-seed standard error, the run-to-run standard error
and the churn the chosen q and f produce, to compare with the two identical nulls
(marker-0 and sym-0).

    uv run --no-sync --no-active python scripts/crossed_coverage.py --calibrate
    uv run --no-sync --no-active python scripts/crossed_coverage.py --trials 1000 \
        --out datasets/results/crossed-coverage.json
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import fmean, median, pstdev

from sphragis.experiment.model import run_provenance
from sphragis.measure.stats import (
    Cluster,
    cluster_bootstrap,
    crossed_bootstrap,
    paired_difference,
    supports_direction,
)

Population = list[tuple[str, int, float]]

parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path, default=Path("datasets/results/calibration-sym-0.json"))
parser.add_argument("--arm", default="adapter:a|a|s1", help="the null arm changes are drawn from")
parser.add_argument("--q", type=float, default=0.35, help="P(an example's arms differ stably)")
parser.add_argument("--f", type=float, default=0.055, help="P(a seed redraws an arm's outcome)")
parser.add_argument("--seeds", type=int, nargs="+", default=[3, 5])
parser.add_argument("--sigma-b", type=float, nargs="+", default=[0.0, 0.005, 0.01, 0.02])
parser.add_argument("--trials", type=int, default=1000)
parser.add_argument("--resamples", type=int, default=2000)
parser.add_argument("--workers", type=int, default=6)
parser.add_argument("--calibrate", action="store_true")
parser.add_argument("--out", type=Path)


def population(source: Path, arm: str) -> Population:
    """(change id, examples, exact-match rate) for every change the arm scored."""
    rows = json.loads(source.read_text())["results"][arm]
    by_change: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_change[row["change_id"]].append(float(row["exact_match"]))
    return [(c, len(v), fmean(v)) for c, v in sorted(by_change.items())]


def _bernoulli(rng: random.Random, p: float) -> float:
    return 1.0 if rng.random() < min(1.0, max(0.0, p)) else 0.0


def simulate(
    rng: random.Random, pop: Population, *, seeds: int, q: float, f: float, sigma_b: float
) -> list[list[Cluster]]:
    """One null study: a draw of changes, scored by both arms at every seed."""
    changes = [pop[rng.randrange(len(pop))] for _ in range(len(pop))]
    # tau is the seed-and-arm shift in the redraw rate. Only redrawn outcomes move, and the
    # contrast differences two arms, so the contrast's seed main effect is f * sqrt(2) * tau.
    tau = sigma_b / (f * 2**0.5) if f > 0 else 0.0
    stable = []
    for _, size, p in changes:
        examples = []
        for _ in range(size):
            shared = _bernoulli(rng, p)
            if rng.random() < q:
                examples.append((_bernoulli(rng, p), _bernoulli(rng, p)))
            else:
                examples.append((shared, shared))
        stable.append(examples)
    runs = []
    for _ in range(seeds):
        shift_t, shift_c = rng.gauss(0.0, tau), rng.gauss(0.0, tau)
        run = []
        for position, ((_, _, p), examples) in enumerate(zip(changes, stable, strict=True)):
            treatment = tuple(
                _bernoulli(rng, p + shift_t) if rng.random() < f else t for t, _ in examples
            )
            control = tuple(
                _bernoulli(rng, p + shift_c) if rng.random() < f else c for _, c in examples
            )
            # Positional ids: a change drawn twice is two clusters, as the bootstrap assumes.
            run.append(Cluster(f"c{position}", treatment, control))
        runs.append(run)
    return runs


def median_seed(runs: list[list[Cluster]], *, bootstrap_seed: int, resamples: int) -> dict:
    """The registered rule: the change bootstrap of the seed whose estimate is the median."""
    intervals = [cluster_bootstrap(r, seed=bootstrap_seed, resamples=resamples) for r in runs]
    middle = median(i["estimate"] for i in intervals)
    return min(intervals, key=lambda i: abs(i["estimate"] - middle))


def trial(job: tuple[int, int, float, float, float, int, Population]) -> dict[str, float]:
    index, seeds, sigma_b, q, f, resamples, pop = job
    rng = random.Random(f"{index}-{seeds}-{sigma_b}")
    runs = simulate(rng, pop, seeds=seeds, q=q, f=f, sigma_b=sigma_b)
    single = median_seed(runs, bootstrap_seed=index, resamples=resamples)
    crossed = crossed_bootstrap(runs, seed=index, resamples=resamples)
    return {
        "median_seed_above": float(supports_direction(single)),
        "median_seed_below": float(single["high"] < 0.0),
        "median_seed_width": single["high"] - single["low"],
        "crossed_above": float(supports_direction(crossed)),
        "crossed_below": float(crossed["high"] < 0.0),
        "crossed_width": crossed["high"] - crossed["low"],
    }


def calibrate(pop: Population, *, q: float, f: float, draws: int = 200) -> None:
    """The three quantities the two identical nulls measured, under the chosen q and f."""
    rng = random.Random(0)
    single_se, run_to_run, churn = [], [], []
    for index in range(draws):
        a, b = simulate(rng, pop, seeds=2, q=q, f=f, sigma_b=0.0)
        interval = cluster_bootstrap(a, seed=index, resamples=500)
        single_se.append((interval["high"] - interval["low"]) / 3.92)
        run_to_run.append(paired_difference(b) - paired_difference(a))
        pairs = [
            (ta - ca, tb - cb)
            for x, y in zip(a, b, strict=True)
            for ta, ca, tb, cb in zip(x.treatment, x.control, y.treatment, y.control, strict=True)
        ]
        churn.append(fmean(1.0 if u != v else 0.0 for u, v in pairs))
    print(
        f"q={q} f={f}: single-seed SE {fmean(single_se):.4f}  run-to-run SD "
        f"{pstdev(run_to_run):.4f}  per-example contrast churn {fmean(churn):.3f}"
    )
    print("measured: single-seed SE 0.012-0.015, run-to-run 0.009-0.012, churn 0.037-0.053")


def main() -> None:
    args = parser.parse_args()
    pop = population(args.source, args.arm)
    if args.calibrate:
        calibrate(pop, q=args.q, f=args.f)
        return
    cells = []
    with ProcessPoolExecutor(args.workers) as pool:
        for seeds in args.seeds:
            for sigma_b in args.sigma_b:
                jobs = [
                    (i, seeds, sigma_b, args.q, args.f, args.resamples, pop)
                    for i in range(args.trials)
                ]
                outcomes = list(pool.map(trial, jobs, chunksize=10))
                cell = {
                    "seeds": seeds,
                    "sigma_b": sigma_b,
                    **{k: fmean(o[k] for o in outcomes) for k in outcomes[0]},
                }
                cells.append(cell)
                print(
                    f"S={seeds} sigma_b={sigma_b:<6} one-sided FPR: median-seed "
                    f"{cell['median_seed_above']:.3f} crossed {cell['crossed_above']:.3f} | "
                    f"below: {cell['median_seed_below']:.3f} {cell['crossed_below']:.3f} | "
                    f"width {cell['median_seed_width']:.4f} {cell['crossed_width']:.4f}",
                    flush=True,
                )
    if args.out:
        args.out.write_text(
            json.dumps(
                {
                    "source": str(args.source),
                    "arm": args.arm,
                    "q": args.q,
                    "f": args.f,
                    "trials": args.trials,
                    "resamples": args.resamples,
                    "nominal_one_sided": 0.025,
                    "cells": cells,
                    "provenance": run_provenance(),
                },
                indent=2,
            )
        )
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
