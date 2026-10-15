"""The label audit read again on one organization's items, from the committed agreement report.

`label_audit_agreement.py` pools every audited item. The audit sampled OpenStack and Qt alike,
and Qt's review records came from a host whose permission is still pending, so no published
figure may rest on them. This reading keeps one organization's items and recomputes, with the
same functions and seeds, the locked-label pairs on the five labels and on valid against the rest,
the model raters again on the human's items, and each reader's valid share. It does not reread
slips or the halves of the human check: the report keys them by neutral item id, and the key to
example ids stays with the sheet, uncommitted.

The report's `labels` are keyed by example id, whose prefix is the organization, in the order the
pooled reading took its items, so the pooled pairs this reading computes are first recomputed from
them and the reading is refused unless they reproduce the report.

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
from label_audit_agreement import _is_valid, _pair, _valid_rates  # noqa: E402
from label_audit_sample import LABELS  # noqa: E402

from sphragis.provenance import provenance_header  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--report", type=Path, required=True, help="label_audit_agreement.py's output")
parser.add_argument("--org", required=True)
parser.add_argument("--out", type=Path, required=True)

#: The pairs the pooled report reads on locked labels, every item each pair shares.
PAIRS = (("A", "B"), ("human", "A"), ("human", "B"))


def _read(labels: dict[str, dict], first: str, second: str, items: list[str]) -> dict[str, dict]:
    """One pair on the five labels and on valid against the rest, under the report's names."""
    a = {i: labels[i][first]["label"] for i in items}
    b = {i: labels[i][second]["label"] for i in items}
    return {
        "": _pair(a, b, items, list(LABELS)),
        " valid_vs_rest": _pair(_is_valid(a), _is_valid(b), items, [True, False]),
    }


def pairs(labels: dict[str, dict]) -> dict[str, dict]:
    """Each pair on the items both readers labelled, in the report's item order, and the model
    raters again on the human's items, the comparator a human-to-model figure needs."""
    checked = [i for i, by in labels.items() if "human" in by]
    if not checked:
        raise ValueError("no item carries a human label, so no human pair can be read")
    out = {}
    for first, second in PAIRS:
        items = [i for i, by in labels.items() if first in by and second in by]
        for suffix, pair in _read(labels, first, second, items).items():
            out[f"{first}~{second}{suffix}"] = pair
    out["A~B on checked items"] = _read(labels, "A", "B", checked)[""]
    return out


def valid_shares(labels: dict[str, dict], org: str) -> dict[str, dict]:
    """Each reader's valid share on the organization's items, as the pooled report computes it."""
    readers = sorted({reader for by in labels.values() for reader in by})
    return {
        reader: _valid_rates(
            {i: by[reader]["label"] for i, by in labels.items() if reader in by},
            {i: (org, org) for i in labels},
        )[org]
        for reader in readers
    }


def reproduces(recomputed: dict, recorded: dict) -> list[str]:
    """The fields of the pooled pairs the recomputation does not reproduce."""
    wrong = []
    for name, pair in recomputed.items():
        if name not in recorded:
            wrong.append(f"{name}: not in the report")
            continue
        for field in ("items", "raw_agreement", "kappa", "kappa_95", "ac1", "ac1_95"):
            ours, theirs = pair[field], recorded[name][field]
            same = (
                all(math.isclose(x, y, abs_tol=1e-12) for x, y in zip(ours, theirs, strict=True))
                if isinstance(ours, list)
                else math.isclose(ours, theirs, abs_tol=1e-12)
            )
            if not same:
                wrong.append(f"{name}.{field}: {ours} against {theirs}")
    return wrong


def main() -> None:
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    labels: dict[str, dict] = report["labels"]
    try:
        pooled = pairs(labels)
        mine = {i: by for i, by in labels.items() if i.split(":", 1)[0] == args.org}
        if not mine:
            raise ValueError(f"{args.report} holds no {args.org} items")
        read = pairs(mine)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    wrong = reproduces(pooled, report["pairs"])
    if wrong:
        raise SystemExit("the pooled reading does not reproduce: " + "; ".join(wrong))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "provenance": provenance_header(),
                "from": str(args.report),
                "org": args.org,
                "items": len(mine),
                "pairs": read,
                "valid_shares": valid_shares(mine, args.org),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
