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
from sphragis.corpus.halves import excluded_projects, halves, runner_train, split_criteria
from sphragis.corpus.load import refined_examples
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, default=Path("datasets/gerrit"))
parser.add_argument("--org", required=True)
parser.add_argument("--reference", default="openstack")
parser.add_argument("--reference-root", type=Path, default=None, help="default: --root")
parser.add_argument("--out", type=Path, default=None)
parser.add_argument(
    "--size-floor",
    type=int,
    default=None,
    help="the repeated-partition design's fixed training size N, in place of the reference's "
    "smaller half",
)
parser.add_argument(
    "--dedup-org",
    action="store_true",
    help="build --org's halves as the repeated-partition design does, deduplicating the "
    "organization first; the reference stays registered",
)
parser.add_argument(
    "--order-seed",
    type=int,
    default=None,
    help="check an alternative balanced partition of --org; the reference stays registered",
)


def org_halves(
    root: Path, org: str, order_seed: int | None = None, dedup_org: bool = False
) -> tuple[list[dict], list[str]]:
    rows = refined_examples(root, org)
    if not rows:
        raise SystemExit(f"{org}: no refined examples under {root / org}")
    if dedup_org:
        # As `placebo_corpus.py --dedup-org` builds the halves: the organization deduplicated
        # first, then assigned on its training-window counts, then each half its own dedup.
        from sphragis.corpus.pipeline import run_dedup

        rows = run_dedup(rows)[0]
    window = WINDOWS["train"]
    train = runner_train(rows, WINDOWS, order_seed)
    return halves(rows, train, window, order_seed), excluded_projects(rows, window)


def main() -> None:
    args = parser.parse_args()
    own, excluded = org_halves(args.root, args.org, args.order_seed, args.dedup_org)
    ref, _ = org_halves(args.reference_root or args.root, args.reference)
    result = {"excluded_projects": excluded, **split_criteria(own, ref, size_floor=args.size_floor)}
    for name, half in zip(("a", "b"), result["halves"], strict=True):
        print(
            f"{args.org}-{name}: {half['projects']} projects, {half['train_examples']} train, "
            f"largest {half['largest']} at {half['largest_share']:.3f}, "
            f"{half['target_in_comment']} with the target typed into a comment"
        )
    print(
        f"suffix TV {result['suffix_total_variation']} "
        f"(ceiling {result['suffix_total_variation_ceiling']:.4f}), "
        f"{len(excluded)} projects excluded before the split, "
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
