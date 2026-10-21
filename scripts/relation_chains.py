"""Relation chains among an organization's merged changes, and how many refinements sit in one.

The registered reconstruction (research log, 2026-10-21, "Relation chains registered as H1's
cluster unit"; `sphragis.corpus.chains`): every merged change of the raw snapshots, linked to the
change its last patch set was parented on while that change was still in review, its parents read
from the bare clone the backport lookup made of its project (`--clones`, its source named in
`--lookup`). No host is asked. Per window, the examples whose change sits in a chain of two or
more changes, the chain sizes, and the map from each example's Change-Id to its chain, which the
bootstrap reads as its cluster. A change whose project has no clone, or whose last patch set its
clone lacks, is a chain of one, counted.

    uv run --no-sync --no-active python scripts/relation_chains.py \\
        --root ~/ajsoftworks/sphragis-data-local/reference --org openstack \\
        --clones ~/ajsoftworks/sphragis-data-local/lookup/clones \\
        --lookup datasets/results/backport-lookup-openstack.json \\
        --out datasets/results/relation-chains-openstack.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sphragis.corpus import chains, lookup
from sphragis.corpus.storage import read_snapshot
from sphragis.provenance import provenance_header

WINDOWS_READ = ["pilot", "train", "dev"]

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True, help="the corpus the partitions came from")
parser.add_argument("--org", required=True)
parser.add_argument("--clones", type=Path, required=True, help="the backport lookup's clones")
parser.add_argument("--lookup", type=Path, required=True, help="backport_lookup.py's artifact")
parser.add_argument("--out", type=Path, required=True)


def main() -> None:
    args = parser.parse_args()
    found = json.loads(args.lookup.read_text())
    if found.get("org") != args.org:
        raise SystemExit(f"{args.lookup} is not {args.org}'s lookup")
    rows = lookup.read_windows(args.root, args.org, WINDOWS_READ)
    projects = sorted({row["project"] for held in rows.values() for row in held})
    every = chains.merged(
        row
        for path in sorted((args.root / args.org / "raw").glob("*.ndjson.gz"))
        for row in read_snapshot(path)
    )
    changes = {change.number: change for change in every}  # a change listed twice is one
    by_project: dict[str, list[chains.Merged]] = defaultdict(list)
    for change in changes.values():
        by_project[change.project].append(change)
    cloner = lookup.Cloner(args.clones, user_agent="", claim=lambda host: None)
    parent_of: dict[str, list[str]] = {}
    no_clone = unread = 0
    for project in projects:
        source = (found["projects"].get(project) or {}).get("source")
        members = by_project.get(project, [])
        if not source or not (cloner.path(source) / "HEAD").is_file():
            no_clone += len(members)
            continue
        read = chains.parents(cloner.path(source), (c.commit for c in members))
        unread += sum(c.commit not in read for c in members)
        parent_of.update(read)
    linked = chains.links(list(changes.values()), parent_of)
    chain_of = chains.chains(list(changes.values()), linked)
    size = Counter(chain_of.values())
    # Every change sharing a Change-Id is in one chain (`chains.chains` joins them), so a
    # Change-Id names its chain; an example's Change-Id the raw snapshots lack is its own.
    root_of = {change.change_id: chain_of[change.number] for change in changes.values()}
    unmatched = sorted({row["change_id"] for held in rows.values() for row in held} - set(root_of))
    report: dict[str, Any] = {
        "org": args.org,
        "example_change_ids_not_in_the_snapshots": len(unmatched),
        "merged_changes": len(changes),
        "links": len(linked),
        "changes_without_a_clone": no_clone,
        "changes_whose_commit_the_clone_lacks": unread,
        "windows": {},
    }
    change_chain: dict[str, str] = {}
    for window, held in rows.items():
        keys = []
        for row in held:
            root = root_of.get(row["change_id"])
            key = f"chain-{root}" if root is not None else f"change-{row['change_id']}"
            change_chain[row["change_id"]] = key
            keys.append((key, size[root] if root is not None else 1))
        in_chain = sum(1 for _, n in keys if n >= 2)
        report["windows"][window] = {
            "examples": len(held),
            "in_a_chain": in_chain,
            "share": round(in_chain / len(held), 6) if held else None,
            "clusters_by_change": len({row["change_id"] for row in held}),
            "clusters_by_chain": len({key for key, _ in keys}),
            "chain_sizes": dict(sorted(Counter(n for _, n in set(keys)).items())),
        }
        print(window, report["windows"][window], flush=True)
    report["change_chain"] = dict(sorted(change_chain.items()))
    report["provenance"] = provenance_header()
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
