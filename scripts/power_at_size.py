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
partitions are rebuilt from the corpus as the simulation built them (`partition_sensitivity.pools`):
on a continuous metric from the pilot's runs, with the change-by-run noise the simulation recorded.

    uv run --no-sync --no-active python scripts/power_at_size.py \\
        --report datasets/results/partition-test-openstack.json \\
        --simulation datasets/results/partition-sensitivity-openstack.json \\
        --placebo datasets/results/rq1-placebo-openstack-v3.json \\
        --pilot datasets/results/partition-pilot-openstack.json \\
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
import partition_sensitivity  # noqa: E402
from partition_sensitivity import Pool, _init, pools, trials_at, worker_args  # noqa: E402

from sphragis.experiment.cells import by_level, is_count, level_key, same_calibration  # noqa: E402
from sphragis.provenance import provenance_header  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--report", type=Path, required=True, help="partition_pilot.py's test read")
parser.add_argument(
    "--simulation", type=Path, required=True, help="the partition_sensitivity.py artifact it read"
)
parser.add_argument("--placebo", type=Path, required=True, help="the simulation's placebo run")
parser.add_argument("--pilot", type=Path, required=True, help="the simulation's pilot artifact")
parser.add_argument("--corpus", type=Path, required=True, help="the corpus root, for projects")
parser.add_argument("--admissible", type=Path, required=True, help="the admissible list")
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
    if report.get("org") != recorded.get("org"):
        raise ValueError(f"the report for {report.get('org')!r} records {recorded.get('org')!r}")
    for name in ("org", "runs", "planned_changes"):
        if simulation.get(name) != recorded.get(name):
            raise ValueError(
                f"the report was read under a simulation with {name} {recorded.get(name)!r}, "
                f"this one has {simulation.get(name)!r}"
            )
    if not same_calibration(simulation.get("spread_targets"), recorded.get("spread_targets")):
        raise ValueError("the report was read under a simulation calibrated on another pilot")
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


def recorded_pilot(simulation: Mapping[str, Any]) -> str:
    """The file name of the pilot the simulation calibrated on, from its recorded inputs."""
    source = simulation.get("inputs", {}).get("calibration_changes", "")
    path, sep, field = source.rpartition(": ")
    if not sep or field != "changes":
        raise ValueError(f"the simulation records no pilot ({source!r})")
    return Path(path).name


def simulation_seed(simulation: Mapping[str, Any]) -> int:
    """The seed the simulation's trials took: recorded, or `partition_sensitivity.py`'s default,
    which every simulation that predates the record ran at."""
    return simulation.get("seed", partition_sensitivity.parser.get_default("seed"))


def power_at(
    executor: Any,
    pool: Pool,
    *,
    changes: int,
    sigma_run: float,
    lifts: Mapping[float, Mapping[str, float]],
    simulation: Mapping[str, Any],
) -> dict[float, dict[str, float]]:
    """Each level's power at its registered lift on `changes` changes, with its Monte Carlo SE.

    The trials take the simulation's own seed, churn, resamples and K. One set of trials per
    distinct lift reads every level, as the simulation's bisection does.
    """
    if not is_count(changes):
        raise ValueError(f"changes {changes!r} is not a positive whole number")
    levels = list(lifts)
    trials = simulation["trials"]
    at_lift: dict[float, list[dict]] = {}
    out: dict[float, dict[str, float]] = {}
    for c, registered in lifts.items():
        lift = registered["lift"]
        if lift not in at_lift:
            at_lift[lift] = trials_at(
                executor,
                pool,
                size=changes,
                lift=lift,
                sigma_run=sigma_run,
                redraw=simulation["redraw"],
                resamples=simulation["resamples"],
                seed=simulation_seed(simulation),
                levels=levels,
                runs=simulation["runs"],
                count=trials,
            )
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
    recorded = report.get("sensitivity") or {}
    if Path(recorded.get("file", "")).name != args.simulation.name:
        raise SystemExit(f"{args.report} was read under {recorded.get('file')}")
    if not report.get("changes", 0) < recorded.get("planned_changes", 0):
        raise SystemExit(
            f"{args.report}: {report.get('changes')} changes against "
            f"{recorded.get('planned_changes')} projected; only a window below it needs this"
        )
    # By name: the simulation records the path it ran on, which differs between machines.
    if Path(simulation.get("placebo", "")).name != args.placebo.name:
        raise SystemExit(f"{args.simulation} was simulated on {simulation.get('placebo')}")
    try:
        if recorded_pilot(simulation) != args.pilot.name:
            raise ValueError(f"the simulation calibrated on {recorded_pilot(simulation)}")
        sigma_run, lifts = registered_lifts(report, simulation)
    except (KeyError, TypeError, ValueError) as error:
        raise SystemExit(f"{args.report}: {error}") from error
    # Old artifacts predate the field and are exact match.
    metric = simulation.get("metric", "exact_match")
    if report.get("metric", "exact_match") != metric:
        raise SystemExit(f"{args.report} is read on {report.get('metric')}, simulated on {metric}")
    org, changes = report["org"], report["changes"]
    pool, partitions, tried, noise, shrink = pools(
        args.placebo,
        args.corpus,
        org,
        args.admissible,
        simulation["partitions_pooled"],
        simulation["partition_seeds_tried"],
        metric,
        json.loads(args.pilot.read_text()),
    )
    # The rebuild must be the simulation's own: the same pilot changes and the same partitions,
    # and on a continuous metric the same noise and shrinkage.
    if (len(pool), tried) != (simulation["pilot_changes"], simulation["partition_seeds_tried"]):
        raise SystemExit(
            f"{args.corpus} rebuilt {len(pool)} pilot changes and partitions to seed {tried}; "
            f"the simulation had {simulation['pilot_changes']} and "
            f"{simulation['partition_seeds_tried']}"
        )
    if metric != "exact_match":
        recorded_noise = simulation["change_by_run_noise"]
        rebuilt = {"shared_by_change": noise[0], "per_example": noise[1], "shrink": shrink}
        expected = {**recorded_noise, "shrink": simulation["truth_shrinkage"]}
        if any(
            not math.isclose(rebuilt[k], expected[k], rel_tol=1e-9, abs_tol=1e-12) for k in expected
        ):
            raise SystemExit(f"{args.pilot} rebuilt noise {rebuilt}, the simulation had {expected}")
    initargs = worker_args(partitions, metric, noise)
    if initargs[2] != simulation.get("sesoi", initargs[2]):
        raise SystemExit(
            f"{metric}'s SESOI is {initargs[2]}, the simulation's {simulation['sesoi']}"
        )
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=initargs) as executor:
        by_level_ = power_at(
            executor,
            pool,
            changes=changes,
            sigma_run=sigma_run,
            lifts=lifts,
            simulation=simulation,
        )
    for c, at in by_level_.items():
        print(
            f"{org} at {c}: power {at['power']:.3f} (MC SE {at['mc_se']:.3f}) at bound "
            f"{at['bound']:+.4f} on {changes} changes, {simulation['planned_changes']} projected"
        )
    out = {
        "org": org,
        "metric": metric,
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
        "seed": simulation_seed(simulation),
        "by_level": {str(c): at for c, at in by_level_.items()},
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
