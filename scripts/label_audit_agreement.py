"""Agreement on the label audit: two blind model raters, and a human's blind check of one.

`label_audit_sample.py` draws the sheet. Each item gets a label (is the rewrite the author's answer
to the comment) and, separately, `outside_names` (does the rewrite use names or values the item
does not show), since a clear request answered with a project's own names is a valid label that no
prompt alone can reproduce. Each rater labels it without seeing the organization, project, window
or half, under neutral item ids, and without seeing the other's labels. The human then labels the
items blind in a fixed random order and only then sees rater A's label, so any prefix of the check
is a fair sample. Reported: raw agreement, Cohen's kappa and Gwet's AC1 for each pair with
bootstrap intervals, specific agreement per label, the confusion tables, and the share of valid
labels per organization and half with Wilson intervals, since a validity rate that differs between
the units a contrast compares is the noise the design cannot cancel.

A human answer the checker reports, after the reveal, as a slip (the check page's `slips` field,
or `--slip ITEM:FIELD`) stays as it was locked: a correction made after seeing rater A's answer
is no longer blind. Every human pair the slip touches is reported again without that item,
beside the locked reading.

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
    ac1_interval,
    cohen_kappa,
    confusion,
    gwet_ac1,
    kappa_interval,
    specific_agreement,
    wilson_interval,
)
from sphragis.provenance import provenance_header  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sheet", type=Path, required=True)
parser.add_argument("--key", type=Path, required=True, help="neutral item id -> example id")
parser.add_argument("--rater", action="append", required=True, metavar="NAME=LABELS.json")
parser.add_argument("--human", type=Path, help="neutral item id -> {label, ...}, blind checks")
parser.add_argument("--checked-against", default="A", help="the rater the human checked")
parser.add_argument(
    "--slip",
    action="append",
    default=[],
    metavar="ITEM:FIELD",
    help="a human answer reported as a slip after the reveal (FIELD: label or outside_names)",
)
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


def _slips(values: list[str], checked: list[str]) -> dict[str, set[str]]:
    """Reported slips by field; each must name a checked item and a field the human answers."""
    slips: dict[str, set[str]] = {"label": set(), "outside_names": set()}
    for value in values:
        item, _, field = value.partition(":")
        if field not in slips:
            raise SystemExit(f"--slip {value}: field must be label or outside_names")
        if item not in checked:
            raise SystemExit(f"--slip {value}: {item} is not a checked item")
        slips[field].add(item)
    return slips


def _is_valid(labels: dict[str, str]) -> dict[str, bool]:
    """The label collapsed to the question the corpus depends on: valid or not."""
    return {item: label == "valid" for item, label in labels.items()}


def _pair(first: dict, second: dict, items: list[str], categories: list) -> dict:
    a, b = [first[i] for i in items], [second[i] for i in items]
    low, high = kappa_interval(a, b, seed=20260924)
    ac1_low, ac1_high = ac1_interval(a, b, categories, seed=20260924)
    return {
        "items": len(items),
        "raw_agreement": sum(x == y for x, y in zip(a, b, strict=True)) / len(items),
        "kappa": cohen_kappa(a, b),
        "kappa_95": [low, high],
        "ac1": gwet_ac1(a, b, categories),
        "ac1_95": [ac1_low, ac1_high],
        "specific_agreement": {str(k): v for k, v in specific_agreement(a, b, categories).items()},
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
            report["pairs"][f"{pair} valid_vs_rest"] = _pair(
                _is_valid(raters[first]), _is_valid(raters[second]), items, [True, False]
            )
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
        report["pairs"][f"{pair} valid_vs_rest"] = _pair(
            _is_valid(human), _is_valid(raters[against]), checked, [True, False]
        )
        report["pairs"][f"{pair} outside_names"] = _pair(
            human_flags, flags[against], checked, [True, False]
        )
        report["valid_rates"]["human"] = _valid_rates({i: human[i] for i in checked}, cell_of)
        recorded = [
            f"{i}:{f}"
            for i, v in json.loads(args.human.read_text()).items()
            for f in v.get("slips", [])
        ]
        slips = _slips(args.slip + [r for r in recorded if r.split(":", 1)[0] in checked], checked)
        report["human_slips"] = {f: sorted(items) for f, items in slips.items() if items}
        if slips["label"]:
            kept = [i for i in checked if i not in slips["label"]]
            report["pairs"][f"{pair} without slips"] = _pair(
                human, raters[against], kept, list(LABELS)
            )
            report["pairs"][f"{pair} valid_vs_rest without slips"] = _pair(
                _is_valid(human), _is_valid(raters[against]), kept, [True, False]
            )
        if slips["outside_names"]:
            kept = [i for i in checked if i not in slips["outside_names"]]
            report["pairs"][f"{pair} outside_names without slips"] = _pair(
                human_flags, flags[against], kept, [True, False]
            )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    for pair, stats in report["pairs"].items():
        low, high = stats["kappa_95"]
        ac1_low, ac1_high = stats["ac1_95"]
        print(
            f"{pair}: {stats['items']} items, kappa {stats['kappa']:.3f} [{low:.3f}, {high:.3f}],"
            f" AC1 {stats['ac1']:.3f} [{ac1_low:.3f}, {ac1_high:.3f}]"
        )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
