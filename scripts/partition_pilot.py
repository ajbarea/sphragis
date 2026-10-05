"""H1 over repeated partitions on real runs: the cell, the number of runs, and the runs' order.

Takes one single-seed `partition_run` result per admissible partition, in the admissible list's
order, and checks each is the partition and seed that order assigns. Reads H1 with
`h1_over_partitions` over the first K of them at the registered Holm levels, K read from the
pilot artifact's `sizing.runs` given as `--sizing` (all runs, when omitted: the pilot itself);
with `--sensitivity`, each level's bound is the detectable effect that simulation found at
`--spread-target` for an H1 over `--h1-cells` organizations. With `--planted`, the organization's
planted dev run must pass outcome-neutral check 5 (`neutral.planted_convention`) or the cell is
not read. On the development window it is
the pilot, and `runs_needed` sizes the organization's K from every run computed. A GitHub
replication member takes `--replication` and is read at its registered fixed level instead, as
one cell (its bounds from a simulation of one cell).

    uv run --no-sync --no-active python scripts/partition_pilot.py \\
        --admissible datasets/results/admissible-partitions-openstack.json \\
        datasets/results/rq1-partition-openstack-p2-n1850.json ... \\
        --out datasets/results/partition-pilot-openstack.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from sphragis.experiment.across import TEST_BOOTSTRAP_SEED, TEST_RESAMPLES, require_resamples
from sphragis.experiment.decomposition import (
    REPLICATION_CONFIDENCE,
    REPLICATION_FAMILY,
    REPLICATION_MEMBERS,
    holm_levels,
    valid_bound,
)
from sphragis.experiment.neutral import apparatus_holds, planted_convention, source_windows
from sphragis.experiment.partitions import (
    h1_over_partitions,
    pilot_sizing,
    runs_needed,
    sensitivity_bounds,
)
from sphragis.measure.stats import one_sided_alpha
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("runs", type=Path, nargs="+", help="partition runs, in admissible order")
parser.add_argument("--admissible", type=Path, required=True)
parser.add_argument("--org", default="openstack")
parser.add_argument(
    "--sizing", type=Path, help="the organization's pilot artifact: K is its sizing.runs"
)
# H1 and H2: the registered Holm family (2 when omitted).
parser.add_argument("--hypotheses", type=int, help="the Holm family size")
parser.add_argument(
    "--replication",
    action="store_true",
    help="a GitHub replication member, read at its own fixed level, not a Holm step",
)
parser.add_argument("--sensitivity", type=Path, help="a partition-sensitivity artifact")
parser.add_argument(
    "--spread-target",
    choices=("pilot_lower_90", "pilot_estimate", "sizing_bound_90", "pilot_upper_99"),
    help="which point on the pilot's run spread the bounds assume",
)
parser.add_argument("--h1-cells", type=int, help="the admitted organizations H1 intersects")
# Any fixed value: fixed so the reading reproduces.
parser.add_argument(
    "--planted", type=Path, help="the organization's planted dev run (outcome-neutral check 5)"
)
parser.add_argument("--bootstrap-seed", type=int, default=TEST_BOOTSTRAP_SEED)
# The gate's registered resample count, as every confirmatory interval here uses.
parser.add_argument("--resamples", type=int, default=TEST_RESAMPLES)
parser.add_argument("--out", type=Path, required=True)


def main() -> None:
    args = parser.parse_args()
    # Every check on the arguments alone, before any run is read.
    if args.replication != (args.org in REPLICATION_FAMILY):
        raise SystemExit(
            f"{args.org}: --replication is for the GitHub family {REPLICATION_FAMILY} only, "
            "and every member needs it"
        )
    if args.replication:
        if REPLICATION_MEMBERS is not None and args.org not in REPLICATION_MEMBERS:
            raise SystemExit(f"{args.org} is not among the frozen members {REPLICATION_MEMBERS}")
        if args.hypotheses is not None:
            raise SystemExit("--replication reads one fixed level; --hypotheses does not apply")
        # A member is read alone, so its simulation is of one cell at that level.
        if args.h1_cells not in (None, 1):
            raise SystemExit("--replication reads a member as one cell: --h1-cells 1")
        args.h1_cells = 1
    levels = (
        [REPLICATION_CONFIDENCE]
        if args.replication
        else holm_levels(2 if args.hypotheses is None else args.hypotheses)
    )
    for c in levels:
        try:
            require_resamples(args.resamples, alpha=one_sided_alpha(c), org=args.org)
        except ValueError as error:
            raise SystemExit(str(error)) from error
    listing = json.loads(args.admissible.read_text())
    admissible, train_size = listing["admissible"], listing["size_floor"]
    if len(args.runs) > len(admissible):
        raise SystemExit(f"{len(args.runs)} runs, more than the {len(admissible)} admissible")
    runs = []
    windows: set[str] = set()
    for k, path in enumerate(args.runs, start=1):
        run = json.loads(path.read_text())
        if "halted" in run:
            raise SystemExit(f"{path}: halted at {run['halted']}; the apparatus failed, not read")
        seeds = run["seeds"]
        # Every half's source, so a run with one half on the test window is a test-window read.
        windows |= {source_windows(c["source"]) for c in run["corpora"].values()}
        source = next(iter(run["corpora"].values()))["source"]
        # The partition's corpus root, `corpus-partition-<org>-p<seed>[-<tags>]`, and its seed.
        root = re.search(r"/corpus-partition-[^/]*?-p(\d+)(?:-[^/]*)?/", source)
        partition = int(root.group(1)) if root else None
        if run.get("train_size") != train_size:
            raise SystemExit(
                f"{path}: trained at {run.get('train_size')}, not the list's {train_size}"
            )
        if seeds != [k]:
            raise SystemExit(f"{path}: run {k} must use training seed {k}, has {seeds}")
        if partition != admissible[k - 1]:
            raise SystemExit(
                f"{path}: run {k} must use admissible partition {admissible[k - 1]}, "
                f"built from {source}"
            )
        runs.append((run["results"], k))
    if windows == {"train -> test"}:
        window = "test"
    elif windows == {"train -> dev"}:
        window = "development"
    else:
        raise SystemExit(f"runs read on {sorted(windows)}, not one of the two windows")
    if window == "test":
        # Fixed before the test window: who is read, its K, and the draws its interval takes.
        if args.replication and (
            REPLICATION_MEMBERS is None or args.org not in REPLICATION_MEMBERS
        ):
            raise SystemExit(f"{args.org}'s test window is read only once it is a frozen member")
        if not args.sizing:
            raise SystemExit("a test-window read needs --sizing: K from the development pilot")
        if (args.resamples, args.bootstrap_seed) != (TEST_RESAMPLES, TEST_BOOTSTRAP_SEED):
            raise SystemExit(
                f"a test-window read takes {TEST_RESAMPLES} resamples at bootstrap seed "
                f"{TEST_BOOTSTRAP_SEED}, the registered ones"
            )
    # Check 5 is part of the halt rule, so a read of the sealed window cannot go without it.
    if window == "test" and not args.planted:
        raise SystemExit("a test-window read needs --planted: outcome-neutral check 5 halts it")
    # Bounds are registered before the test window, so it is never read without them.
    if window == "test" and not args.sensitivity:
        raise SystemExit("a test-window read needs --sensitivity: its registered bounds")
    planted = None
    if args.planted:
        check = planted_convention(
            json.loads(args.planted.read_text()), org=args.org, train_size=train_size
        )
        planted = {"file": str(args.planted), "passed": check.passed, **check.evidence}
        if not apparatus_holds([check]):
            raise SystemExit(f"{args.planted}: check 5 failed ({check.evidence}); H1 is not read")
    runs_fixed, k_source = len(runs), "all runs"
    if args.sizing:
        try:
            runs_fixed = pilot_sizing(
                json.loads(args.sizing.read_text()), str(args.sizing), org=args.org
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error
        k_source = f"{args.sizing}: sizing.runs"
    bounds = None
    simulation = None
    if args.sensitivity:
        if not args.spread_target:
            raise SystemExit("--sensitivity needs --spread-target")
        if not args.h1_cells:
            raise SystemExit("--sensitivity needs --h1-cells")
        sensitivity = json.loads(args.sensitivity.read_text())
        if sensitivity.get("org") != args.org:
            raise SystemExit(
                f"{args.sensitivity} simulates {sensitivity.get('org')}, not {args.org}"
            )
        bounds = sensitivity_bounds(sensitivity, args.spread_target, levels, cells=args.h1_cells)
        invalid = {c: b for c, b in bounds.items() if not valid_bound(b)}
        if invalid:
            raise SystemExit(f"{args.sensitivity}: bounds {invalid} are not detectable effects")
        simulation = {
            "file": str(args.sensitivity),
            "org": sensitivity["org"],
            "spread_target": args.spread_target,
            "cells": args.h1_cells,
        }
    cell = h1_over_partitions(
        runs,
        org=args.org,
        runs_fixed=runs_fixed,
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
    head = {
        "run_files",
        "admissible",
        "k_source",
        "levels",
        "window",
        "sensitivity",
        "planted_convention",
        "sizing",
        "provenance",
    }
    if head & set(cell):
        raise SystemExit(f"cell keys {sorted(head & set(cell))} would overwrite the report's")
    report = {
        "run_files": [str(p) for p in args.runs],
        "admissible": str(args.admissible),
        "k_source": k_source,
        "levels": levels,
        "window": window,
        "sensitivity": simulation,
        "planted_convention": planted,
        **cell,
        "sizing": sizing,
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
