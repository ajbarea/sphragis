"""Whether an organization's registered split qualifies for a confirmatory H1 cell.

Checks the halves `sphragis.corpus.halves` builds against OpenStack's: at least three projects a
half, no project above half of its half's training examples, each half at least OpenStack's
smaller half, and a file-suffix mix between the halves no further apart (total variation) than
OpenStack's halves are. Run once the organization's train window is frozen, before any contrast.

    uv run --no-sync python scripts/split_criteria.py --root datasets/gerrit --org wikimedia \
        --out datasets/results/split-criteria-wikimedia.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sphragis.corpus.cli import WINDOWS
from sphragis.corpus.halves import assign, halves, project_counts, split_criteria
from sphragis.corpus.load import refined_examples
from sphragis.corpus.pipeline import run_dedup, run_split
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, default=Path("datasets/gerrit"))
parser.add_argument("--org", required=True)
parser.add_argument("--reference", default="openstack")
parser.add_argument("--reference-root", type=Path, default=None, help="default: --root")
parser.add_argument("--out", type=Path, default=None)


def org_halves(root: Path, org: str) -> list[dict]:
    """Each half deduplicated and split on its own, as the runner reads a placebo half."""
    rows = refined_examples(root, org)
    if not rows:
        raise SystemExit(f"{org}: no refined examples under {root / org}")
    side_of = assign(dict(project_counts(rows, WINDOWS["train"])))
    train = []
    for side in (0, 1):
        kept, _ = run_dedup([row for row in rows if side_of.get(row["project"]) == side])
        windows, _, _ = run_split(kept, WINDOWS)
        train.extend(windows["train"])
    return halves(rows, train, WINDOWS["train"])


def main() -> None:
    args = parser.parse_args()
    result = split_criteria(
        org_halves(args.root, args.org),
        org_halves(args.reference_root or args.root, args.reference),
    )
    for name, half in zip(("a", "b"), result["halves"], strict=True):
        print(
            f"{args.org}-{name}: {half['projects']} projects, {half['train_examples']} train, "
            f"largest {half['largest']} at {half['largest_share']:.3f}"
        )
    print(
        f"suffix TV {result['suffix_total_variation']:.4f} "
        f"(ceiling {result['suffix_total_variation_ceiling']:.4f}), "
        f"size floor {result['size_floor']}"
    )
    print(json.dumps(result["checks"]), "qualifies" if result["qualifies"] else "does not qualify")
    if args.out:
        args.out.write_text(
            json.dumps(
                {
                    "org": args.org,
                    "reference": args.reference,
                    **result,
                    "provenance": provenance_header(),
                },
                indent=2,
            )
        )
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
