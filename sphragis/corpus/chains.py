"""Gerrit relation chains, rebuilt from merged changes and their projects' local clones.

Registered 2026-10-21 (research log, "Relation chains registered as H1's cluster unit"). Change A
is linked to change B when the parent of A's last patch set is B's last patch set, in the same
project, and B was not yet merged when A's last patch set was uploaded; a merged change's last
patch set is its merged commit, so its parents are read from a bare clone. A chain is a connected
component of the links, joined also by a shared Change-Id, which the change-id bootstrap already
treats as one cluster, so chains coarsen it strictly. A change with no link is a chain of one.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sphragis.corpus.notedb import _isolated_env


@dataclass(frozen=True)
class Merged:
    number: int
    project: str
    change_id: str
    commit: str
    uploaded: str  # the last patch set's upload time
    submitted: str


def merged(rows: Iterable[Mapping[str, Any]]) -> list[Merged]:
    """The merged changes of raw snapshot rows that name their last patch set."""
    out = []
    for row in rows:
        commit = row.get("current_revision")
        revision = (row.get("revisions") or {}).get(commit) if commit else None
        if row.get("status") != "MERGED" or not revision or not row.get("submitted"):
            continue
        out.append(
            Merged(
                int(row["_number"]),
                str(row["project"]),
                str(row["change_id"]),
                str(commit),
                str(revision["created"]),
                str(row["submitted"]),
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
    """(child, parent) change numbers: the parent's last patch set is the child's last patch
    set's parent, in the same project, and was not yet merged when the child's was uploaded."""
    by_commit = {change.commit: change for change in changes}
    out = []
    for child in changes:
        for sha in parent_of.get(child.commit, []):
            parent = by_commit.get(sha)
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
