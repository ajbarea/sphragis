"""Print a result's readings with their labels, so a number is never quoted without one.

Every figure in this study has four coordinates: the estimand (pooled or change-averaged), the
interval rule (the registered crossed one, or the median-seed one it is read beside), the arm,
and the run. A console tail carries the numbers and loses the coordinates, and a number whose
coordinates are guessed is worse than no number: on 2026-09-22 the same placebo contrast was
quoted twice under the wrong rule, from a truncated tail, while the artifact sat on disk.

So this prints them together, always, and `--markdown` emits a table ready to paste. A check 5
artifact (`planted_check.py`, kind `planted_check`) prints one line per run and half: its
partition, verdict and projected pass probability, the half's estimate and interval, and the share
of the half the plant changed. Quoting
from here rather than from a job log is meant to be the easy path as well as the correct one.

    uv run --no-sync --no-active python scripts/reading.py \
        datasets/results/rq1-placebo-qt-seeds.json --registered
    uv run --no-sync --no-active python scripts/reading.py <artifact> --rule crossed --markdown
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

#: The rule the study registered. Named here so a caller that wants "the reading we report"
#: does not have to remember which of the four cells that is.
REGISTERED = ("pooled", "crossed")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("artifact", type=Path, nargs="+")
parser.add_argument("--estimand", choices=("pooled", "change_averaged"), default=None)
parser.add_argument("--rule", choices=("crossed", "median_seed"), default=None)
parser.add_argument(
    "--registered", action="store_true", help=f"only {REGISTERED[0]} + {REGISTERED[1]}"
)
parser.add_argument("--markdown", action="store_true", help="a table rather than lines")


def rows(path: Path, *, estimand: str | None, rule: str | None) -> list[tuple[str, ...]]:
    report = json.loads(path.read_text())
    readings = report.get("readings")
    if not isinstance(readings, dict):
        raise SystemExit(f"{path} carries no `readings` block; it is not a merged seed study")
    out: list[tuple[str, ...]] = []
    for this_estimand, block in readings.items():
        if estimand and this_estimand != estimand:
            continue
        for this_rule, reading in block.items():
            if rule and this_rule != rule:
                continue
            if not isinstance(reading, dict) or "per_org" not in reading:
                continue
            for arm, value in reading["per_org"].items():
                out.append(
                    (
                        path.name,
                        this_estimand,
                        this_rule,
                        arm,
                        f"{value['estimate']:+.4f}",
                        f"[{value['low']:+.4f}, {value['high']:+.4f}]",
                        reading.get("verdict", ""),
                    )
                )
    if not out:
        raise SystemExit(f"{path}: no reading matches estimand={estimand} rule={rule}")
    return out


def planted_rows(path: Path) -> list[tuple[str, ...]]:
    """One row per run and half of a `planted_check.py` artifact, its rule's reading beside it."""
    report = json.loads(path.read_text())
    out: list[tuple[str, ...]] = []
    for run in sorted(report["by_run"], key=lambda r: (r["partition"] is None, r["partition"])):
        for half, value in run["halves"].items():
            share = run.get("plant", {}).get(half, {}).get("realised_fraction")
            out.append(
                (
                    f"p{run['partition']}",
                    run["verdict"],
                    f"{run['projected_pass']:.4f}",
                    half,
                    f"{value['estimate']:+.4f}",
                    f"[{value['low']:+.4f}, {value['high']:+.4f}]",
                    "" if share is None else f"{share:.4f}",
                )
            )
    return out


def planted_summary(path: Path) -> str:
    report = json.loads(path.read_text())
    passed = sum(1 for run in report["by_run"] if run["passed"])
    plant = [report["convention"], report["fraction"]]
    return (
        f"{path.name}: {report['org']}, plant {report['convention']} at f = {report['fraction']}, "
        f"{passed} of {report['runs']} pass on {report['metric']}, projected pass "
        f"min {report['projected_pass_min']:.4f} (floor {report['projected_floor']}), "
        f"{'meets' if report['rule_met'] else 'fails'} item 4's rule, "
        f"{'' if plant == report['registered_plant'] else 'not '}the registered plant"
    )


def _is_planted(path: Path) -> bool:
    return json.loads(path.read_text()).get("kind") == "planted_check"


def main() -> None:
    args = parser.parse_args()
    if any(_is_planted(path) for path in args.artifact):
        if not all(_is_planted(path) for path in args.artifact):
            raise SystemExit("check 5 artifacts are read apart from seed studies; pass one kind")
        for path in args.artifact:
            print(planted_summary(path))
            found = planted_rows(path)
            if args.markdown:
                head = ("partition", "verdict", "projected pass", "half", "estimate")
                print("| " + " | ".join((*head, "interval", "changed")) + " |")
                print("|---|---|---|---|---|---|---|")
                for row in found:
                    print("| " + " | ".join(row) + " |")
            else:
                for row in found:
                    print("  ".join(row))
        return
    estimand = REGISTERED[0] if args.registered else args.estimand
    rule = REGISTERED[1] if args.registered else args.rule
    found = [row for path in args.artifact for row in rows(path, estimand=estimand, rule=rule)]

    if args.markdown:
        print("| run | estimand | rule | arm | estimate | interval |")
        print("|---|---|---|---|---|---|")
        for run, this_estimand, this_rule, arm, estimate, span, _ in found:
            print(f"| {run} | {this_estimand} | {this_rule} | {arm} | {estimate} | {span} |")
        return
    width = max(len(row[0]) for row in found)
    for run, this_estimand, this_rule, arm, estimate, span, verdict in found:
        mark = "  <- registered" if (this_estimand, this_rule) == REGISTERED else ""
        print(
            f"{run:<{width}}  {this_estimand:<15} {this_rule:<11} {arm:<12} "
            f"{estimate} {span} {verdict}{mark}"
        )


if __name__ == "__main__":
    main()
