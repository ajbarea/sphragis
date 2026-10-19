"""Whether a host's `total_comment_count` of 0 means a row has no comments, asked of the host.

`build --fewer-requests` skips every snapshot row the build's own `cli.uncommented` reads as
having no comments, month by month. Before a host's build may use it, this asks the host for the
comments of a seeded sample of those rows, spread over the months in proportion to each month's
zero rows so the pooled sample stands for the whole skip, and of a few rows above zero from every
month as a control the probe can fail on, over the host's own terms (`http_transport`: its
`REST_PERMITTED` pacing and client claim). A change recorded at zero in one month and above zero
later is sampled as its zero row, the case the skip could lose. A month whose snapshot is refused
or damaged is skipped, as the build skips it; a month with fewer rows than asked is asked in full
and its shortfall recorded. A failed request is recorded with its error, not taken as an answer.
Answers are kept in `<out>.partial.jsonl` as they arrive, so an interrupted probe resumes. Only
counts are written, no comment text, and the digest of the raw snapshots asked from, which the
build's gate compares with its own.

The probe holds when every zero row asked returned no comment, every control returned at least
one, and no request failed. With n zero rows all empty, the 95% upper bound on the share of skipped
rows that carry comments is 1 - 0.05^(1/n) (proportional allocation makes the pooled sample at
least as precise as a simple random one); it is written only when no zero row returned comments.

    uv run --no-sync --no-active python scripts/comment_count_probe.py --org qt \\
        --root datasets/gerrit-granted --out datasets/results/comment-count-probe-qt.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from sphragis.corpus.cli import (
    GERRIT,
    Admitted,
    admit,
    grant_scope,
    host_of,
    http_transport,
    outside_grant,
    rest_rows,
    uncommented,
)
from sphragis.corpus.gerrit import fetch_comments
from sphragis.corpus.load import raw_digest
from sphragis.corpus.storage import iter_snapshot, refused_snapshot
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--org", required=True)
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--zero", type=int, default=300, help="zero-count rows asked over all months")
parser.add_argument("--control", type=int, default=3, help="rows above zero asked per month")
parser.add_argument("--seed", type=int, default=20261015)
parser.add_argument("--out", type=Path, required=True)


def recorded(
    root: Path, org: str, admitted: Admitted | None = None
) -> tuple[dict[str, tuple[list[int], list[int]]], dict[str, str]]:
    """Each month's change numbers whose row the build would skip, and those it would ask, each
    month's REST rows admitted to its host's transport (`cli.admit`) as the build admits them."""
    by_month: dict[str, tuple[list[int], list[int]]] = {}
    skipped: dict[str, str] = {}
    host, scope = host_of(org), grant_scope(org)
    for snapshot in sorted((root / org / "raw").glob("*.ndjson.gz")):
        month = snapshot.name.removesuffix(".ndjson.gz")
        reason = refused_snapshot(snapshot)
        if reason is not None:
            skipped[month] = reason
            continue
        zero: set[int] = set()
        some: set[int] = set()
        try:
            changes = list(iter_snapshot(snapshot))
            # Asked by number, as the build asks: a month the host's grant does not cover gives
            # no change to ask.
            rest = rest_rows(changes)
            why = outside_grant(scope, month, rest)
            if why:
                skipped[month] = why
                continue
            if admitted is not None:
                admit(admitted, host, rest)
            for change in changes:
                count = change.get("total_comment_count")
                if uncommented(change):
                    zero.add(int(change["_number"]))
                elif isinstance(count, int) and count > 0:
                    some.add(int(change["_number"]))
        except ValueError as error:
            skipped[month] = str(error)
            continue
        by_month[month] = (sorted(zero), sorted(some))
    return by_month, skipped


def proportional(total: int, sizes: dict[str, int]) -> dict[str, int]:
    """`total` draws spread over strata in proportion to their sizes, by largest remainder."""
    whole = sum(sizes.values())
    if whole == 0:
        return dict.fromkeys(sizes, 0)
    exact = {k: total * n / whole for k, n in sizes.items()}
    shares = {k: int(v) for k, v in exact.items()}
    for k in sorted(exact, key=lambda k: exact[k] - shares[k], reverse=True)[
        : total - sum(shares.values())
    ]:
        shares[k] += 1
    return {k: min(v, sizes[k]) for k, v in shares.items()}


def _partial(out: Path) -> Path:
    return out.with_name(out.name + ".partial.jsonl")


def ask(
    org: str, asked: list[tuple[str, int]], out: Path, admitted: Admitted | None = None
) -> dict[str, dict]:
    """Each (group, change) asked's comment count or error, resumed from and kept in the partial."""
    partial = _partial(out)
    answers: dict[str, dict] = {}
    if partial.is_file():
        for line in partial.read_text().splitlines():
            row = json.loads(line)
            answers[f"{row['group']}:{row['change']}"] = row
    # The host's own REST_PERMITTED pace and client claim, and only the admitted changes.
    transport = http_transport(admitted=admitted)
    with partial.open("a") as kept:
        for group, number in asked:
            key = f"{group}:{number}"
            if key in answers:
                continue
            row: dict = {"group": group, "change": number}
            try:
                comments = fetch_comments(GERRIT[org], number, transport=transport)
                row["comments"] = sum(len(v) for v in comments.values())
            except (RuntimeError, ValueError, OSError) as error:
                row["error"] = f"{type(error).__name__}: {error}"
            answers[key] = row
            kept.write(json.dumps(row) + "\n")
            kept.flush()
    return answers


def main() -> None:
    args = parser.parse_args()
    if args.org not in GERRIT:
        raise SystemExit(f"{args.org} has no REST host to ask")
    admitted: Admitted = {}
    by_month, skipped = recorded(args.root, args.org, admitted)
    rng = random.Random(args.seed)
    asked: list[tuple[str, int]] = []
    shortfall: dict[str, dict[str, int]] = {}
    allocation = proportional(args.zero, {m: len(z) for m, (z, _) in by_month.items()})
    for month, (zero, some) in sorted(by_month.items()):
        for group, rows, want in (
            ("zero", zero, allocation[month]),
            ("control", some, args.control),
        ):
            take = min(len(rows), want)
            if take < want:
                shortfall.setdefault(month, {})[group] = want - take
            asked += [(f"{group}@{month}", n) for n in rng.sample(rows, take)]
    if not any(g.startswith("control") for g, _ in asked):
        raise SystemExit("no control asked: the probe could not fail")
    answers = ask(args.org, asked, args.out, admitted)
    rows = [answers[f"{g}:{n}"] for g, n in asked]
    failed = sorted({r["change"] for r in rows if "error" in r})
    zero_with = sorted(
        {r["change"] for r in rows if r["group"].startswith("zero") and r.get("comments")}
    )
    control_without = sorted(
        {r["change"] for r in rows if r["group"].startswith("control") and r.get("comments") == 0}
    )
    answered_zero = sum(r["group"].startswith("zero") and "error" not in r for r in rows)
    holds = not zero_with and not control_without and not failed
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "provenance": provenance_header(),
                "org": args.org,
                "raw_digest": raw_digest(args.root, args.org),
                "seed": args.seed,
                "asked": {"zero": args.zero, "control_per_month": args.control},
                "recorded": {
                    m: {"zero": len(z), "above_zero": len(s)} for m, (z, s) in by_month.items()
                },
                "skipped_months": skipped,
                "shortfall": shortfall,
                "answers": rows,
                "answered_zero": answered_zero,
                "failed": failed,
                "zero_with_comments": zero_with,
                "control_without_comments": control_without,
                "upper_95_share_with_comments": (
                    1 - 0.05 ** (1 / answered_zero) if answered_zero and not zero_with else None
                ),
                "holds": holds,
            },
            indent=2,
        )
        + "\n"
    )
    _partial(args.out).unlink(missing_ok=True)
    print(f"{args.org}: zero with comments {zero_with}, empty controls {control_without}")
    print(f"failed {failed}; upper 95% share with comments over {answered_zero} zero rows")
    print("COUNT_HOLDS" if holds else "COUNT_DOES_NOT_HOLD")


if __name__ == "__main__":
    main()
