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
from pathlib import Path

from sphragis.experiment import decomposition
from sphragis.experiment.cells import (
    REGISTERED_SENSITIVITIES,
    SPREAD_TARGETS,
    TEST_BOOTSTRAP_SEED,
    TEST_RESAMPLES,
    require_resamples,
    require_test_read,
    sensitivity_bounds,
)
from sphragis.experiment.decomposition import (
    ORGANIZATIONS,
    REPLICATION_CONFIDENCE,
    REPLICATION_FAMILY,
    SESOI,
    holm_levels,
    registered_read,
    valid_bound,
)
from sphragis.experiment.neutral import apparatus_holds, planted_convention
from sphragis.experiment.partitions import (
    eval_ids,
    h1_over_partitions,
    partition_run_windows,
    pilot_sizing,
    runs_needed,
    spread_targets,
)
from sphragis.experiment.runs import decoder
from sphragis.measure.stats import one_sided_alpha
from sphragis.provenance import provenance_header

#: A sensitivity listing's window (`sensitivity_ids.py --window`) to this read's name for it.
READ_WINDOW = {"dev": "development", "test": "test"}

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("runs", type=Path, nargs="+", help="partition runs, in admissible order")
parser.add_argument("--admissible", type=Path, required=True)
parser.add_argument("--org", default="openstack")
parser.add_argument(
    "--sizing", type=Path, help="the organization's pilot artifact: K is its sizing.runs"
)
# A development read's Holm family size (2 when omitted); a test read takes the registered one.
parser.add_argument("--hypotheses", type=int, help="the Holm family size")
parser.add_argument(
    "--replication",
    action="store_true",
    help="a GitHub replication member, read at its own fixed level, not a Holm step",
)
parser.add_argument("--sensitivity", type=Path, help="a partition-sensitivity artifact")
parser.add_argument(
    "--spread-target",
    choices=SPREAD_TARGETS,
    help="which point on the pilot's run spread the bounds assume",
)
parser.add_argument("--h1-cells", type=int, help="the admitted organizations H1 intersects")
# Any fixed value: fixed so the reading reproduces.
parser.add_argument(
    "--planted", type=Path, help="the organization's planted dev run (outcome-neutral check 5)"
)
parser.add_argument(
    "--without",
    action="append",
    default=[],
    metavar="NAME=FILE",
    help="a registered sensitivity: the cell again without sensitivity_ids.py's NAME ids",
)
# The per-example score H1 is read on: greedy exact match, or the reference's mean log-probability
# per token from `likelihood_score.py` (research log, 2026-10-09).
parser.add_argument(
    "--metric", choices=("exact_match", "logprob_per_token", "logprob"), default="exact_match"
)

#: Where each metric's SESOI is registered. Exact match's is the cost-benefit constant; the
#: per-token log-probability's is carried from it by `likelihood_sesoi.py`. On any other metric
#: every reading that compares against a SESOI is reported as unregistered.
LIKELIHOOD_SESOI = Path("datasets/results/likelihood-sesoi.json")


def metric_sesoi(metric: str) -> float | None:
    if metric == "exact_match":
        return SESOI
    if metric == "logprob_per_token" and LIKELIHOOD_SESOI.is_file():
        return json.loads(LIKELIHOOD_SESOI.read_text())["sesoi"]
    return None


parser.add_argument("--bootstrap-seed", type=int, default=TEST_BOOTSTRAP_SEED)
# The gate's registered resample count, as every confirmatory interval here uses.
parser.add_argument("--resamples", type=int, default=TEST_RESAMPLES)
parser.add_argument("--out", type=Path, required=True)


def check_resamples(args: argparse.Namespace, levels: list[float]) -> None:
    """Refuse a resample count that puts any level's bound between draws, before any is taken."""
    for c in levels:
        try:
            require_resamples(args.resamples, alpha=one_sided_alpha(c), org=args.org)
        except ValueError as error:
            raise SystemExit(f"not read: {error}") from error


def sensitivity_listings(specs: list[str], *, org: str) -> dict[str, tuple[str, dict]]:
    """Each --without as (file, listing), checked on the arguments alone, before any run is read."""
    listings: dict[str, tuple[str, dict]] = {}
    for spec in specs:
        name, sep, path = spec.partition("=")
        if not (name and sep and path):
            raise SystemExit(f"--without {spec!r}: give NAME=FILE")
        if name not in REGISTERED_SENSITIVITIES:
            raise SystemExit(f"--without {name!r} is not a registered sensitivity")
        if name in listings:
            raise SystemExit(f"--without names {name!r} twice")
        if not Path(path).is_file():
            raise SystemExit(f"--without {name}: no file {path}")
        listing = json.loads(Path(path).read_text())
        if name not in listing.get("ids", {}):
            raise SystemExit(f"{path} lists no {name!r} ids")
        if listing.get("org") != org:
            raise SystemExit(f"{path} lists {listing.get('org')}'s examples, not {org}'s")
        listings[name] = (path, listing)
    return listings


def main() -> None:
    args = parser.parse_args()
    sesoi = metric_sesoi(args.metric)
    listings = sensitivity_listings(args.without, org=args.org)
    # Every check on the arguments alone, before any run is read.
    if args.replication != (args.org in REPLICATION_FAMILY):
        raise SystemExit(
            f"{args.org}: --replication is for the GitHub family {REPLICATION_FAMILY} only, "
            "and every member needs it"
        )
    if not args.replication and args.org not in ORGANIZATIONS:
        raise SystemExit(f"{args.org} is not a registered Gerrit organization {ORGANIZATIONS}")
    if args.replication:
        frozen = decomposition.REPLICATION_MEMBERS
        if frozen is not None and args.org not in frozen:
            raise SystemExit(f"{args.org} is not among the frozen members {frozen}")
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
    # A development read's levels are final here; a test read's are replaced by the registered
    # ones and checked again below.
    check_resamples(args, levels)
    listing = json.loads(args.admissible.read_text())
    admissible, train_size = listing["admissible"], listing["size_floor"]
    if len(args.runs) > len(admissible):
        raise SystemExit(f"{len(args.runs)} runs, more than the {len(admissible)} admissible")
    runs = []
    windows: set[str] = set()
    decoders: set[str] = set()
    for k, path in enumerate(args.runs, start=1):
        run = json.loads(path.read_text())
        try:
            windows |= partition_run_windows(
                run, position=k, admissible=admissible, train_size=train_size
            )
        except ValueError as error:
            raise SystemExit(f"{path}: {error}") from error
        missing = sorted(
            arm for arm, rows in run["results"].items() if rows and args.metric not in rows[0]
        )
        if missing:
            raise SystemExit(f"{path}: arms {missing} carry no {args.metric}")
        runs.append((run["results"], k))
        decoders.add(json.dumps(decoder(run), sort_keys=True))
    if len(decoders) != 1:
        raise SystemExit(f"runs decoded differently, not one study: {sorted(decoders)}")
    if windows == {"train -> test"}:
        window = "test"
    elif windows == {"train -> dev"}:
        window = "development"
    else:
        raise SystemExit(f"runs read on {sorted(windows)}, not one of the two windows")
    if window == "test":
        # Fixed before the test window: who is read, its K, and the draws its interval takes.
        if not args.sizing:
            raise SystemExit("a test-window read needs --sizing: K from the development pilot")
        # Levels, cell count and spread target come from the registration, never the command.
        try:
            expected = registered_read(args.org)
        except ValueError as error:
            raise SystemExit(f"not read: {error}") from error
        chosen = {
            "levels": None if args.hypotheses is None else holm_levels(args.hypotheses),
            "cells": args.h1_cells,
            "spread_target": args.spread_target,
        }
        conflicts = {k: v for k, v in chosen.items() if v is not None and v != expected[k]}
        if conflicts:
            raise SystemExit(f"not read: {conflicts} differ from the registered {expected}")
        levels = expected["levels"]
        args.h1_cells, args.spread_target = expected["cells"], expected["spread_target"]
        # Checked again on the report by require_test_read; here before any draw is taken.
        if (args.resamples, args.bootstrap_seed) != (TEST_RESAMPLES, TEST_BOOTSTRAP_SEED):
            raise SystemExit(
                f"not read: {args.resamples} resamples, seed {args.bootstrap_seed}; "
                f"registered {TEST_RESAMPLES} at {TEST_BOOTSTRAP_SEED}"
            )
    if window == "test":
        check_resamples(args, levels)
    # Check 5 is part of the halt rule, so a read of the sealed window cannot go without it.
    # A K, a bound or a test read rests on the SESOI, so none is taken on an unregistered metric.
    if sesoi is None and (args.sizing or args.sensitivity or window == "test"):
        raise SystemExit(
            f"{args.metric} has no registered SESOI: no --sizing, --sensitivity or test read"
        )
    # A K or a bound carries the units of the metric it was made on.
    for artifact in (args.sizing, args.sensitivity):
        made_on = (
            json.loads(artifact.read_text()).get("metric", "exact_match") if artifact else None
        )
        if made_on is not None and made_on != args.metric:
            raise SystemExit(f"{artifact} was made on {made_on}, this read is on {args.metric}")
    if window == "test" and not args.planted:
        raise SystemExit("a test-window read needs --planted: outcome-neutral check 5 halts it")
    # Bounds are registered before the test window, so it is never read without them.
    if window == "test" and not args.sensitivity:
        raise SystemExit("a test-window read needs --sensitivity: its registered bounds")
    planted = None
    if args.planted:
        planted_run = json.loads(args.planted.read_text())
        # Check 5 reads the planted run's verdict, which is on the metric the run was scored by.
        if planted_run.get("metric", "exact_match") != args.metric:
            raise SystemExit(
                f"{args.planted} is scored on {planted_run.get('metric', 'exact_match')}"
            )
        check = planted_convention(planted_run, org=args.org, train_size=train_size)
        planted = {"file": str(args.planted), "passed": check.passed, **check.evidence}
        if not apparatus_holds([check]):
            raise SystemExit(f"{args.planted}: check 5 failed ({check.evidence}); H1 is not read")
    runs_fixed, k_source, k_from = len(runs), "all runs", None
    if args.sizing:
        sizing_artifact = json.loads(args.sizing.read_text())
        try:
            runs_fixed = pilot_sizing(
                sizing_artifact, str(args.sizing), org=args.org, require_org=window == "test"
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error
        k_source = f"{args.sizing}: sizing.runs"
        k_from = {"file": str(args.sizing), "org": sizing_artifact.get("org"), "runs": runs_fixed}
        # Only a test read is checked against its simulation's calibration.
        if window == "test":
            try:
                k_from["spread_targets"] = spread_targets(sizing_artifact)
            except (KeyError, TypeError, ValueError) as error:
                raise SystemExit(
                    f"{args.sizing}: no per-run spread to calibrate on ({error})"
                ) from error
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
        try:
            bounds = sensitivity_bounds(
                sensitivity, args.spread_target, levels, cells=args.h1_cells
            )
        except (KeyError, TypeError, ValueError) as error:
            raise SystemExit(f"not read: {args.sensitivity}: {error}") from error
        invalid = {c: b for c, b in bounds.items() if not valid_bound(b)}
        if invalid:
            raise SystemExit(f"{args.sensitivity}: bounds {invalid} are not detectable effects")
        simulation = {
            "file": str(args.sensitivity),
            "org": sensitivity["org"],
            "runs": sensitivity.get("runs"),
            "spread_target": args.spread_target,
            "cells": args.h1_cells,
            "spread_targets": sensitivity.get("spread_targets"),
            "planned_changes": sensitivity.get("planned_changes"),
        }
    # Each sensitivity's listing checked against this read before any draw is taken: its window,
    # and the corpus it was made from, which must hold every example any run scored (the runs
    # past K are read too, so their examples are checked as well).
    read = set.intersection(*(eval_ids(results, "adapter") for results, _ in runs[:runs_fixed]))
    scored = set().union(*(eval_ids(results, "adapter") for results, _ in runs))
    for path, ids_listing in listings.values():
        listed = ids_listing.get("window")
        if READ_WINDOW.get(listed) != window:
            raise SystemExit(f"{path} lists the {listed} window, not this read's {window}")
        outside = sorted(scored - set(ids_listing.get("universe", [])))
        if outside:
            raise SystemExit(
                f"{path} was made from another corpus: {len(outside)} of the runs' examples, "
                f"{outside[:3]}, are not in it"
            )
    if window == "test":
        missing = [n for n in REGISTERED_SENSITIVITIES if n not in listings]
        if missing:
            raise SystemExit(f"a test-window read needs --without for {missing}")
    cell = h1_over_partitions(
        runs,
        org=args.org,
        runs_fixed=runs_fixed,
        levels=levels,
        bounds=bounds,
        bootstrap_seed=args.bootstrap_seed,
        resamples=args.resamples,
        metric=args.metric,
        sesoi=sesoi or SESOI,
    )
    sizing = runs_needed(cell["per_run"] + cell["runs_left_out"], xi=sesoi or SESOI)
    if sesoi is None:
        # The SESOI is 0.01 in exact match; on this metric it has no meaning, nor have the
        # verdicts, margins and K that compare against it. Intervals and estimates stand.
        for key in ("verdicts", "within_sesoi", "meaningful", "reproducibility"):
            cell[key] = None
        kept = ("pilot_runs", "sd", "sd_upper", "confidence")
        sizing = {key: value for key, value in sizing.items() if key in kept}
        sizing["runs"] = None
    # The registered sensitivities, beside the cell and binding nothing: the same runs, levels,
    # seed and draws, without each named set of examples.
    without = {}
    for name, (path, ids_listing) in listings.items():
        drop = set(ids_listing["ids"][name])
        # What the AI-trailer searches never reached among the cell's examples, stated beside it.
        unsearched = len(set(ids_listing.get("ai_unsearched", [])) & read)
        kept = [
            ({arm: [r for r in rows if r["id"] not in drop] for arm, rows in results.items()}, k)
            for results, k in runs
        ]
        try:
            reduced = h1_over_partitions(
                kept,
                org=args.org,
                runs_fixed=runs_fixed,
                levels=levels,
                bounds=None,
                bootstrap_seed=args.bootstrap_seed,
                resamples=args.resamples,
                metric=args.metric,
                sesoi=sesoi or SESOI,
            )
        except ValueError as error:
            without[name] = {
                "file": path,
                "listed": len(drop),
                **({"unsearched": unsearched} if name == "ai_assisted" else {}),
                "unreadable": str(error),
            }
            continue
        fields = ("intervals", "within_sesoi", "meaningful", "p_one_sided", "bootstrap_se")
        if sesoi is None:
            fields = ("intervals", "p_one_sided", "bootstrap_se")
        without[name] = {
            "file": path,
            "listed": len(drop),
            **({"unsearched": unsearched} if name == "ai_assisted" else {}),
            # The examples that left the cell, not ids that never entered it.
            "removed": cell["examples"] - reduced["examples"],
            "estimate": reduced["estimate"],
            "examples": reduced["examples"],
            **{key: reduced[key] for key in fields},
        }
    head = {
        "run_files",
        "metric",
        "sesoi",
        "decoder",
        "admissible",
        "k_source",
        "k_from",
        "levels",
        "window",
        "sensitivity",
        "planted_convention",
        "sizing",
        "without",
        "provenance",
    }
    if head & set(cell):
        raise SystemExit(f"cell keys {sorted(head & set(cell))} would overwrite the report's")
    report = {
        "run_files": [str(p) for p in args.runs],
        "metric": args.metric,
        "sesoi": sesoi,
        "decoder": json.loads(next(iter(decoders))),
        "admissible": str(args.admissible),
        "k_source": k_source,
        "k_from": k_from,
        "levels": levels,
        "window": window,
        "sensitivity": simulation,
        "planted_convention": planted,
        **cell,
        "sizing": sizing,
        "without": without,
        "provenance": provenance_header(),
    }
    if window == "test":
        try:
            require_test_read(report, org=args.org, expected=expected)
        except ValueError as error:
            raise SystemExit(f"not read: {error}") from error
    for c in levels:
        interval = cell["intervals"][c]
        print(
            f"H1 {args.org} over {len(cell['per_run'])} partitions at {c}: {cell['estimate']:+.4f} "
            f"[{interval['low']:+.4f}, {interval['high']:+.4f}] "
            f"{cell['verdicts'][c] if cell['verdicts'] else 'no registered SESOI'}"
        )
    print(
        f"read over K={cell['runs']} of {cell['runs_computed']} computed; examples "
        f"{cell['examples']}, dropped {cell['dropped']}; reproducible "
        f"{cell['reproducibility']['holds'] if cell['reproducibility'] else None}; "
        f"sizing: sd {sizing['sd']:.4f}, upper "
        f"{sizing['sd_upper']:.4f}, K {sizing['runs']}"
    )
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
