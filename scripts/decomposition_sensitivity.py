"""The smallest half-split effect the decomposition gate detects, and whether absence is reachable.

A sensitivity analysis, as `sensitivity.py` is for the organization-level gate, rebuilt for the
decomposition: each H1 cell is two equally weighted halves read off `stratified_crossed_draws`
at Holm's two levels. The variance model is the placebo's own-against-sibling contrast on each
half, which is exactly an H1 cell at half-organization size, simulated at the test window's
projected size split between the halves in their dev-window proportion.

Two numbers per cell and level:

- the realised exact-match difference detected with marginal power `--target` (0.928 per cell
  gives about 0.80 across three independent cells, the intersection-union test's joint power);
- the share of null studies whose interval sits inside the SESOI band, which says whether the
  registered "absent" verdict is reachable at all at this design's size.

    uv run --no-sync --no-active python scripts/decomposition_sensitivity.py \\
        --placebo openstack=datasets/results/rq1-placebo-openstack.json \\
        --placebo qt=datasets/results/rq1-placebo-qt.json \\
        --size openstack=1809 --size qt=3281 \\
        --sigma-b openstack=0.0106 --sigma-b qt=0.0148 \\
        --out datasets/results/decomposition-sensitivity.json
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import fmean

from sphragis.experiment.decomposition import SESOI, halves, holm_levels
from sphragis.experiment.power import realised_difference, stratified_seed_trial
from sphragis.experiment.runner import to_clusters
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--placebo", action="append", required=True, metavar="ORG=PATH")
parser.add_argument("--size", action="append", default=[], metavar="ORG=N", help="test changes")
parser.add_argument("--sigma-b", action="append", default=[], metavar="ORG=S")
parser.add_argument("--seeds", type=int, default=5)
parser.add_argument("--redraw", type=float, default=0.055)
parser.add_argument("--target", type=float, default=0.928, help="marginal power per cell")
parser.add_argument("--trials", type=int, default=100)
parser.add_argument("--resamples", type=int, default=1000)
parser.add_argument("--steps", type=int, default=7, help="bisection steps on the lift")
parser.add_argument("--seed", type=int, default=41)
parser.add_argument("--workers", type=int, default=6)
parser.add_argument("--out", type=Path, required=True)


def _pairs(values: list[str]) -> dict[str, str]:
    return dict(v.split("=", 1) for v in values)


def _trial(job):
    cell, lift, sizes, seeds, sigma_b, redraw, resamples, seed, levels = job
    return stratified_seed_trial(
        cell,
        lift=lift,
        n_changes=sizes,
        seeds=seeds,
        sigma_b=sigma_b,
        redraw=redraw,
        resamples=resamples,
        seed=seed,
        confidences=levels,
        sesoi=SESOI,
    )


def run_trials(pool, cell, lift, sizes, sigma_b, args, levels):
    jobs = [
        (cell, lift, sizes, args.seeds, sigma_b, args.redraw, args.resamples, args.seed + t, levels)
        for t in range(args.trials)
    ]
    return list(pool.map(_trial, jobs, chunksize=2))


def main() -> None:
    args = parser.parse_args()
    placebos = {org: Path(p) for org, p in _pairs(args.placebo).items()}
    sizes = {org: int(n) for org, n in _pairs(args.size).items()}
    sigma = {org: float(s) for org, s in _pairs(args.sigma_b).items()}
    levels = holm_levels(2)
    report: dict = {
        "seeds": args.seeds,
        "target_marginal_power": args.target,
        "sesoi": SESOI,
        "levels": levels,
        "trials": args.trials,
        "resamples": args.resamples,
        "cells": {},
    }
    with ProcessPoolExecutor(args.workers) as pool:
        for org, path in placebos.items():
            results = json.loads(path.read_text())["results"]
            first, second = halves(org)
            cell = [
                to_clusters(results[f"adapter:{w}|{w}|s1"], results[f"adapter:{s}|{w}|s1"])
                for w, s in ((first, second), (second, first))
            ]
            total = sum(len(h) for h in cell)
            planned = sizes.get(org, total)
            half_sizes = [max(10, round(planned * len(h) / total)) for h in cell]
            sigma_b = sigma.get(org, 0.013)
            null = run_trials(pool, cell, 0.0, half_sizes, sigma_b, args, levels)
            entry: dict = {
                "pilot_changes_per_half": [len(h) for h in cell],
                "planned_changes_per_half": half_sizes,
                "sigma_b": sigma_b,
                "null_false_positive": {c: fmean(t.supported[c] for t in null) for c in levels},
                "null_reads_absent": {c: fmean(t.absent[c] for t in null) for c in levels},
                "by_level": {},
            }
            for level in levels:
                low, high = 0.0, 0.3
                for _ in range(args.steps):
                    mid = (low + high) / 2
                    trials = run_trials(pool, cell, mid, half_sizes, sigma_b, args, [level])
                    if fmean(t.supported[level] for t in trials) >= args.target:
                        high = mid
                    else:
                        low = mid
                effect = fmean(
                    realised_difference(h, lift=high, seed=args.seed, draws=300) for h in cell
                )
                entry["by_level"][level] = {"lift": high, "minimum_detectable_effect": effect}
                print(
                    f"{org} H1 {half_sizes} at {level}: detects {effect:+.4f} at power "
                    f"{args.target}; null reads absent {entry['null_reads_absent'][level]:.3f}",
                    flush=True,
                )
            report["cells"][org] = entry
    report["provenance"] = provenance_header()
    args.out.write_text(json.dumps(report, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
