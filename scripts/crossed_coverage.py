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
change, sized by `tau_for` so the seed main effect realized after clipping is the one
labelled. `--calibrate` prints the single-seed standard error, the run-to-run standard error
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
from statistics import fmean, median, pstdev, variance

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
    return 1.0 if rng.random() < _clip(p) else 0.0


def _clip(p: float) -> float:
    return min(1.0, max(0.0, p))


def realized_sigma_b(pop: Population, *, tau: float, f: float, draws: int = 4000) -> float:
    """The contrast's seed main effect a seed-and-arm shift of SD `tau` actually produces.

    A seed's expected contrast, given its two arm shifts, is f times the example-weighted mean
    of clip(p + shift_t) - clip(p + shift_c). Without clipping its SD is f * sqrt(2) * tau, but
    most changes sit at a rate of exactly 0 or 1, where a shift one way is cut off, so the
    naive value overstates the effect simulated.
    """
    rng = random.Random("realized")
    total = sum(size for _, size, _ in pop)
    effects = []
    for _ in range(draws):
        shift_t, shift_c = rng.gauss(0.0, tau), rng.gauss(0.0, tau)
        effects.append(
            f * sum(size * (_clip(p + shift_t) - _clip(p + shift_c)) for _, size, p in pop) / total
        )
    return pstdev(effects)


def tau_for(pop: Population, *, sigma_b: float, f: float) -> float:
    """The arm shift whose expected seed main effect is `sigma_b`, by bisection.

    The effect is bounded: once shifts are large every redraw is 0 or 1, and on sym-0 at
    f = 0.055 it tops out near 0.039. A target at or past that has no shift, so it is refused
    rather than searched for forever.
    """
    if sigma_b == 0.0:
        return 0.0
    if f <= 0.0:
        raise ValueError(f"a seed effect of {sigma_b} needs a redraw rate f > 0, got {f}")
    low, high = 0.0, 1.0
    while realized_sigma_b(pop, tau=high, f=f) < sigma_b:
        high *= 2
        if high > 1_000:
            ceiling = realized_sigma_b(pop, tau=high, f=f)
            raise ValueError(
                f"sigma_b {sigma_b} exceeds the largest reachable, about {ceiling:.4f}"
            )
    for _ in range(40):
        middle = (low + high) / 2
        if realized_sigma_b(pop, tau=middle, f=f) < sigma_b:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def simulate(
    rng: random.Random, pop: Population, *, seeds: int, q: float, f: float, tau: float
) -> list[list[Cluster]]:
    """One null study: a draw of changes, scored by both arms at every seed.

    `tau` is the SD of each seed-and-arm shift in the redraw rate; `tau_for` converts the seed
    main effect wanted into it.
    """
    changes = [pop[rng.randrange(len(pop))] for _ in range(len(pop))]
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


def trial(job: tuple[int, int, float, float, float, float, int, Population]) -> dict[str, float]:
    index, seeds, sigma_b, tau, q, f, resamples, pop = job
    rng = random.Random(f"{index}-{seeds}-{sigma_b}")
    runs = simulate(rng, pop, seeds=seeds, q=q, f=f, tau=tau)
    single = median_seed(runs, bootstrap_seed=index, resamples=resamples)
    crossed = crossed_bootstrap(runs, seed=index, resamples=resamples)
    estimates = [paired_difference(run) for run in runs]
    first, second = runs[0], runs[1]
    changed = [
        (ta - ca) != (tb - cb)
        for x, y in zip(first, second, strict=True)
        for ta, ca, tb, cb in zip(x.treatment, x.control, y.treatment, y.control, strict=True)
    ]
    return {
        "between_seed_variance": variance(estimates),
        "churn": fmean(1.0 if c else 0.0 for c in changed),
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
        a, b = simulate(rng, pop, seeds=2, q=q, f=f, tau=0.0)
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
        taus = {sigma_b: tau_for(pop, sigma_b=sigma_b, f=args.f) for sigma_b in args.sigma_b}
        for sigma_b, tau in taus.items():
            naive = args.f * 2**0.5 * tau
            print(f"sigma_b {sigma_b}: tau {tau:.4f} (unclipped the effect would be {naive:.4f})")
        for seeds in args.seeds:
            for sigma_b in args.sigma_b:
                tau = taus[sigma_b]
                jobs = [
                    (i, seeds, sigma_b, tau, args.q, args.f, args.resamples, pop)
                    for i in range(args.trials)
                ]
                outcomes = list(pool.map(trial, jobs, chunksize=10))
                cell = {
                    "seeds": seeds,
                    "sigma_b": sigma_b,
                    "tau": tau,
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
    # The seed effect the trials produced, not the target: between-seed variance of each trial's
    # estimate, less the same with no seed effect, which is the seed-by-change part alone.
    for cell in cells:
        null = next((c for c in cells if c["seeds"] == cell["seeds"] and c["sigma_b"] == 0.0), None)
        excess = (
            None if null is None else cell["between_seed_variance"] - null["between_seed_variance"]
        )
        cell["measured_sigma_b"] = None if excess is None else max(0.0, excess) ** 0.5
        cell["churn_in_measured_band"] = 0.037 <= cell["churn"] <= 0.053
        print(
            f"S={cell['seeds']} sigma_b={cell['sigma_b']}: measured {cell['measured_sigma_b']}, "
            f"churn {cell['churn']:.3f} (measured band 0.037-0.053)"
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
