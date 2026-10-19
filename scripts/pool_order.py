"""A foreign pool's projects in the order item 4 of the H2 plan takes them, from listing snapshots.

Each project's count is its merged changes created inside the host's grant
(`cli.GrantScope`: from `created_from`, before `created_before`), read from the month snapshots a
`fetch` wrote under `--root`, each its month listed in the scope's day ranges (`cli.day_ranges`;
a range's halves where the host would have truncated it, `cli.range_halves`), once per granted
project, or under a scope of every project naming none (`GrantScope.every_project`) (research
log, 2026-10-18 and 2026-10-19). Projects are
ranked by that count, most first, ties by name. A month lists the changes last updated in it, so
a change updated after the grant's last month is in no listing. Beside it, as feasibility only,
the changes whose snapshot records an inline comment (`cli.uncommented` reads the rest as
uncommented), the changes a `build --fewer-requests` would ask about. Reads listings only.

    uv run --no-sync --no-active python scripts/pool_order.py --org aosp \\
        --root ~/ajsoftworks/sphragis-data-local/aosp/listing \\
        --out datasets/results/pool-order-aosp.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from sphragis.corpus.cli import (
    GrantScope,
    covers_change,
    created_in_grant,
    day_ranges,
    grant_months,
    grant_scope,
    month_listing,
    range_halves,
    uncommented,
)
from sphragis.corpus.storage import iter_snapshot, read_snapshot_record
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--org", required=True)
parser.add_argument("--root", type=Path, required=True, help="holds <org>/raw/<month>.ndjson.gz")
parser.add_argument("--out", type=Path, required=True)


def main() -> None:
    args = parser.parse_args()
    scope = grant_scope(args.org)
    if scope is None:
        raise SystemExit(f"{args.org}'s host is read under no scoped grant; there is no pool")
    snapshots = sorted((args.root / args.org / "raw").glob("*.ndjson.gz"))
    months = sorted(s.name.removesuffix(".ndjson.gz") for s in snapshots)
    wanted = _months(scope)
    if months != wanted:
        raise SystemExit(f"the listing holds months {months}, the grant {wanted}")
    merged, commented, seen, repeated = Counter(), Counter(), set(), 0
    for snapshot in snapshots:
        record = read_snapshot_record(snapshot) or {}
        month = snapshot.name.removesuffix(".ndjson.gz")
        queries = _queries(record)
        asked = [month_listing(q) for q in queries]
        ranges: defaultdict[str | None, Counter[tuple[str, str]]] = defaultdict(Counter)
        for listing in asked:
            if listing is None:
                raise SystemExit(f"{snapshot.name} is not its month's listing: {queries}")
            ranges[listing[2]][listing[:2]] += 1
        if not ranges:
            raise SystemExit(f"{snapshot.name} is not its month's listing: {queries}")
        listed = sorted(ranges, key=str)
        if scope.every_project:
            if listed != [None]:
                raise SystemExit(
                    f"{snapshot.name} names {[p for p in listed if p]}: the ranking listings "
                    "name no project"
                )
        elif listed != sorted(scope.projects):
            raise SystemExit(
                f"{snapshot.name} lists {len(listed)} projects, the grant names "
                f"{len(scope.projects)}"
            )
        registered = day_ranges(month, scope.listing_days)
        halve = scope.listing_days is not None
        for project, spans in ranges.items():
            tiled = all(_tiled(span, spans, halve=halve) for span in registered)
            if not tiled or any(spans.values()):
                of = f" of {project}" if project else ""
                raise SystemExit(f"{snapshot.name} is not its month's listing{of}: {queries}")
        for change in iter_snapshot(snapshot):
            if change["_number"] in seen:
                # Updated into a later month between the two months' fetches: counted once.
                repeated += 1
                continue
            seen.add(change["_number"])
            if not created_in_grant(scope, change.get("created")) or not covers_change(
                scope, change
            ):
                raise SystemExit(
                    f"{snapshot.name}: change {change['_number']} is outside the grant"
                )
            merged[change["project"]] += 1
            commented[change["project"]] += not uncommented(change)
    # Every project the grant names, or under a scope of every project, each the listings return.
    projects = merged if scope.every_project else scope.projects
    ranked = sorted(projects, key=lambda p: (-merged[p], p))
    report = {
        "org": args.org,
        "root": str(args.root),
        "grant": scope._asdict(),
        "months": months,
        "changes": sum(merged.values()),
        "listed_twice": repeated,
        "order": [{"project": p, "merged": merged[p], "commented": commented[p]} for p in ranked],
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{args.org}: {len(ranked)} projects, {report['changes']} changes; wrote {args.out}")


def _months(scope: GrantScope) -> list[str]:
    """Every month a fetch under `scope` may write, in order."""
    year, month = map(int, scope.created_from[:7].split("-"))
    out = []
    while grant_months(scope, f"{year:04d}-{month:02d}"):
        out.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def _tiled(span: tuple[str, str], spans: Counter[tuple[str, str]], *, halve: bool) -> bool:
    """Whether `spans` lists `span`, or with `halve` its two halves (`range_halves`), each in
    turn, down to one day; each listing so matched is taken from `spans`."""
    if spans[span] > 0:
        spans[span] -= 1
        return True
    halves = range_halves(*span) if halve else None
    return halves is not None and all(_tiled(half, spans, halve=True) for half in halves)


def _queries(record: dict) -> list[str]:
    return [r["query"] for r in record.get("listings", [record]) if "query" in r]


if __name__ == "__main__":
    main()
