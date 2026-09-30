"""H1 over repeated partitions on real runs: the cell, the number of runs, and the runs' order.

Takes one single-seed `partition_run` result per admissible partition, in the admissible list's
order, and checks each is the partition and seed that order assigns. Reads H1 with
`h1_over_partitions` over the first `--fixed-k` of them (all, when omitted) at the registered Holm
levels; with `--sensitivity`, each level's bound is the detectable effect that simulation found at
`--sigma-run`. On the development window it is the pilot, and `runs_needed` sizes the
organization's K from every run computed.

    uv run --no-sync --no-active python scripts/partition_pilot.py \\
        --admissible datasets/results/admissible-partitions-openstack.json \\
        datasets/results/rq1-partition-openstack-p2-n1850.json ... \\
        --out datasets/results/partition-pilot-openstack.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sphragis.experiment.decomposition import holm_levels
from sphragis.experiment.partitions import h1_over_partitions, runs_needed
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("runs", type=Path, nargs="+", help="partition runs, in admissible order")
parser.add_argument("--admissible", type=Path, required=True)
parser.add_argument("--org", default="openstack")
parser.add_argument("--fixed-k", type=int, help="the organization's fixed K; all runs when omitted")
# H1 and H2: the registered Holm family.
parser.add_argument("--hypotheses", type=int, default=2, help="the Holm family size")
parser.add_argument("--sensitivity", type=Path, help="a partition-sensitivity artifact")
parser.add_argument("--sigma-run", help="which of its sigma_run entries sets the bounds")
# Any fixed value: fixed so the reading reproduces.
parser.add_argument("--bootstrap-seed", type=int, default=7)
# The gate's registered resample count, as every confirmatory interval here uses.
parser.add_argument("--resamples", type=int, default=10_000)
parser.add_argument("--out", type=Path, required=True)


def main() -> None:
    args = parser.parse_args()
    listing = json.loads(args.admissible.read_text())
    admissible, train_size = listing["admissible"], listing["size_floor"]
    runs = []
    for k, path in enumerate(args.runs, start=1):
        run = json.loads(path.read_text())
        seeds = run["seeds"]
        partition = next(iter(run["corpora"].values()))["source"]
        if run.get("train_size") != train_size:
            raise SystemExit(
                f"{path}: trained at {run.get('train_size')}, not the list's {train_size}"
            )
        if seeds != [k]:
            raise SystemExit(f"{path}: run {k} must use training seed {k}, has {seeds}")
        if f"-p{admissible[k - 1]}-" not in partition:
            raise SystemExit(
                f"{path}: run {k} must use admissible partition {admissible[k - 1]}, "
                f"built from {partition}"
            )
        runs.append((run["results"], k))
    levels = holm_levels(args.hypotheses)
    bounds = None
    if args.sensitivity:
        cells = json.loads(args.sensitivity.read_text())["by_sigma_run"][args.sigma_run]
        bounds = {c: cells["by_level"][str(c)]["minimum_detectable_effect"] for c in levels}
    cell = h1_over_partitions(
        runs,
        org=args.org,
        runs_fixed=args.fixed_k or len(runs),
        levels=levels,
        bounds=bounds,
        bootstrap_seed=args.bootstrap_seed,
        resamples=args.resamples,
    )
    for c in levels:
        interval = cell["intervals"][c]
        print(
            f"H1 {args.org} over {len(cell['per_run'])} partitions at {c}: {cell['estimate']:+.4f} "
            f"[{interval['low']:+.4f}, {interval['high']:+.4f}] {cell['verdicts'][c]}"
        )
    sizing = runs_needed(cell["per_run"] + cell["runs_left_out"])
    print(
        f"read over K={cell['runs']} of {cell['runs_computed']} computed; examples "
        f"{cell['examples']}, dropped {cell['dropped']}; reproducible "
        f"{cell['reproducibility']['holds']}; sizing: sd {sizing['sd']:.4f}, upper "
        f"{sizing['sd_upper']:.4f}, K {sizing['runs']}"
    )
    head = {"run_files", "admissible", "levels", "bounds", "sizing", "provenance"}
    if head & set(cell):
        raise SystemExit(f"cell keys {sorted(head & set(cell))} would overwrite the report's")
    report = {
        "run_files": [str(p) for p in args.runs],
        "admissible": str(args.admissible),
        "levels": levels,
        "bounds": bounds,
        **cell,
        "sizing": sizing,
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
