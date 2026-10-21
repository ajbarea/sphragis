"""Every patch set's parents, for the relation chains, from the host's listing with commits.

Registered 2026-10-21 (research log, "Relation chains amended"): each raw month's recorded
listing queries are replayed with every revision's commit (`o=ALL_COMMITS`) through the fetch's
own transport, grants and pacing, and only each patch set's SHA and its parents' SHAs are kept,
never a commit message or a name. One file per month under `--out`, written whole; a month already
written is skipped, so a stopped run resumes.

    uv run --no-sync --no-active python scripts/fetch_parents.py \\
        --root ~/ajsoftworks/sphragis-data-local/reference --org openstack \\
        --out ~/ajsoftworks/sphragis-data-local/parents/openstack
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from sphragis.corpus.cli import GERRIT, http_transport
from sphragis.corpus.gerrit import fetch_changes
from sphragis.corpus.storage import read_snapshot_record
from sphragis.durable import write_json_atomic

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True, help="the corpus whose raw months to read")
parser.add_argument("--org", required=True, choices=sorted(GERRIT))
parser.add_argument("--out", type=Path, required=True, help="where each month's parents go")
parser.add_argument("--request-interval", type=float, default=2.0)


def recorded_queries(record: Mapping[str, Any]) -> list[str]:
    """The listing queries a raw month was fetched with, in order; refused if one names none."""
    listings = record.get("listings", [record])
    if any("query" not in listing for listing in listings):
        raise SystemExit(f"a recorded listing names no query: {listings}")
    return [listing["query"] for listing in listings]


def patch_set_parents(changes: Iterable[Mapping[str, Any]]) -> dict[str, list[str]]:
    """Each revision's SHA and its parents' SHAs; everything else in the listing is dropped."""
    out: dict[str, list[str]] = {}
    for change in changes:
        for sha, revision in (change.get("revisions") or {}).items():
            commit = revision.get("commit") or {}
            out[sha] = [str(parent["commit"]) for parent in commit.get("parents") or []]
    return out


def main() -> None:
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    transport = http_transport(min_interval=args.request_interval)
    for path in sorted((args.root / args.org / "raw").glob("*.ndjson.gz")):
        month = path.name.removesuffix(".ndjson.gz")
        target = args.out / f"{month}.json"
        if target.is_file():
            continue
        record = read_snapshot_record(path)
        if record is None:
            raise SystemExit(f"{path}: no record of how it was fetched")
        parents: dict[str, list[str]] = {}
        for query in recorded_queries(record):
            changes, _ = fetch_changes(
                GERRIT[args.org],
                query,
                transport=transport,
                options=("ALL_REVISIONS", "ALL_COMMITS"),
            )
            parents.update(patch_set_parents(changes))
        write_json_atomic(target, {"org": args.org, "month": month, "parents": parents})
        print(f"{args.org} {month}: {len(parents)} patch sets", flush=True)


if __name__ == "__main__":
    main()
