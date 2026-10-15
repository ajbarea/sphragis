"""The label audit read again on one organization's items, from the committed agreement report.

`label_audit_agreement.py` pools every audited item. The audit sampled OpenStack and Qt alike,
and Qt's review records came from a host whose permission was still pending, so no published
figure rests on them. This reading keeps one organization's items and recomputes every pair the
pooled report reads (`label_audit_agreement.readings`), slips and the halves of the human check
included; each reader's valid share is the report's own per-organization rate.

The report keys its labels by example id, in the order of its sorted neutral item ids, and keys
slips by neutral id. The neutral ids are rebuilt from that order (`item-001` upward, the excluded
ones left out), and the whole pooled reading is recomputed from them and refused unless every pair
reproduces the report, which a wrong rebuild of the ids cannot do.

    uv run --no-sync --no-active python scripts/label_audit_by_org.py \\
        --report datasets/results/label-audit-v2.json --org openstack \\
        --out datasets/results/label-audit-v2-openstack.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from label_audit_agreement import readings  # noqa: E402

from sphragis.provenance import provenance_header  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--report", type=Path, required=True, help="label_audit_agreement.py's output")
parser.add_argument("--org", required=True)
parser.add_argument("--out", type=Path, required=True)


def neutral_ids(report: dict) -> list[str]:
    """The neutral id of each label, in the report's order: the sorted ids less the excluded."""
    excluded = set(report["excluded"])
    total = len(report["labels"]) + len(excluded)
    ids = sorted(set(f"item-{n:03d}" for n in range(1, total + 1)) - excluded)
    if len(ids) != len(report["labels"]):
        raise ValueError("the excluded ids are not among item-001 upward")
    return ids


def inputs(report: dict, keep: set[str] | None = None) -> dict:
    """`readings`' arguments from the report, on the labels whose example ids are in `keep`."""
    by_neutral = dict(zip(neutral_ids(report), report["labels"].items(), strict=True))
    chosen = {n: by for n, (eid, by) in by_neutral.items() if keep is None or eid in keep}
    raters = sorted({r for by in chosen.values() for r in by} - {"human"})
    items = sorted(chosen)
    human = {n: by["human"] for n, by in chosen.items() if "human" in by}
    slips = {f: set(report.get("human_slips", {}).get(f, [])) for f in ("label", "outside_names")}
    return {
        "items": items,
        "raters": {r: {n: chosen[n][r]["label"] for n in items} for r in raters},
        "flags": {r: {n: bool(chosen[n][r]["outside_names"]) for n in items} for r in raters},
        "human": (
            (
                {n: h["label"] for n, h in human.items()},
                {n: bool(h["outside_names"]) for n, h in human.items()},
            )
            if human
            else None
        ),
        "slips": slips,
    }


def read(report: dict, keep: set[str] | None = None, *, against: str) -> dict[str, dict]:
    given = inputs(report, keep)
    return readings(
        given["items"],
        given["raters"],
        given["flags"],
        human=given["human"],
        slips=given["slips"],
        against=against,
    )


def reproduces(recomputed: dict, recorded: dict) -> list[str]:
    """Every pair the recomputation and the report do not share, or do not agree on."""
    wrong = [f"{name}: only one side has it" for name in set(recomputed) ^ set(recorded)]
    for name in sorted(set(recomputed) & set(recorded)):
        for field in ("items", "raw_agreement", "kappa", "kappa_95", "ac1", "ac1_95"):
            ours, theirs = recomputed[name][field], recorded[name][field]
            pairs = zip(ours, theirs, strict=True) if isinstance(ours, list) else [(ours, theirs)]
            if not all(math.isclose(x, y, abs_tol=1e-12) for x, y in pairs):
                wrong.append(f"{name}.{field}: {ours} against {theirs}")
    return wrong


def _relative(path: Path) -> str:
    resolved = path.resolve()
    return str(resolved.relative_to(ROOT)) if resolved.is_relative_to(ROOT) else str(path)


def main() -> None:
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    # The checked rater is the one whose halves the report reads.
    halves = [p for p in report["pairs"] if p.endswith(" first half")]
    if len(halves) != 1:
        raise SystemExit(f"{args.report} reads {len(halves)} checked raters' halves, not one")
    against = halves[0].removeprefix("human~").removesuffix(" first half")
    try:
        wrong = reproduces(read(report, against=against), report["pairs"])
        if wrong:
            raise ValueError("the pooled reading does not reproduce: " + "; ".join(wrong))
        mine = {eid for eid in report["labels"] if eid.split(":", 1)[0] == args.org}
        if not mine:
            raise ValueError(f"{args.report} holds no {args.org} items")
        if not any("human" in report["labels"][eid] for eid in mine):
            raise ValueError(f"the human checked none of {args.org}'s items")
        pairs = read(report, mine, against=against)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "provenance": provenance_header(),
                "from": _relative(args.report),
                "org": args.org,
                "items": len(mine),
                "pairs": pairs,
                "valid_shares": {r: rates[args.org] for r, rates in report["valid_rates"].items()},
            },
            indent=2,
        )
        + "\n"
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
