"""Gerrit relation chains, rebuilt from merged changes and their projects' local clones.

Registered 2026-10-21 and amended the same day before any reading (research log, "Relation chains
registered as H1's cluster unit" and "Relation chains amended"). Change A is linked to change B when
the parent of A's last authored patch set (its last patch set that is not a trivial rebase, a
no-code change or no change; its first if none) is any patch set of B, in the same project, and B
was not yet merged when that patch set of A was uploaded. Parents come from the host's listing with
every revision's commit (`scripts/fetch_parents.py`). A chain is a connected component of the
links, joined also by a shared Change-Id, which the change-id bootstrap already treats as one
cluster, so chains coarsen it strictly. A change with no link is a chain of one.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sphragis.corpus.notedb import _isolated_env

#: Patch-set kinds that are not the author's own revision (Gerrit's ChangeKind).
NOT_AUTHORED = frozenset(
    {"TRIVIAL_REBASE", "TRIVIAL_REBASE_WITH_MESSAGE_UPDATE", "NO_CODE_CHANGE", "NO_CHANGE"}
)


@dataclass(frozen=True)
class Merged:
    number: int
    project: str
    change_id: str
    commit: str  # the last patch set, the merged commit
    authored: str  # the last authored patch set
    uploaded: str  # the last authored patch set's upload time
    submitted: str
    patch_sets: frozenset[str]


def merged(rows: Iterable[Mapping[str, Any]]) -> list[Merged]:
    """The merged changes of raw snapshot rows that name their last patch set."""
    out = []
    for row in rows:
        commit = row.get("current_revision")
        revisions = row.get("revisions") or {}
        if row.get("status") != "MERGED" or commit not in revisions or not row.get("submitted"):
            continue
        ordered = sorted(revisions.items(), key=lambda item: int(item[1]["_number"]))
        authored = [sha for sha, rev in ordered if rev.get("kind") not in NOT_AUTHORED]
        sha = authored[-1] if authored else ordered[0][0]
        out.append(
            Merged(
                int(row["_number"]),
                str(row["project"]),
                str(row["change_id"]),
                str(commit),
                sha,
                str(revisions[sha]["created"]),
                str(row["submitted"]),
                frozenset(revisions),
            )
        )
    return out


def parents(clone: Path, commits: Iterable[str]) -> dict[str, list[str]]:
    """Each commit's parents, for the commits the clone holds; one it lacks is left out."""
    wanted = sorted(set(commits))
    if not wanted:
        return {}
    env = _isolated_env()
    check = subprocess.run(
        ["git", "-C", str(clone), "cat-file", "--batch-check=%(objectname) %(objecttype)"],
        input="\n".join(wanted) + "\n",
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout.split("\n")
    held = [line.split()[0] for line in check if line.endswith(" commit")]
    if not held:
        return {}
    listed = subprocess.run(
        ["git", "-C", str(clone), "rev-list", "--no-walk=unsorted", "--parents", "--stdin"],
        input="\n".join(held) + "\n",
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout.split("\n")
    return {line.split()[0]: line.split()[1:] for line in listed if line}


def links(changes: list[Merged], parent_of: Mapping[str, list[str]]) -> list[tuple[int, int]]:
    """(child, parent) change numbers: a patch set of the parent is the parent of the child's
    last authored patch set, in the same project, and the parent was not yet merged when that
    patch set was uploaded."""
    by_patch_set = {sha: change for change in changes for sha in change.patch_sets}
    out = []
    for child in changes:
        for sha in parent_of.get(child.authored, []):
            parent = by_patch_set.get(sha)
            if (
                parent is not None
                and parent.number != child.number
                and parent.project == child.project
                and parent.submitted > child.uploaded
            ):
                out.append((child.number, parent.number))
    return out


def chains(changes: list[Merged], edges: Iterable[tuple[int, int]]) -> dict[int, int]:
    """Each change number's chain, named by its smallest change number: the connected components
    of `edges` and of shared Change-Ids."""
    root = {change.number: change.number for change in changes}

    def find(n: int) -> int:
        while root[n] != n:
            root[n] = root[root[n]]
            n = root[n]
        return n

    def join(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            root[max(ra, rb)] = min(ra, rb)

    for a, b in edges:
        join(a, b)
    first: dict[str, int] = {}
    for change in changes:
        join(change.number, first.setdefault(change.change_id, change.number))
    return {n: find(n) for n in root}


def chain_map(artifact: Mapping[str, Any], org: str) -> dict[str, str]:
    """`relation_chains.py`'s map from each example's Change-Id to its chain, for `org` only."""
    if artifact.get("org") != org:
        raise SystemExit(f"the chain artifact is {artifact.get('org')!r}'s, not {org!r}'s")
    return dict(artifact["change_chain"])


def relabel(rows: Iterable[dict[str, Any]], chain_of: Mapping[str, str]) -> set[str]:
    """Set every row's `change_id` to its chain, so whatever groups by `change_id` groups by
    chain; returns the Change-Ids seen. A Change-Id the map lacks is refused, never left as its
    own cluster."""
    seen: set[str] = set()
    for row in rows:
        if row["change_id"] not in chain_of:
            raise SystemExit(f"{row['change_id']} has no chain in the chain artifact")
        seen.add(row["change_id"])
        row["change_id"] = chain_of[row["change_id"]]
    return seen
