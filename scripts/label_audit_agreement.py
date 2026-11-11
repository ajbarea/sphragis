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
is no longer blind. Every human pair the slip touches, with each rater and on each half, is
reported again without that item, beside the locked reading.

The human is paired with every rater: the checked one, whose labels were revealed, and the others,
whose labels never were. Each reveal can calibrate the checker toward the checked rater, so that
pair is also read on the first and second half of the check apart, in the page's order. The model
pairs are read again on the checked items alone, so a human-to-model figure has a comparator on the
same items.

Second raters (`--second-raters`) label the human's checked items of one organization on a page
that reveals nothing (`label_audit_page.py --second-rater`), each under their own id in the page's
store. Their answers are read from that store's export, beside the committed report: every pair
of readers (the model raters, the human and each second rater, under a pseudonym in the order
they first locked an answer) on the items both answered, with raw agreement and its Wilson
interval, kappa, AC1, valid against the rest and outside names, and again without the items a
slip touches. No second rater saw any label, so no pair of theirs is read by halves. The human's
pairs and the model raters' label pair must first reproduce the organization's own reading
(`label_audit_by_org.py`) on the same items.

Only labels keyed by example id are written: the raters' reasons can quote the code, and the
sheet is corpus text, so neither is committed.

    uv run --no-sync --no-active python scripts/label_audit_agreement.py \\
        --sheet scratch/audit-v2/sheet.jsonl --key scratch/audit-v2/key.json \\
        --rater A=scratch/audit-v2/raters/A/labels.json \\
        --rater B=scratch/audit-v2/raters/B/labels.json \\
        --human scratch/audit-v2/human.json --out datasets/results/label-audit-v2.json
    uv run --no-sync --no-active python scripts/label_audit_agreement.py \\
        --from-report datasets/results/label-audit-v2.json \\
        --org-reading datasets/results/label-audit-v2-openstack.json \\
        --second-raters scratch/rater2/export --out datasets/results/label-audit-v2-rater2.json
"""

from __future__ import annotations

import argparse
import json
import math
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
parser.add_argument("--sheet", type=Path)
parser.add_argument("--key", type=Path, help="neutral item id -> example id")
parser.add_argument("--rater", action="append", metavar="NAME=LABELS.json")
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
parser.add_argument("--from-report", type=Path, help="the committed report second raters join")
parser.add_argument(
    "--org-reading", type=Path, help="label_audit_by_org.py's reading of that organization"
)
parser.add_argument(
    "--second-raters", type=Path, help="the second-rater page's store export (answers/...)"
)
parser.add_argument("--out", type=Path, required=True)

# The second-rater page saves answers under this rubric; any other is not this audit's.
RUBRIC_VERSION = "v2"
ROOT = Path(__file__).resolve().parents[1]


def relative(path: Path) -> str:
    """A path as recorded in a committed result: from the repository root when inside it."""
    resolved = path.resolve()
    return str(resolved.relative_to(ROOT)) if resolved.is_relative_to(ROOT) else str(path)


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


def _undefined_as_nan(interval) -> tuple[float, float]:
    """A bootstrap interval, or NaN at both ends where the statistic is undefined on every
    resample (one label throughout, as on a second rater's first few items)."""
    try:
        return interval()
    except ValueError as error:
        if "undefined on every resample" not in str(error):
            raise
        return math.nan, math.nan


def _pair(first: dict, second: dict, items: list[str], categories: list) -> dict:
    a, b = [first[i] for i in items], [second[i] for i in items]
    low, high = _undefined_as_nan(lambda: kappa_interval(a, b, seed=20260924))
    ac1_low, ac1_high = _undefined_as_nan(lambda: ac1_interval(a, b, categories, seed=20260924))
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


def readings(
    items: list[str],
    raters: dict[str, dict[str, str]],
    flags: dict[str, dict[str, bool]],
    *,
    human: tuple[dict[str, str], dict[str, bool]] | None = None,
    slips: dict[str, set[str]] | None = None,
    against: str = "A",
) -> dict[str, dict]:
    """Every pair this report reads, on `items` and the human's checked items among them.

    One definition, so a reading on part of the items (`label_audit_by_org.py`) is computed
    exactly as the pooled one. The human's items are read in sorted id order, and that order's
    halves give the calibration check.
    """
    pairs: dict[str, dict] = {}
    names = sorted(raters)
    for x, first in enumerate(names):
        for second in names[x + 1 :]:
            pair = f"{first}~{second}"
            pairs[pair] = _pair(raters[first], raters[second], items, list(LABELS))
            pairs[f"{pair} valid_vs_rest"] = _pair(
                _is_valid(raters[first]), _is_valid(raters[second]), items, [True, False]
            )
            pairs[f"{pair} outside_names"] = _pair(
                flags[first], flags[second], items, [True, False]
            )
    if human is None:
        return pairs
    labels, human_flags = human
    slips = slips or {"label": set(), "outside_names": set()}
    checked = sorted(set(labels) & set(items))

    def human_pairs(pair: str, name: str, subset: list[str]) -> None:
        """Every question for one human pair, and again without each item a slip touches."""
        questions = (
            ("", labels, raters[name], list(LABELS), slips["label"]),
            (
                " valid_vs_rest",
                _is_valid(labels),
                _is_valid(raters[name]),
                [True, False],
                slips["label"],
            ),
            (" outside_names", human_flags, flags[name], [True, False], slips["outside_names"]),
        )
        for question, first, second, categories, slipped in questions:
            key = pair + question
            pairs[key] = _pair(first, second, subset, categories)
            if slipped & set(subset):
                kept = [i for i in subset if i not in slipped]
                pairs[f"{key} without slips"] = _pair(first, second, kept, categories)

    for name in names:
        human_pairs(f"human~{name}", name, checked)
    for x, first in enumerate(names):
        for second in names[x + 1 :]:
            pairs[f"{first}~{second} on checked items"] = _pair(
                raters[first], raters[second], checked, list(LABELS)
            )
    half = len(checked) // 2
    for part, subset in (("first half", checked[:half]), ("second half", checked[half:])):
        human_pairs(f"human~{against} {part}", against, subset)
    return pairs


REPRODUCED_FIELDS = ("items", "raw_agreement", "kappa", "kappa_95", "ac1", "ac1_95")


def differences(ours: dict, theirs: dict, names: dict[str, str]) -> list[str]:
    """Each named pair whose recomputed figures and recorded figures disagree beyond 1e-12."""
    wrong = []
    for mine, recorded in sorted(names.items()):
        for field in REPRODUCED_FIELDS:
            a, b = ours[mine][field], theirs[recorded][field]
            pairs = zip(a, b, strict=True) if isinstance(a, list) else [(a, b)]
            if not all(math.isclose(x, y, abs_tol=1e-12) for x, y in pairs):
                wrong.append(f"{mine}.{field}: {a} against {b}")
    return wrong


def neutral_ids(report: dict) -> list[str]:
    """The neutral id of each label, in the report's order: the sorted ids less the excluded."""
    excluded = set(report["excluded"])
    total = len(report["labels"]) + len(excluded)
    ids = sorted(set(f"item-{n:03d}" for n in range(1, total + 1)) - excluded)
    if len(ids) != len(report["labels"]):
        raise ValueError("the excluded ids are not among item-001 upward")
    return ids


def checked_items(report: dict, org: str) -> list[str]:
    """The neutral ids of `org`'s items the human checked, in the page's order."""
    chosen = [
        n
        for n, (eid, by) in zip(neutral_ids(report), report["labels"].items(), strict=True)
        if eid.split(":", 1)[0] == org and "human" in by
    ]
    if not chosen:
        raise SystemExit(f"the human checked none of {org}'s items")
    return chosen


def _answer(path: Path) -> dict:
    """One exported answer document, refused unless it is a locked answer under this rubric."""
    body = json.loads(path.read_text())
    if not isinstance(body, dict):
        raise SystemExit(f"{path}: not an answer document")
    if body.get("rubric") != RUBRIC_VERSION:
        raise SystemExit(f"{path}: saved under rubric {body.get('rubric')!r}, not this audit's")
    if (
        body.get("label") not in LABELS
        or not isinstance(body.get("outside_names"), bool)
        or not isinstance(body.get("locked_at"), str)
    ):
        raise SystemExit(f"{path}: not a locked answer under the rubric")
    slips = body.get("slips", [])
    if not isinstance(slips, list) or not set(slips) <= {"label", "outside_names"}:
        raise SystemExit(f"{path}: unknown slip fields {slips}")
    return {**body, "slips": sorted(slips)}


def read_second_raters(export: Path, items: list[str]) -> dict[str, dict[str, dict]]:
    """Each second rater's answers by item, under pseudonyms in the order they first locked one.

    The export is the page store's `answers/<viewer id>/items` collections saved as files
    (`ArtifactData list` with `out_dir`): `answers/<viewer id>/items/<item>.json`, one
    document body each. Viewer ids never leave this function, so nothing written names who
    answered.
    """
    by_viewer: dict[str, dict[str, dict]] = {}
    for path in sorted(export.glob("answers/*/items/*.json")):
        viewer, item = path.parent.parent.name, path.stem
        if item not in items:
            raise SystemExit(f"{path}: {item} is not one of the page's items")
        by_viewer.setdefault(viewer, {})[item] = _answer(path)
    if not by_viewer:
        raise SystemExit(f"{export}: no answers/<viewer id>/items/<item>.json under it")
    names = pseudonyms(export, by_viewer)
    return {names[v]: by_viewer[v] for v in sorted(by_viewer, key=lambda v: int(names[v][1:]))}


def pseudonyms(export: Path, by_viewer: dict[str, dict[str, dict]]) -> dict[str, str]:
    """Each viewer's pseudonym, kept in `<export>/pseudonyms.json` so a later export renames no one.

    A viewer new to the file takes the next number, in the order of their first lock. The file
    holds viewer ids, so it stays beside the export and is never committed.
    """
    kept = export / "pseudonyms.json"
    names: dict[str, str] = json.loads(kept.read_text()) if kept.exists() else {}
    first = {v: min(a["locked_at"] for a in got.values()) for v, got in by_viewer.items()}
    for viewer in sorted(set(by_viewer) - set(names), key=lambda v: (first[v], v)):
        names[viewer] = f"L{len(names) + 1}"
    kept.write_text(json.dumps(names, indent=1) + "\n")
    return names


def reader_pairs(
    readers: dict[str, tuple[dict[str, str], dict[str, bool], dict[str, set[str]]]],
    order: list[str],
    *,
    already: frozenset[str] = frozenset(),
) -> dict[str, dict]:
    """Every pair of readers on the items both answered, each question with and without slips.

    A reader is (labels, outside-names flags, slipped items by field). Raw agreement carries a
    Wilson interval beside the bootstrap intervals of kappa and AC1. Pairs of two readers in
    `already` are left out, as read before.
    """
    pairs: dict[str, dict] = {}
    for x, first in enumerate(order):
        for second in order[x + 1 :]:
            if first in already and second in already:
                continue
            (la, fa, sa), (lb, fb, sb) = readers[first], readers[second]
            shared = sorted(set(la) & set(lb))
            if not shared:
                continue
            questions = (
                ("", la, lb, list(LABELS), "label"),
                (" valid_vs_rest", _is_valid(la), _is_valid(lb), [True, False], "label"),
                (" outside_names", fa, fb, [True, False], "outside_names"),
            )
            for question, a, b, categories, field in questions:
                slipped = sa[field] | sb[field]
                subsets = [("", shared)]
                if slipped & set(shared):
                    subsets.append((" without slips", [i for i in shared if i not in slipped]))
                for suffix, subset in subsets:
                    if not subset:
                        continue
                    stats = _pair(a, b, subset, categories)
                    same = sum(a[i] == b[i] for i in subset)
                    stats["raw_agreement_95"] = list(wilson_interval(same, len(subset)))
                    pairs[f"{first}~{second}{question}{suffix}"] = stats
    return pairs


Reader = tuple[dict[str, str], dict[str, bool], dict[str, set[str]]]


def report_readers(report: dict, org_reading: dict) -> tuple[list[str], dict[str, Reader], dict]:
    """The report's own readers on the checked items of `org_reading`'s organization, and
    their pairs, refused unless those pairs reproduce `org_reading` (`label_audit_by_org.py`).

    Reproduced: the human's pairs under their own names, and the model raters' label pair as
    its "on checked items" reading (its other model pairs there are on all of the
    organization's items). A wrong rebuild of the neutral ids cannot reproduce them.
    """
    org = org_reading["org"]
    items = checked_items(report, org)
    by_neutral = dict(zip(neutral_ids(report), report["labels"].values(), strict=True))
    human_slips = {
        f: set(report.get("human_slips", {}).get(f, [])) & set(items)
        for f in ("label", "outside_names")
    }
    none: dict[str, set[str]] = {"label": set(), "outside_names": set()}
    models = sorted({r for i in items for r in by_neutral[i]} - {"human"})
    readers: dict[str, Reader] = {
        name: (
            {i: by_neutral[i][name]["label"] for i in items},
            {i: bool(by_neutral[i][name]["outside_names"]) for i in items},
            human_slips if name == "human" else none,
        )
        for name in ["human", *models]
    }
    pairs = reader_pairs(readers, list(readers))
    recorded = org_reading["pairs"]
    names = {n: n for n in pairs if n.startswith("human~") and n in recorded}
    names |= {
        f"{a}~{b}": f"{a}~{b} on checked items"
        for x, a in enumerate(models)
        for b in models[x + 1 :]
    }
    missing = [theirs for theirs in names.values() if theirs not in recorded]
    present = {mine: theirs for mine, theirs in names.items() if theirs in recorded}
    wrong = missing + differences(pairs, recorded, present)
    if wrong or not any(n.startswith("human~") for n in present):
        raise SystemExit(f"the {org} reading does not reproduce: " + "; ".join(wrong))
    return items, readers, pairs


def second_rater_report(report: dict, org_reading: dict, export: Path) -> dict:
    """The second raters' readings beside the report's own readers (`report_readers`)."""
    items, readers, pairs = report_readers(report, org_reading)
    example = dict(zip(neutral_ids(report), report["labels"], strict=True))
    known = list(readers)
    second = read_second_raters(export, items)
    for name, got in second.items():
        readers[name] = (
            {i: a["label"] for i, a in got.items()},
            {i: a["outside_names"] for i, a in got.items()},
            {f: {i for i, a in got.items() if f in a["slips"]} for f in ("label", "outside_names")},
        )
    order = [*known, *second]
    pairs |= reader_pairs(readers, order, already=frozenset(known))
    return {
        "org": org_reading["org"],
        "items": len(items),
        "rubric": RUBRIC_VERSION,
        "second_raters": {
            name: {
                "answered": len(got),
                "slips": {f: sorted(readers[name][2][f]) for f in ("label", "outside_names")},
            }
            for name, got in second.items()
        },
        # Keyed by example id, as the report keys its labels; slips stay by neutral id, as there.
        "labels": {
            name: {
                example[i]: {"label": readers[name][0][i], "outside_names": readers[name][1][i]}
                for i in sorted(readers[name][0])
            }
            for name in second
        },
        "valid_shares": {name: _share(readers[name][0]) for name in order if readers[name][0]},
        "pairs": pairs,
    }


def _nan_as_null(value):
    """NaN (an interval undefined on every resample) written as JSON null, which every reader
    parses."""
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, dict):
        return {k: _nan_as_null(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_nan_as_null(v) for v in value]
    return value


def _share(labels: dict[str, str]) -> dict:
    n, valid = len(labels), sum(v == "valid" for v in labels.values())
    return {"n": n, "valid": valid, "share": valid / n, "share_95": list(wilson_interval(valid, n))}


def _print_pairs(pairs: dict[str, dict]) -> None:
    for pair, stats in pairs.items():
        low, high = stats["kappa_95"]
        ac1_low, ac1_high = stats["ac1_95"]
        print(
            f"{pair}: {stats['items']} items, kappa {stats['kappa']:.3f} [{low:.3f}, {high:.3f}],"
            f" AC1 {stats['ac1']:.3f} [{ac1_low:.3f}, {ac1_high:.3f}]"
        )


def main() -> None:
    args = parser.parse_args()
    if args.second_raters:
        if not (args.from_report and args.org_reading):
            parser.error("--second-raters takes --from-report and --org-reading")
        reading = second_rater_report(
            json.loads(args.from_report.read_text()),
            json.loads(args.org_reading.read_text()),
            args.second_raters,
        )
        sources = {"from": relative(args.from_report), "org_reading": relative(args.org_reading)}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        body = {"provenance": provenance_header(), **sources, **reading}
        args.out.write_text(json.dumps(_nan_as_null(body), indent=2, allow_nan=False) + "\n")
        _print_pairs(reading["pairs"])
        print(f"wrote {args.out}")
        return
    if not (args.sheet and args.key and args.rater):
        parser.error("--sheet, --key and --rater are required")
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
    if args.human:
        human, human_flags = _labels(args.human)
        checked = sorted(set(human) - set(args.exclude))
        for i in checked:
            report["labels"][key[i]]["human"] = {"label": human[i], "outside_names": human_flags[i]}
        recorded = [
            f"{i}:{f}"
            for i, v in json.loads(args.human.read_text()).items()
            for f in v.get("slips", [])
        ]
        slips = _slips(args.slip + [r for r in recorded if r.split(":", 1)[0] in checked], checked)
        report["human_slips"] = {f: sorted(items) for f, items in slips.items() if items}
        report["pairs"] = readings(
            items,
            raters,
            flags,
            human=(human, human_flags),
            slips=slips,
            against=args.checked_against,
        )
        report["valid_rates"]["human"] = _valid_rates({i: human[i] for i in checked}, cell_of)
    else:
        report["pairs"] = readings(items, raters, flags)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    _print_pairs(report["pairs"])
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
