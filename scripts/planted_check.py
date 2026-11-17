"""Read an organization's check 5 runs under the rule that registers a plant as its check 5.

Each run is read by `neutral.planted_convention`, the reader a test read applies, and beside it
the pass probability projected from its two halves' estimates and interval half-widths: normal
per half, the half-width over 1.96 as its standard error, the product over halves (research log,
2026-09-30, the rule check 5's plant was first registered under). The plant is registered for the
organization only on `REGISTERED_RUNS` runs of different partitions, each read on s, every one
passing and every one projected at `PROJECTED_FLOOR` or above (research log, 2026-11-16, item 4;
which partitions the runs are, the organization's first ten admissible, is the submission's).
Each run's corpus manifest gives the share of each half the plant changed and the share carrying
its convention. The plant read is the organization's registered one (`cells.registered_plant`)
unless `--convention` and `--fraction` name a candidate, as the 2026-11-16 runs were read.

    uv run --no-sync --no-active python scripts/planted_check.py --org qt \
        --convention code --fraction 1.0 \
        --run likelihood-partition-qt-p4-n1850-plantcode1.0-rp1.0.json=corpus/placebo.json ... \
        --out datasets/results/planted-check-qt-code1.0.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

from sphragis.experiment.cells import PLANT_BY_ORG, REGISTERED_METRIC, registered_plant
from sphragis.experiment.neutral import planted_convention
from sphragis.provenance import provenance_header

#: The registered rule's floor on each run's projected pass probability, and its count of runs.
PROJECTED_FLOOR = 0.95
REGISTERED_RUNS = 10
#: The training size every check 5 run is read at, the admissible list's size floor.
TRAIN_SIZE = 1850
_Z = 1.959963984540054
#: A run's corpus (`partition_run.sbatch`'s ROOT) and the partition it names: the training seed
#: in the name changes no corpus, so runs are counted by partition.
_ROOT = re.compile(r"corpus-partition-[^/]+")


def _partition(root: str, org: str) -> int | None:
    match = re.match(rf"corpus-partition-{re.escape(org)}-p(\d+)(?:-|$)", root)
    return int(match.group(1)) if match else None


def projected_pass(binding: dict[str, dict[str, float]]) -> float:
    """The product over halves of Φ(estimate / se), se the interval's half-width over 1.96."""
    product = 1.0
    for half in binding.values():
        se = (half["high"] - half["low"]) / 2 / _Z
        if se > 0:
            product *= 0.5 * (1 + math.erf(half["estimate"] / se / math.sqrt(2)))
        else:
            # A point interval projects certainty on its estimate's side of zero.
            product *= 1.0 if half["estimate"] > 0 else 0.0
    return product


def read_run(
    run_file: Path, manifest_file: Path | None, org: str, plant: tuple[str, float]
) -> dict[str, Any]:
    run = json.loads(run_file.read_text())
    convention, fraction = plant
    check = planted_convention(
        run, org=org, train_size=TRAIN_SIZE, convention=convention, fraction=fraction
    )
    binding = run["verdict"]["binding"]
    roots = {m.group(0) for c in run["corpora"].values() if (m := _ROOT.search(c["source"]))}
    out: dict[str, Any] = {
        "file": run_file.name,
        "corpus": sorted(roots)[0] if len(roots) == 1 else None,
        "partition": _partition(next(iter(roots)), org) if len(roots) == 1 else None,
        "metric": run.get("metric", "exact_match"),
        "passed": check.passed,
        "verdict": run["verdict"]["verdict"],
        "projected_pass": projected_pass(binding),
        "halves": {
            name: {k: half[k] for k in ("estimate", "low", "high")}
            for name, half in binding.items()
        },
        "evidence": check.evidence,
    }
    if manifest_file is not None:
        plant = json.loads(manifest_file.read_text())["plant"]
        out["plant"] = {
            name: {
                k: half.get(k)
                for k in (
                    "examples",
                    "eligible",
                    "changed",
                    "realised_fraction",
                    "carrying",
                    "carrying_fraction",
                )
            }
            for name, half in plant["halves"].items()
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", required=True)
    parser.add_argument(
        "--run", action="append", required=True, help="RUN.json[=placebo.json], one per run"
    )
    parser.add_argument("--convention", help="a candidate plant (default: the registered one)")
    parser.add_argument("--fraction", type=float, help="the candidate's fraction")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if (args.convention is None) != (args.fraction is None):
        parser.error("--convention and --fraction name a candidate together")
    try:
        plant = (
            registered_plant(args.org)
            if args.convention is None
            else (args.convention, args.fraction)
        )
    except ValueError as error:
        parser.error(str(error))
    runs = []
    for spec in args.run:
        run, _, manifest = spec.partition("=")
        runs.append(read_run(Path(run), Path(manifest) if manifest else None, args.org, plant))
    passed = sum(r["passed"] for r in runs)
    projected = [r["projected_pass"] for r in runs]
    # Item 4: ten runs of different partitions, read on s, all passing, each projected at the floor.
    partitions = [r["partition"] for r in runs]
    distinct = None not in partitions and len(set(partitions)) == len(runs)
    on_s = all(r["metric"] == REGISTERED_METRIC for r in runs)
    rule_met = (
        len(runs) == REGISTERED_RUNS
        and distinct
        and on_s
        and passed == len(runs)
        and min(projected) >= PROJECTED_FLOOR
    )
    out = {
        "kind": "planted_check",
        "org": args.org,
        "convention": plant[0],
        "fraction": plant[1],
        "runs": len(runs),
        "registered_runs": REGISTERED_RUNS,
        "metric": REGISTERED_METRIC if on_s else sorted({r["metric"] for r in runs}),
        "passed": passed,
        "projected_pass_min": min(projected),
        "projected_pass_mean": sum(projected) / len(projected),
        "projected_floor": PROJECTED_FLOOR,
        "rule_met": rule_met,
        # The plant `cells` registers as the organization's check 5, which a candidate may not be.
        # None for an organization with no registered plant yet, a candidate's first reading.
        "registered_plant": (list(PLANT_BY_ORG[args.org]) if args.org in PLANT_BY_ORG else None),
        "by_run": runs,
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    print(
        f"{args.org}: {passed} of {len(runs)} pass, projected min {min(projected):.4f}; "
        f"{'meets' if rule_met else 'FAILS'} item 4's rule"
    )


if __name__ == "__main__":
    main()
