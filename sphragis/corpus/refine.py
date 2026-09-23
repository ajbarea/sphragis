"""The label rules found by the Stage 1 data audit, applied to built examples.

Three things passed the build's filters that are not what an adapter should learn, and each
reached one organization more than the other (research log, 2026-09-23):

- comments written by a bot, which enforce written rules (`sphragis.corpus.automated`);
- a next patch set that is a rebase or a message-only edit, so the "rewrite" is whatever the
  rebase swept in rather than anything the author did;
- Gerrit's one-click "Acknowledged", which the acknowledgement list missed.

The build applies the same rules to comments as it reads them, so a corpus built after this
passes through here unchanged; a corpus built before is brought to the same rules without
refetching anything, from its examples and the raw snapshots they came from. Every removal is
counted by reason, as the build counts its own.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sphragis.corpus.automated import matched_bot, registry_digest
from sphragis.corpus.build import ACKNOWLEDGEMENTS, is_acknowledgement

# The CLI refuses a corpus refined under another version, so a changed rule cannot be half
# applied. The tag changes with this module's rules; the digest changes with any bot registry
# or the builder's acknowledgement list, without anyone remembering to bump it.
_RULES_TAG = "2026-09-23"
RULES_VERSION = (
    f"{_RULES_TAG}:{registry_digest()}:"
    + hashlib.sha256(",".join(sorted(ACKNOWLEDGEMENTS)).encode()).hexdigest()[:8]
)

REFINE_REASONS = (
    "automated_comment",
    "acknowledgement_comment",
    "automated_only",
    "acknowledgement_only",
    "not_rework_successor",
    "unknown_successor",
)

# Gerrit's ChangeKind for a patch set that changed the code. Every other kind (TRIVIAL_REBASE,
# TRIVIAL_REBASE_WITH_MESSAGE_UPDATE, MERGE_FIRST_PARENT_UPDATE, NO_CODE_CHANGE, NO_CHANGE)
# means the author edited nothing, so a hunk that differs across it is not the author's answer.
REWORK = "REWORK"


def revision_kinds(changes: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str, int], str]:
    """(change id, project, patch set number) to that revision's ChangeKind, from raw changes."""
    kinds: dict[tuple[str, str, int], str] = {}
    for change in changes:
        for revision in (change.get("revisions") or {}).values():
            kind = revision.get("kind")
            if kind is not None:
                kinds[(change["change_id"], change["project"], int(revision["_number"]))] = kind
    return kinds


def successor_is_rework(kind: str | None) -> bool | None:
    """Whether the next patch set changed code; None when its kind was never recorded."""
    return None if kind is None else kind == REWORK


def refine(
    rows: Sequence[Mapping[str, Any]],
    kinds: Mapping[tuple[str, str, int], str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Examples under the audit's rules, and the count removed for each reason.

    Comments are removed one at a time, so an example with a bot's hint and a reviewer's
    instruction keeps the instruction; an example left with no comment is dropped. An example
    whose successor kind was never recorded is dropped and counted apart, since keeping it would
    assume the one thing this checks.
    """
    drops: Counter[str] = Counter(dict.fromkeys(REFINE_REASONS, 0))
    kept: list[dict[str, Any]] = []
    for row in rows:
        rework = successor_is_rework(
            kinds.get((row["change_id"], row["project"], int(row["patch_set"]) + 1))
        )
        if rework is None:
            drops["unknown_successor"] += 1
            continue
        if not rework:
            drops["not_rework_successor"] += 1
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
        drops["automated_comment"] += automated
        drops["acknowledgement_comment"] += acknowledged
        if not comments:
            drops["automated_only" if automated else "acknowledgement_only"] += 1
            continue
        kept.append({**row, "comments": comments})
    return kept, dict(drops)
