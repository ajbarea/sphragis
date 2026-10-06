"""The fixed training size N every adapter trains at, derived from the organization's own splits.

Splits are built as `admissible_partitions.py` builds them (the organization deduplicated once,
projects assigned in each seed's order on training-window counts), for seeds 1 to `--splits`,
and the smaller half's training-window count is read off each. N is that count's
`--percentile` (nearest rank), rounded down to a multiple of `--round-to`: the size a random
split's smaller half reaches with probability about 1 - percentile, so the size criterion
rejects few splits, and every adapter trains on the same number of examples. Reads training-window
rows alone and no outcome.

    uv run --no-sync --no-active python scripts/training_size.py \\
        --root datasets/gerrit --org openstack \\
        --out datasets/results/training-size-openstack.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from sphragis.corpus.halves import halves, organization_train
from sphragis.corpus.load import refined_examples
from sphragis.corpus.pipeline import run_dedup
from sphragis.corpus.windows import WINDOWS
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--org", required=True)
# 300 splits put the 5th percentile's nearest rank at the 15th smallest, stable to a few examples.
parser.add_argument("--splits", type=int, default=300)
# A choice: the size criterion then rejects about one random split in twenty.
parser.add_argument("--percentile", type=float, default=0.05)
# A round figure to register; rounding down keeps N at or below the percentile.
parser.add_argument("--round-to", type=int, default=50)
parser.add_argument("--out", type=Path, required=True)


def main() -> None:
    args = parser.parse_args()
    rows, _ = run_dedup(refined_examples(args.root, args.org))
    train = organization_train(rows, WINDOWS)
    smaller = []
    for seed in range(1, args.splits + 1):
        own = halves(rows, train, WINDOWS["train"], seed)
        smaller.append(min(h["train_examples"] for h in own))
    ordered = sorted(smaller)
    at_percentile = ordered[max(0, math.ceil(args.percentile * len(ordered)) - 1)]
    size = at_percentile // args.round_to * args.round_to
    report = {
        "org": args.org,
        "root": str(args.root.resolve()),
        "splits": args.splits,
        "percentile": args.percentile,
        "round_to": args.round_to,
        "smaller_half": {
            "min": ordered[0],
            "at_percentile": at_percentile,
            "median": ordered[len(ordered) // 2],
            "max": ordered[-1],
        },
        "training_size": size,
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"{args.org}: smaller half min {ordered[0]}, {args.percentile:.0%} {at_percentile}, "
        f"median {report['smaller_half']['median']}; N = {size}; wrote {args.out}"
    )


if __name__ == "__main__":
    main()
