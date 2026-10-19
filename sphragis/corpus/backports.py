"""Which changes sit on release, maintenance or deployment branches: the backport rule.

A backport shares its Change-Id with the change it was picked from, and the examples keep no
branch, so the rule reads every raw change with an example's (project, Change-Id): an example is
only on a backport branch when every such change is, and possibly from one when any is. Both
count only backports that keep their Change-Id. One definition, read by `backport_share.py`
(the bounds) and `sensitivity_ids.py` (the examples the registered sensitivity removes).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from sphragis.corpus.storage import iter_snapshot

# Release, maintenance and deployment branches, as named on the hosts (read from the raw
# snapshots): OpenStack `stable/`, `unmaintained/`, `bugfix/`, `release_N` and StarlingX `r/stx`;
# MediaWiki `REL1_xx` (also under `fundraising/`), `wmf/` and `deploy/wmf/`, `wmf_deploy`,
# `deployment` and a bare `stable`; Qt's version lines (`6.8`, `6.8.1`, Creator's `15.0`), `lts-`
# lines, Design Studio's `qds/4.7` and `qds-4.6`, the Yocto releases its layers keep, and the
# `N-based` lines that maintain a Chromium base for Qt WebEngine (mostly `[Backport]` picks of
# upstream fixes). A bare version names a release line on the other hosts too (`2024.1`).
# Feature branches (`feature/`, `f/`) are development, as are Qt's `dev`, `master`, `qds/dev`,
# `upstream-master` and `N-based-refactor`.
#: The Yocto Project's releases from Dunfell (3.1) through those announced
#: (wiki.yoctoproject.org/wiki/Releases, read 2026-10-19), so a layer's later line reads too.
YOCTO = [
    "dunfell",
    "gatesgarth",
    "hardknott",
    "honister",
    "kirkstone",
    "langdale",
    "mickledore",
    "nanbield",
    "scarthgap",
    "styhead",
    "walnascar",
    "whinlatter",
    "wrynose",
    "blacksail",
    "garburn",
]
BACKPORT = re.compile(
    r"^(?:stable(?:/|-|$)|unmaintained/|bugfix/|release_[\d.]+$|r/stx|(?:fundraising/)?REL\d"
    r"|(?:deploy/)?wmf/|wmf_deploy$|deployment$"
    r"|\d+(?:\.\d+)+$|lts-\d|qds[/-]\d|(?:v|release_)?[\d.]+-based$"
    rf"|(?:{'|'.join(YOCTO)})$)"
)
#: A merge onto a branch, as Gerrit and git title it.
MERGE_FROM = re.compile(r"^Merge (?:remote-tracking )?branch '(?:origin/)?([^']+)'")


def raw_changes(root: Path, org: str) -> Iterator[dict]:
    """Every raw change in an organization's snapshots, month by month."""
    for path in sorted((Path(root) / org / "raw").glob("*.ndjson.gz")):
        yield from iter_snapshot(path)


def index_changes(
    root: Path, org: str
) -> tuple[dict[tuple[str, str], set[str]], dict[tuple[str, str], str | None]]:
    """In one pass over the raw snapshots, per (project, Change-Id): every branch it has a raw
    change on, and the earliest merge of any of them (None if none merged)."""
    branches: dict[tuple[str, str], set[str]] = {}
    merged: dict[tuple[str, str], str | None] = {}
    for change in raw_changes(root, org):
        key = (change["project"], change["change_id"])
        branches.setdefault(key, set()).add(str(change.get("branch") or ""))
        when = change.get("submitted") if change.get("status") == "MERGED" else None
        merged[key] = min((t for t in (merged.get(key), when) if t), default=None)
    return branches, merged


def branches_by_change(root: Path, org: str) -> dict[tuple[str, str], set[str]]:
    """Every branch a (project, Change-Id) has a raw change on, from the raw snapshots."""
    return index_changes(root, org)[0]


def merge_up_projects(root: Path, org: str) -> frozenset[str]:
    """The projects whose development branch merges a release branch up into it (Qt Creator's
    model, research log 2026-10-19): there a change lands on the release line first, so one on
    release branches alone is the original, not a pick of one."""
    found = set()
    for change in raw_changes(root, org):
        merged = MERGE_FROM.match(str(change.get("subject") or ""))
        onto = str(change.get("branch") or "")
        if merged and not BACKPORT.match(onto) and BACKPORT.match(merged.group(1)):
            found.add(change["project"])
    return frozenset(found)


def only_backport(names: set[str], *, merge_up: bool = False) -> bool:
    """On a backport branch and on no other: the lower bound, and the registered rule. In a
    merge-up project such a change is the original, so none is."""
    return not merge_up and bool(names) and all(BACKPORT.match(name) for name in names)


def possibly_backport(names: set[str], *, merge_up: bool = False) -> bool:
    """On a backport branch, whatever else: the upper bound. In a merge-up project, on a backport
    branch and another line too, as a pick between lines is."""
    on_one = any(BACKPORT.match(name) for name in names)
    return on_one and (not merge_up or len(names) > 1)
