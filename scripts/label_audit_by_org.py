"""The label audit read again on one organization's items, from the committed agreement report.

`label_audit_agreement.py` pools every audited item. The audit sampled OpenStack and Qt alike,
and Qt's review records came from a host whose permission is still pending, so no published
figure may rest on them; this reading keeps one organization's items and recomputes, with the
same functions and seeds, every pair's agreement on the locked labels and each rater's valid
share. The report's `labels` are keyed by example id, whose prefix is the organization, in the
order the pooled reading took its items, so `--check` first recomputes the pooled pairs from
them and refuses unless they reproduce the report.

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
from label_audit_agreement import _pair  # noqa: E402
from label_audit_sample import LABELS  # noqa: E402

from sphragis.measure.agreement import wilson_interval  # noqa: E402
from sphragis.provenance import provenance_header  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--report", type=Path, required=True, help="label_audit_agreement.py's output")
parser.add_argument("--org", required=True)
parser.add_argument("--out", type=Path, required=True)

#: The pairs the pooled report reads on locked labels, every item each pair shares.
PAIRS = (("A", "B"), ("human", "A"), ("human", "B"))


def _read(labels: dict[str, dict], first: str, second: str, items: list[str]) -> dict:
    return _pair(
        {i: labels[i][first]["label"] for i in items},
        {i: labels[i][second]["label"] for i in items},
        items,
        list(LABELS),
    )


def pairs(labels: dict[str, dict]) -> dict[str, dict]:
    """Each pair's agreement on the items both readers labelled, in the report's item order, and
    the model raters again on the human's items, the comparator a human-to-model figure needs."""
    out = {
        f"{first}~{second}": _read(
            labels, first, second, [i for i, by in labels.items() if first in by and second in by]
        )
        for first, second in PAIRS
    }
    checked = [i for i, by in labels.items() if "human" in by]
    out["A~B on checked items"] = _read(labels, "A", "B", checked)
    return out


def valid_share(labels: dict[str, dict], reader: str) -> dict:
    """The share of the reader's labels that are valid, with its Wilson interval."""
    given = [by[reader]["label"] for by in labels.values() if reader in by]
    valid = sum(label == "valid" for label in given)
    low, high = wilson_interval(valid, len(given))
    return {"n": len(given), "valid": valid, "share": valid / len(given), "share_95": [low, high]}


def reproduces(recomputed: dict, recorded: dict) -> list[str]:
    """The fields of the pooled pairs the recomputation does not reproduce."""
    wrong = []
    for name, pair in recomputed.items():
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
    wrong = reproduces(pairs(labels), report["pairs"])
    if wrong:
        raise SystemExit("the pooled reading does not reproduce: " + "; ".join(wrong))
    mine = {i: by for i, by in labels.items() if i.split(":", 1)[0] == args.org}
    if not mine:
        raise SystemExit(f"{args.report} holds no {args.org} items")
    readers = sorted({reader for by in mine.values() for reader in by})
    args.out.write_text(
        json.dumps(
            {
                "provenance": provenance_header(),
                "from": str(args.report),
                "org": args.org,
                "items": len(mine),
                "pairs": pairs(mine),
                "valid_shares": {reader: valid_share(mine, reader) for reader in readers},
            },
            indent=2,
        )
        + "\n"
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
