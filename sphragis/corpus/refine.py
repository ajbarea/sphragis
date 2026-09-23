"""The label rules found by the Stage 1 data audit, applied to built examples.

Two things passed the build's filters that are not what an adapter should learn, and the first
reached one organization and not the other (research log, 2026-09-23):

- comments written by a bot, which enforce written rules (`sphragis.corpus.automated`);
- Gerrit's one-click "Acknowledged", which the acknowledgement list missed.

A third rule guards a case the corpus turned out not to contain: a next patch set that is a
rebase or a message-only edit, where the "rewrite" would be whatever the rebase swept in. It is
applied per change (`examples.revision_kind`), never through the Change-Id alone, which a
cherry-pick shares across branches.

The build applies the same rules as it reads comments, so a corpus built after them passes
through here unchanged; one built before is brought to the same rules from its examples and raw
snapshots, nothing refetched. Every removal is counted by reason, as the build counts its own.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from sphragis.corpus.automated import matched_bot, registry_digest
from sphragis.corpus.build import is_acknowledgement
from sphragis.corpus.examples import REWORK, revision_kind

REFINE_REASONS = (
    "automated_comment",
    "acknowledgement_comment",
    "automated_only",
    "acknowledgement_only",
    "not_rework_successor",
)
# Counted and kept: an example whose successor kind was never recorded, or whose change cannot
# be told apart from another with the same Change-Id, project and creation time.
KEPT_UNCHECKED = "successor_kind_unknown"

_CORPUS = Path(__file__).resolve().parent
_RULE_SOURCES = ("refine.py", "build.py", "examples.py", "automated/__init__.py")


def rules_version(sources: Mapping[str, str], registry: str) -> str:
    """A digest of the code that decides what an example is, and of the bot registries.

    Hashing the rule code itself means a changed rule is a changed version without anyone
    remembering to bump one; a comment edit also changes it, which errs toward re-refining.
    """
    digest = hashlib.sha256()
    for name in sorted(sources):
        digest.update(name.encode() + b"\0" + sources[name].encode() + b"\0")
    digest.update(registry.encode())
    return digest.hexdigest()[:16]


RULES_VERSION = rules_version(
    {name: (_CORPUS / name).read_text() for name in _RULE_SOURCES}, registry_digest()
)


ChangeIndex = dict[str, Any]


def index_changes(changes: Iterable[Mapping[str, Any]]) -> ChangeIndex:
    """Raw changes by number, and by (Change-Id, project, created) for examples without one."""
    by_number: dict[int, Mapping[str, Any]] = {}
    by_identity: dict[tuple[str, str, str], dict[int, Mapping[str, Any]]] = defaultdict(dict)
    for change in changes:
        number = int(change["_number"])
        by_number[number] = change
        key = (change["change_id"], change["project"], str(change.get("created")))
        by_identity[key][number] = change
    return {"by_number": by_number, "by_identity": by_identity}


def successor_kind(row: Mapping[str, Any], index: ChangeIndex) -> str | None:
    """The kind of this example's next patch set, from its own change; None if unresolvable."""
    change: Mapping[str, Any] | None
    if row.get("change_number") is not None:
        change = index["by_number"].get(int(row["change_number"]))
    else:
        candidates = index["by_identity"].get(
            (row["change_id"], row["project"], str(row.get("created")))
        )
        change = next(iter(candidates.values())) if candidates and len(candidates) == 1 else None
    return None if change is None else revision_kind(change, int(row["patch_set"]) + 1)


def refine(
    rows: Sequence[Mapping[str, Any]], index: ChangeIndex
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Examples under the audit's rules, and the count for each reason.

    Comments are removed one at a time, so an example with a bot's hint and a reviewer's
    instruction keeps the instruction; an example left with no comment is dropped.
    """
    counts: Counter[str] = Counter(dict.fromkeys((*REFINE_REASONS, KEPT_UNCHECKED), 0))
    kept: list[dict[str, Any]] = []
    for row in rows:
        kind = successor_kind(row, index)
        if kind is None:
            counts[KEPT_UNCHECKED] += 1
        elif kind != REWORK:
            counts["not_rework_successor"] += 1
            continue
        comments: list[str] = []
        automated = acknowledged = 0
        for text in row["comments"]:
            if matched_bot(text) is not None:
                automated += 1
            elif is_acknowledgement(text):
                acknowledged += 1
            else:
                comments.append(text)
        counts["automated_comment"] += automated
        counts["acknowledgement_comment"] += acknowledged
        if not comments:
            counts["automated_only" if automated else "acknowledgement_only"] += 1
            continue
        kept.append({**row, "comments": comments})
    return kept, dict(counts)
