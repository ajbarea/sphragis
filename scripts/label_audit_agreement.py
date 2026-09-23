"""Agreement on the label audit: two blind model raters, and a human's blind check of one.

`label_audit_sample.py` draws the sheet. Each item gets a label (is the rewrite the author's answer
to the comment) and, separately, `outside_names` (does the rewrite use names or values the item
does not show), since a clear request answered with a project's own names is a valid label that
no prompt alone can reproduce. Each rater labels it without seeing the organization,
project, window or half, under neutral item ids, and without seeing the other's labels. The
human then labels the items blind in a fixed random order and only then sees rater A's label, so
any prefix of the check is a fair sample. Reported: Cohen's kappa for each pair with a bootstrap
interval, the confusion tables, and the share of valid labels per organization and half with
Wilson intervals, since a validity rate that differs between the units a contrast compares is
the noise the design cannot cancel.

Only labels keyed by example id are written: the raters' reasons can quote the code, and the
sheet is corpus text, so neither is committed.

    uv run --no-sync --no-active python scripts/label_audit_agreement.py \\
        --sheet scratch/audit-v2/sheet.jsonl --key scratch/audit-v2/key.json \\
        --rater A=scratch/audit-v2/raters/A/labels.json \\
        --rater B=scratch/audit-v2/raters/B/labels.json \\
        --human scratch/audit-v2/human.json --out datasets/results/label-audit-v2.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from label_audit_sample import LABELS  # noqa: E402

from sphragis.measure.agreement import (  # noqa: E402
    cohen_kappa,
    confusion,
    kappa_interval,
    wilson_interval,
)
from sphragis.provenance import provenance_header  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sheet", type=Path, required=True)
parser.add_argument("--key", type=Path, required=True, help="neutral item id -> example id")
parser.add_argument("--rater", action="append", required=True, metavar="NAME=LABELS.json")
parser.add_argument("--human", type=Path, help="neutral item id -> {label, ...}, blind checks")
parser.add_argument("--checked-against", default="A", help="the rater the human checked")
parser.add_argument("--models", default="", help="NAME=model,... recorded beside the labels")
parser.add_argument(
    "--exclude", action="append", default=[], help="item ids left out, e.g. the rubric's example"
)
parser.add_argument("--out", type=Path, required=True)


def _labels(path: Path) -> tuple[dict[str, str], dict[str, bool]]:
    """A rater's labels and outside-names flags, by item."""
    raw = json.loads(path.read_text())
    labels = {item: v["label"] for item, v in raw.items()}
    flags = {item: bool(v.get("outside_names", v.get("outside"))) for item, v in raw.items()}
    bad = sorted({v for v in labels.values() if v not in LABELS})
    if bad:
        raise SystemExit(f"{path}: labels outside the rubric: {bad}")
    return labels, flags


def _pair(first: dict, second: dict, items: list[str], categories: list) -> dict:
    a, b = [first[i] for i in items], [second[i] for i in items]
    low, high = kappa_interval(a, b, seed=20260924)
    return {
        "items": len(items),
        "raw_agreement": sum(x == y for x, y in zip(a, b, strict=True)) / len(items),
        "kappa": cohen_kappa(a, b),
        "kappa_95": [low, high],
        "confusion": {
            str(k): {str(j): n for j, n in row.items()}
            for k, row in confusion(a, b, categories).items()
        },
    }


def _valid_rates(labels: dict[str, str], cell_of: dict[str, tuple[str, str]]) -> dict:
    by: dict[str, Counter[str]] = {}
    for item, label in labels.items():
        org, half = cell_of[item]
        for unit in (org, half):
            by.setdefault(unit, Counter())[label] += 1
    rates = {}
    for unit, counts in sorted(by.items()):
        n = sum(counts.values())
        low, high = wilson_interval(counts["valid"], n)
        rates[unit] = {
            "n": n,
            "valid": counts["valid"],
            "share": counts["valid"] / n,
            "share_95": [low, high],
            "counts": dict(counts),
        }
    return rates


def main() -> None:
    args = parser.parse_args()
    key: dict[str, str] = json.loads(args.key.read_text())
    sheet = {r["id"]: r for r in map(json.loads, args.sheet.read_text().splitlines()) if r}
    cell_of = {item: (sheet[eid]["org"], sheet[eid]["half"]) for item, eid in key.items()}
    raters, flags = {}, {}
    for spec in args.rater:
        name, _, path = spec.partition("=")
        raters[name], flags[name] = _labels(Path(path))
        missing = set(key) - set(raters[name])
        if missing:
            raise SystemExit(f"rater {name} left {len(missing)} items unlabelled")
    names = sorted(raters)
    items = sorted(set(key) - set(args.exclude))
    report: dict = {
        "provenance": provenance_header(),
        "rubric": list(LABELS),
        "models": dict(s.partition("=")[::2] for s in args.models.split(",") if s),
        "items": len(items),
        "excluded": sorted(args.exclude),
        "labels": {
            key[i]: {n: {"label": raters[n][i], "outside_names": flags[n][i]} for n in names}
            for i in items
        },
        "outside_names": {n: sum(flags[n][i] for i in items) for n in names},
        "pairs": {},
        "valid_rates": {n: _valid_rates({i: raters[n][i] for i in items}, cell_of) for n in names},
    }
    for x, first in enumerate(names):
        for second in names[x + 1 :]:
            pair = f"{first}~{second}"
            report["pairs"][pair] = _pair(raters[first], raters[second], items, list(LABELS))
            report["pairs"][f"{pair} outside_names"] = _pair(
                flags[first], flags[second], items, [True, False]
            )
    if args.human:
        human, human_flags = _labels(args.human)
        checked = sorted(set(human) - set(args.exclude))
        for i in checked:
            report["labels"][key[i]]["human"] = {"label": human[i], "outside_names": human_flags[i]}
        against = args.checked_against
        pair = f"human~{against}"
        report["pairs"][pair] = _pair(human, raters[against], checked, list(LABELS))
        report["pairs"][f"{pair} outside_names"] = _pair(
            human_flags, flags[against], checked, [True, False]
        )
        report["valid_rates"]["human"] = _valid_rates({i: human[i] for i in checked}, cell_of)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    for pair, stats in report["pairs"].items():
        low, high = stats["kappa_95"]
        print(f"{pair}: {stats['items']} items, kappa {stats['kappa']:.3f} [{low:.3f}, {high:.3f}]")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
