"""Print a result's readings with their labels, so a number is never quoted without one.

Every figure in this study has four coordinates: the estimand (pooled or change-averaged), the
interval rule (the registered crossed one, or the median-seed one it is read beside), the arm,
and the run. A console tail carries the numbers and loses the coordinates, and a number whose
coordinates are guessed is worse than no number: on 2026-09-22 the same placebo contrast was
quoted twice under the wrong rule, from a truncated tail, while the artifact sat on disk.

So this prints them together, always, and `--markdown` emits a table ready to paste. Quoting
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


def main() -> None:
    args = parser.parse_args()
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
