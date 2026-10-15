"""Whether a host's `total_comment_count` of 0 means a change has no comments, asked of the host.

`build --fewer-requests` skips a change whose snapshot records no comment. Before a host's build
relies on that, this asks the host for the comments of a seeded sample of changes its snapshots
record at zero, and of a smaller sample recorded above zero as a control the probe can fail on,
over the host's own terms (`http_transport`, its `REST_PERMITTED` pacing and client claim). Only
counts are written: which changes were asked and how many comments each returned, no text.

    uv run --no-sync --no-active python scripts/comment_count_probe.py --org qt \\
        --root datasets/gerrit-granted --out datasets/results/comment-count-probe-qt.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from sphragis.corpus.cli import GERRIT, http_transport
from sphragis.corpus.gerrit import fetch_comments
from sphragis.corpus.storage import iter_snapshot
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--org", required=True)
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--zero", type=int, default=60, help="changes recorded at zero to ask")
parser.add_argument("--control", type=int, default=10, help="changes recorded above zero to ask")
parser.add_argument("--seed", type=int, default=20261015)
parser.add_argument("--out", type=Path, required=True)


def recorded(root: Path, org: str) -> tuple[list[int], list[int]]:
    """Every fetched change number, split by whether its snapshot records zero comments."""
    zero, some = [], []
    for snapshot in sorted((root / org / "raw").glob("*.ndjson.gz")):
        for change in iter_snapshot(snapshot):
            count = change.get("total_comment_count")
            if count == 0:
                zero.append(int(change["_number"]))
            elif isinstance(count, int) and count > 0:
                some.append(int(change["_number"]))
    return zero, some


def main() -> None:
    args = parser.parse_args()
    zero, some = recorded(args.root, args.org)
    rng = random.Random(args.seed)
    asked = {"zero": rng.sample(zero, args.zero), "control": rng.sample(some, args.control)}
    transport = http_transport(min_interval=2.0)
    answers = {
        group: {
            str(n): sum(
                len(v) for v in fetch_comments(GERRIT[args.org], n, transport=transport).values()
            )
            for n in numbers
        }
        for group, numbers in asked.items()
    }
    nonempty_zero = sorted(n for n, c in answers["zero"].items() if c)
    empty_control = sorted(n for n, c in answers["control"].items() if not c)
    holds = not nonempty_zero and not empty_control
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "provenance": provenance_header(),
                "org": args.org,
                "recorded": {"zero": len(zero), "above_zero": len(some)},
                "seed": args.seed,
                "comments_returned": answers,
                "zero_with_comments": nonempty_zero,
                "control_without_comments": empty_control,
                "holds": holds,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"{args.org}: zero-count with comments {nonempty_zero}, empty controls {empty_control}")
    print("COUNT_HOLDS" if holds else "COUNT_DOES_NOT_HOLD")


if __name__ == "__main__":
    main()
