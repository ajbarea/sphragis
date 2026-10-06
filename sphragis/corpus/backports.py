"""Which changes sit on release, maintenance or deployment branches: the backport rule.

A backport shares its Change-Id with the change it was picked from, and the examples keep no
branch, so the rule reads every raw change with an example's (project, Change-Id): an example is
only on a backport branch when every such change is, and possibly from one when any is. Both
count only backports that keep their Change-Id. One definition, read by `backport_share.py`
(the bounds) and `sensitivity_ids.py` (the examples the registered sensitivity removes).
"""

from __future__ import annotations

import gzip
import json
import re
from collections.abc import Iterator
from pathlib import Path

# Release, maintenance and deployment branches, as named on the two hosts (read from the raw
# snapshots): OpenStack `stable/`, `unmaintained/`, `bugfix/`, `release_N` and StarlingX `r/stx`;
# MediaWiki `REL1_xx` (also under `fundraising/`), `wmf/` and `deploy/wmf/`, `wmf_deploy`,
# `deployment` and a bare `stable`. Feature branches (`feature/`, `f/`) are development.
BACKPORT = re.compile(
    r"^(?:stable(?:/|-|$)|unmaintained/|bugfix/|release_\d|r/stx|(?:fundraising/)?REL\d"
    r"|(?:deploy/)?wmf/|wmf_deploy$|deployment$)"
)


def raw_changes(root: Path, org: str) -> Iterator[dict]:
    """Every raw change in an organization's snapshots, month by month."""
    for path in sorted((Path(root) / org / "raw").glob("*.ndjson.gz")):
        with gzip.open(path, "rt") as lines:
            for line in lines:
                yield json.loads(line)


def branches_by_change(root: Path, org: str) -> dict[tuple[str, str], set[str]]:
    """Every branch a (project, Change-Id) has a raw change on, from the raw snapshots."""
    branches: dict[tuple[str, str], set[str]] = {}
    for change in raw_changes(root, org):
        key = (change["project"], change["change_id"])
        branches.setdefault(key, set()).add(str(change.get("branch") or ""))
    return branches


def merged_by_change(root: Path, org: str) -> dict[tuple[str, str], str | None]:
    """The earliest merge of any raw change with a (project, Change-Id); None if none merged."""
    merged: dict[tuple[str, str], str | None] = {}
    for change in raw_changes(root, org):
        key = (change["project"], change["change_id"])
        when = change.get("submitted") if change.get("status") == "MERGED" else None
        earlier = merged.get(key)
        merged[key] = min((t for t in (earlier, when) if t), default=None)
    return merged


def only_backport(names: set[str]) -> bool:
    """On a backport branch and on no other: the lower bound, and the registered rule."""
    return bool(names) and all(BACKPORT.match(name) for name in names)


def possibly_backport(names: set[str]) -> bool:
    """On a backport branch, whatever else: the upper bound."""
    return any(BACKPORT.match(name) for name in names)
