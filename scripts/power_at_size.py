"""Power at the registered bound for a test window that came in below its projected size.

"Fetch horizon" (registered-decisions.md): a window whose changes fall short of the simulation's
projection is reported beside its verdict with the simulation's power at the realised size. This
reruns that simulation's trials at the number of changes the test read was read on, at the lift
that registered each level's bound and the run shift it was calibrated to, and records the share
of trials supported at that level with its Monte Carlo standard error (Morris, White and Crowther,
Stat. Med. 2019). Power is at the registered bound, never at an observed effect.

Every input is read from an artifact: the realised size, levels, cell count and spread target
from the test report (`require_test_read` fixed them); K, trials, resamples, churn, the run shift
and each level's lift from the simulation the report was read under. The pilot clusters and the
partitions are rebuilt from the corpus as the simulation built them (`partition_sensitivity.pools`).

    uv run --no-sync --no-active python scripts/power_at_size.py \\
        --report datasets/results/partition-test-openstack.json \\
        --simulation datasets/results/partition-sensitivity-openstack.json \\
        --placebo datasets/results/rq1-placebo-openstack-v3.json \\
        --corpus datasets/gerrit \\
        --admissible datasets/results/admissible-partitions-openstack.json \\
        --out datasets/results/power-at-size-openstack.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import fmean
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from partition_sensitivity import Pool, _init, pools, trial  # noqa: E402

from sphragis.experiment.cells import by_level, is_count, level_key  # noqa: E402
from sphragis.provenance import provenance_header  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--report", type=Path, required=True, help="partition_pilot.py's test read")
parser.add_argument(
    "--simulation", type=Path, required=True, help="the partition_sensitivity.py artifact it read"
)
parser.add_argument("--placebo", type=Path, required=True, help="the simulation's placebo run")
parser.add_argument("--corpus", type=Path, required=True, help="the corpus root, for projects")
parser.add_argument("--admissible", type=Path, required=True, help="the admissible list")
# Any fixed value, fixed so the result reproduces; `partition_sensitivity.py`'s default.
parser.add_argument("--seed", type=int, default=43)
parser.add_argument("--workers", type=int, default=6)
parser.add_argument("--out", type=Path, required=True)


def registered_lifts(
    report: Mapping[str, Any], simulation: Mapping[str, Any]
) -> tuple[float, dict[float, dict[str, float]]]:
    """The run shift and, per level the report was read at, the lift and bound it registered.

    The report must have been read under this simulation: its organization, K, projected size and
    calibration, and the bound it recorded at each level.
    """
    recorded = report["sensitivity"]
    for name in ("org", "runs", "planned_changes"):
        if simulation.get(name) != recorded.get(name):
            raise ValueError(
                f"the report was read under a simulation with {name} {recorded.get(name)!r}, "
                f"this one has {simulation.get(name)!r}"
            )
    target = simulation["by_target"][recorded["spread_target"]]
    levels = by_level(target["by_level"])
    read_under = by_level(report["bounds"])
    out: dict[float, dict[str, float]] = {}
    for c in report["levels"]:
        at = levels[level_key(c)]["by_cells"][str(recorded["cells"])]
        if read_under.get(level_key(c)) != at["minimum_detectable_effect"]:
            raise ValueError(f"the report's bound at {c} is not this simulation's")
        out[level_key(c)] = {"lift": at["lift"], "bound": at["minimum_detectable_effect"]}
    return target["calibration"]["sigma_run"], out


def power_at(
    executor: Any,
    pool: Pool,
    *,
    changes: int,
    sigma_run: float,
    lifts: Mapping[float, Mapping[str, float]],
    simulation: Mapping[str, Any],
    seed: int,
) -> dict[float, dict[str, float]]:
    """Each level's power at its registered lift on `changes` changes, with its Monte Carlo SE.

    One set of trials per distinct lift reads every level, as the simulation's bisection does.
    """
    if not is_count(changes):
        raise ValueError(f"changes {changes!r} is not a whole number")
    levels = list(lifts)
    trials = simulation["trials"]
    at_lift: dict[float, list[dict]] = {}
    out: dict[float, dict[str, float]] = {}
    for c, registered in lifts.items():
        lift = registered["lift"]
        if lift not in at_lift:
            jobs = [
                (
                    pool,
                    changes,
                    lift,
                    sigma_run,
                    simulation["redraw"],
                    simulation["resamples"],
                    seed + t,
                    levels,
                    simulation["runs"],
                    False,
                )
                for t in range(trials)
            ]
            at_lift[lift] = list(executor.map(trial, jobs, chunksize=1))
        power = fmean(t["supported"][c] for t in at_lift[lift])
        out[c] = {
            **registered,
            "power": power,
            "mc_se": math.sqrt(power * (1.0 - power) / trials),
        }
    return out


def main() -> None:
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    simulation = json.loads(args.simulation.read_text())
    if report.get("window") != "test":
        raise SystemExit(f"{args.report} is not a test-window read")
    if simulation.get("placebo") != str(args.placebo):
        raise SystemExit(f"{args.simulation} was simulated on {simulation.get('placebo')}")
    try:
        sigma_run, lifts = registered_lifts(report, simulation)
    except (KeyError, TypeError, ValueError) as error:
        raise SystemExit(f"{args.report}: {error}") from error
    org, changes = report["org"], report["changes"]
    pool, partitions, _ = pools(
        args.placebo,
        args.corpus,
        org,
        args.admissible,
        simulation["partitions_pooled"],
        simulation["partition_seeds_tried"],
    )
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(partitions,)) as executor:
        by_level_ = power_at(
            executor,
            pool,
            changes=changes,
            sigma_run=sigma_run,
            lifts=lifts,
            simulation=simulation,
            seed=args.seed,
        )
    for c, at in by_level_.items():
        print(
            f"{org} at {c}: power {at['power']:.3f} (MC SE {at['mc_se']:.3f}) at bound "
            f"{at['bound']:+.4f} on {changes} changes, {simulation['planned_changes']} projected"
        )
    out = {
        "org": org,
        "report": str(args.report),
        "simulation": str(args.simulation),
        "changes": changes,
        "planned_changes": simulation["planned_changes"],
        "runs": simulation["runs"],
        "spread_target": report["sensitivity"]["spread_target"],
        "cells": report["sensitivity"]["cells"],
        "levels": report["levels"],
        "sigma_run": sigma_run,
        "trials": simulation["trials"],
        "resamples": simulation["resamples"],
        "seed": args.seed,
        "by_level": {str(c): at for c, at in by_level_.items()},
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
