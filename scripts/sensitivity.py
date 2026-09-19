"""The smallest organizational effect the confirmatory design detects, by seed count.

A sensitivity analysis rather than power at an observed effect. The test window's size is fixed
by what exists, and power computed from a pilot's own estimate is biased upward, the more so when
the pilot looked promising (Albers and Lakens, JESP 2018; Lakens, "Sample Size Justification",
Collabra 2022). So this reports, per organization and seed count, the exact-match difference the
registered gate detects with marginal power `target`, under the crossed interval and a seed main
effect of `--sigma-b`. The gate is conjunctive, so a marginal target of 0.894 per organization
gives about 0.80 for both together when the organizations are independent.

The variance model is each organization's own windowed run: its changes, sizes and exact-match
rates, with seeds simulated as in `power.seed_runs`.

    uv run --no-sync --no-active python scripts/sensitivity.py --sigma-b 0.013 \
        --seeds 3 5 10 --size openstack=2171 --size qt=3937 \
        --out datasets/results/sensitivity-b0.013.json
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from sphragis.experiment.power import realised_difference, seed_trial
from sphragis.experiment.runner import to_clusters
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser()
parser.add_argument(
    "results", type=Path, nargs="?", default=Path("datasets/results/rq1-windows.json")
)
parser.add_argument("--size", action="append", default=[], metavar="ORG=N")
parser.add_argument("--seeds", type=int, nargs="+", default=[3, 5, 10])
parser.add_argument("--sigma-b", type=float, default=0.013)
parser.add_argument("--redraw", type=float, default=0.055)
parser.add_argument("--target", type=float, default=0.894, help="marginal power per organization")
parser.add_argument("--trials", type=int, default=120)
parser.add_argument("--resamples", type=int, default=300)
parser.add_argument("--steps", type=int, default=8, help="bisection steps on the lift")
parser.add_argument("--seed", type=int, default=31)
parser.add_argument("--workers", type=int, default=6)
parser.add_argument("--out", type=Path, required=True)


def _trial(job):
    clusters, lift, n, seeds, sigma_b, redraw, resamples, seed = job
    return seed_trial(
        clusters,
        lift=lift,
        n_changes=n,
        seeds=seeds,
        sigma_b=sigma_b,
        redraw=redraw,
        resamples=resamples,
        seed=seed,
    ).crossed


def power_at(pool, clusters, lift, n, seeds, args) -> float:
    jobs = [
        (clusters, lift, n, seeds, args.sigma_b, args.redraw, args.resamples, args.seed + t)
        for t in range(args.trials)
    ]
    return sum(pool.map(_trial, jobs, chunksize=4)) / args.trials


def main() -> None:
    args = parser.parse_args()
    results = json.loads(args.results.read_text())["results"]
    sizes = dict(s.split("=") for s in args.size)
    orgs = sorted({k.split("|")[1] for k in results if k.startswith("base|")})
    report: dict = {
        "sigma_b": args.sigma_b,
        "target_marginal_power": args.target,
        "trials": args.trials,
        "resamples": args.resamples,
        "organizations": {},
    }
    with ProcessPoolExecutor(args.workers) as pool:
        for org in orgs:
            other = next(o for o in orgs if o != org)
            clusters = to_clusters(
                results[f"adapter:{org}|{org}|s1"], results[f"adapter:{other}|{org}|s1"]
            )
            n = int(sizes.get(org, len(clusters)))
            report["organizations"][org] = {"n_changes": n, "by_seeds": {}}
            for seeds in args.seeds:
                low, high = 0.0, 0.3
                for _ in range(args.steps):
                    mid = (low + high) / 2
                    if power_at(pool, clusters, mid, n, seeds, args) >= args.target:
                        high = mid
                    else:
                        low = mid
                effect = realised_difference(clusters, lift=high, seed=args.seed, draws=400)
                report["organizations"][org]["by_seeds"][seeds] = {
                    "lift": high,
                    "minimum_detectable_effect": effect,
                }
                print(
                    f"{org} ({n} changes), {seeds} seeds: detects {effect:+.4f} exact match "
                    f"at marginal power {args.target}",
                    flush=True,
                )
    report["provenance"] = provenance_header()
    args.out.write_text(json.dumps(report, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
