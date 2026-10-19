"""A foreign pool's projects in the order item 4 of the H2 plan takes them, from listing snapshots.

Each project's count is its merged changes created inside the host's grant
(`cli.GrantScope`: from `created_from`, before `created_before`), read from the month snapshots a
`fetch` wrote under `--root`, each its month of every granted project, or of every project listed
whole (`GrantScope.every_project`) (research log, 2026-10-18 and 2026-10-19). Projects are
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
from collections import Counter
from pathlib import Path

from sphragis.corpus.cli import (
    GrantScope,
    covers_change,
    created_in_grant,
    grant_months,
    grant_scope,
    month_listing,
    next_month,
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
        asked = [month_listing(q) for q in _queries(record)]
        whole_month = (f"{month}-01", f"{next_month(month)}-01")
        if not asked or any(a is None or a[:2] != whole_month for a in asked):
            raise SystemExit(f"{snapshot.name} is not its month's listing: {_queries(record)}")
        listed = [a[2] for a in asked if a is not None]
        if scope.every_project:
            if listed != [None]:
                raise SystemExit(f"{snapshot.name} is not its month listed whole: {listed}")
        elif sorted(listed, key=str) != sorted(scope.projects):
            raise SystemExit(
                f"{snapshot.name} lists {len(listed)} projects, the grant names "
                f"{len(scope.projects)}"
            )
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


def _queries(record: dict) -> list[str]:
    return [r["query"] for r in record.get("listings", [record]) if "query" in r]


if __name__ == "__main__":
    main()
