"""Whether a host's `total_comment_count` of 0 means a change has no comments, asked of the host.

`build --fewer-requests` skips a change the build's own `cli.uncommented` reads as having no
comments. Before a host's build may use it, this asks the host for the comments of a sample of
such changes, drawn per month with a seed, and of a smaller sample above zero as a control the
probe can fail on, over the host's own terms (`http_transport`: its `REST_PERMITTED` pacing and
client claim). Each change is asked once, under the month its latest row sits in; a month whose
snapshot is refused is skipped, as the build skips it. A request that fails is recorded with its
error, not taken as an answer. Only counts are written, no comment text.

The probe holds when every zero-count change answered returned no comment, every control answered
returned at least one, and no request failed. With n zero-count changes all empty, the 95% upper
bound on the share that carry comments is 1 - 0.05^(1/n), written beside the result.

    uv run --no-sync --no-active python scripts/comment_count_probe.py --org qt \\
        --root datasets/gerrit-granted --out datasets/results/comment-count-probe-qt.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from sphragis.corpus.cli import GERRIT, http_transport, uncommented
from sphragis.corpus.gerrit import fetch_comments
from sphragis.corpus.storage import iter_snapshot, refused_snapshot
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--org", required=True)
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--zero", type=int, default=25, help="zero-count changes asked per month")
parser.add_argument("--control", type=int, default=3, help="changes above zero asked per month")
parser.add_argument("--seed", type=int, default=20261015)
parser.add_argument("--out", type=Path, required=True)


def recorded(root: Path, org: str) -> tuple[dict[str, tuple[list[int], list[int]]], list[str]]:
    """Each month's changes the build would skip and those it would ask, each change once."""
    latest: dict[int, tuple[str, bool]] = {}
    refused = []
    for snapshot in sorted((root / org / "raw").glob("*.ndjson.gz")):
        month = snapshot.name.removesuffix(".ndjson.gz")
        if refused_snapshot(snapshot) is not None:
            refused.append(month)
            continue
        for change in iter_snapshot(snapshot):
            count = change.get("total_comment_count")
            if uncommented(change) or (isinstance(count, int) and count > 0):
                latest[int(change["_number"])] = (month, uncommented(change))
    by_month: dict[str, tuple[list[int], list[int]]] = {}
    for number, (month, zero) in sorted(latest.items()):
        by_month.setdefault(month, ([], []))[0 if zero else 1].append(number)
    return by_month, refused


def ask(org: str, numbers: list[int], transport) -> dict[str, dict]:
    """Each change's comment count, or the error its request ended in."""
    answers = {}
    for number in numbers:
        try:
            comments = fetch_comments(GERRIT[org], number, transport=transport)
            answers[str(number)] = {"comments": sum(len(v) for v in comments.values())}
        except (RuntimeError, ValueError, OSError) as error:
            answers[str(number)] = {"error": f"{type(error).__name__}: {error}"}
    return answers


def main() -> None:
    args = parser.parse_args()
    by_month, refused = recorded(args.root, args.org)
    rng = random.Random(args.seed)
    asked: dict[str, list[int]] = {"zero": [], "control": []}
    for month, (zero, some) in sorted(by_month.items()):
        if len(zero) < args.zero or len(some) < args.control:
            raise SystemExit(f"{args.org} {month}: {len(zero)} zero and {len(some)} above, too few")
        asked["zero"] += rng.sample(zero, args.zero)
        asked["control"] += rng.sample(some, args.control)
    if not asked["control"]:
        raise SystemExit("no control asked: the probe could not fail")
    transport = http_transport()  # the host's own REST_PERMITTED pace and client claim
    answers = {group: ask(args.org, numbers, transport) for group, numbers in asked.items()}
    failed = sorted((int(n) for g in answers.values() for n, a in g.items() if "error" in a))
    zero_with = sorted(int(n) for n, a in answers["zero"].items() if a.get("comments"))
    control_without = sorted(
        int(n) for n, a in answers["control"].items() if a.get("comments") == 0
    )
    answered_zero = sum("error" not in a for a in answers["zero"].values())
    holds = not zero_with and not control_without and not failed
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "provenance": provenance_header(),
                "org": args.org,
                "seed": args.seed,
                "per_month": {"zero": args.zero, "control": args.control},
                "recorded": {
                    m: {"zero": len(z), "above_zero": len(s)} for m, (z, s) in by_month.items()
                },
                "refused_months": refused,
                "answers": answers,
                "failed": failed,
                "zero_with_comments": zero_with,
                "control_without_comments": control_without,
                "upper_95_share_with_comments": 1 - 0.05 ** (1 / answered_zero)
                if answered_zero
                else None,
                "holds": holds,
            },
            indent=2,
        )
        + "\n"
    )
    print(
        f"{args.org}: zero with comments {zero_with}, empty controls {control_without}, "
        f"failed {failed}"
    )
    print("COUNT_HOLDS" if holds else "COUNT_DOES_NOT_HOLD")


if __name__ == "__main__":
    main()
