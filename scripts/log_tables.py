"""Markdown tables for the research log, printed from committed artifacts so no figure is typed.

    uv run --no-sync --no-active python scripts/log_tables.py sensitivity \\
        datasets/results/partition-sensitivity-qt-rp1.0-logprob_per_token-k15.json
    uv run --no-sync --no-active python scripts/log_tables.py k-coverage \\
        datasets/results/k-coverage-qt-likelihood.json
    uv run --no-sync --no-active python scripts/log_tables.py split qt
    uv run --no-sync --no-active python scripts/log_tables.py split openstack wikimedia qt \\
        --lexer-sure

`tests/unit/test_log_tables.py` asserts that each table the log quotes is what this prints.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[1] / "datasets" / "results"
NAMES = {"openstack": "OpenStack", "wikimedia": "Wikimedia", "qt": "Qt"}
SPREAD_POINTS = ("pilot_lower_90", "pilot_estimate", "sizing_bound_90", "pilot_upper_99")
LEVELS = ("0.975", "0.95")
#: Each part of s as the log names it (`likelihood.COMPONENTS`).
PARTS = {
    "identifier": "identifier",
    "other": "other (not a name)",
    "end": "end token",
    "changed": "changed by the revision",
    "copied": "copied from the reviewed hunk",
    "other_changed": "other and changed",
}


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _table(header: list[str], rows: list[list[str]]) -> str:
    return "\n".join([_row(header), "|" + "---|" * len(header), *map(_row, rows)])


def _percent(level: str) -> str:
    return f"{float(level) * 100:g}%"


def sensitivity(path: Path) -> str:
    """A partition simulation's detectable effects over one to three cells, by spread point."""
    report = json.loads(path.read_text())
    rows = []
    for point in SPREAD_POINTS:
        target = report["by_target"][point]
        cells = [point.replace("_", " "), f"{target['calibration']['sigma_run']:.4f}"]
        for level in LEVELS:
            effects = [
                c["minimum_detectable_effect"]
                for c in target["by_level"][level]["by_cells"].values()
            ]
            cells.append(f"{min(effects):.4f} to {max(effects):.4f}")
        cells += [str(target["null_false_positive"][level]) for level in LEVELS]
        rows.append(cells)
    header = ["spread point", "sigma_run"]
    header += [f"detectable, {_percent(level)}" for level in LEVELS]
    header += [f"null false positive, {_percent(level)}" for level in LEVELS]
    return _table(header, rows)


def k_coverage(path: Path) -> str:
    """The coverage rule's grid: each K's null false-positive rates and whether both hold."""
    report = json.loads(path.read_text())
    rows = [
        [str(g["runs"])]
        + [str(g["null_false_positive"][level]) for level in LEVELS]
        + ["yes" if g["within_nominal"] else "no"]
        for g in report["grid"]
    ]
    header = ["K", *(f"null false positive, {_percent(level)}" for level in LEVELS)]
    return _table([*header, "within nominal"], rows)


def _reading(path: Path) -> str:
    report = json.loads(path.read_text())
    interval = report["intervals"]["0.95"]
    return f"{report['estimate']:+.4f} [{interval['low']:+.4f}, {interval['high']:+.4f}]"


def split(orgs: list[str], *, lexer_sure: bool = False) -> str:
    """Each part of s on the development pilots, 95% crossed intervals; with `lexer_sure`, the
    reading without every example a lexer may have misread, with s itself on the same rows."""
    suffix = "-lexer-sure" if lexer_sure else ""
    rows = [
        [label]
        + [
            _reading(RESULTS / f"partition-pilot-{org}-likelihood-split-{part}{suffix}.json")
            for org in orgs
        ]
        for part, label in PARTS.items()
    ]
    if lexer_sure:
        whole = [
            _reading(RESULTS / f"partition-pilot-{org}-likelihood{suffix}.json") for org in orgs
        ]
        dropped = []
        for org in orgs:
            report = json.loads(
                (RESULTS / f"partition-pilot-{org}-likelihood{suffix}.json").read_text()
            )
            kept, gone = report["examples"], report["lexer_sure"]["examples_dropped"]
            dropped.append(f"{gone} of {kept + gone}")
        rows = [["examples dropped", *dropped], ["s on the same rows", *whole], *rows]
    return _table(["Part of s", *(NAMES[org] for org in orgs)], rows)


parser = argparse.ArgumentParser(description=__doc__)
sub = parser.add_subparsers(dest="table", required=True)
sub.add_parser("sensitivity").add_argument("artifact", type=Path)
sub.add_parser("k-coverage").add_argument("artifact", type=Path)
split_parser = sub.add_parser("split")
split_parser.add_argument("orgs", nargs="+", choices=sorted(NAMES))
split_parser.add_argument("--lexer-sure", action="store_true")


def main() -> None:
    args = parser.parse_args()
    if args.table == "sensitivity":
        print(sensitivity(args.artifact))
    elif args.table == "k-coverage":
        print(k_coverage(args.artifact))
    else:
        print(split(args.orgs, lexer_sure=args.lexer_sure))


if __name__ == "__main__":
    main()
