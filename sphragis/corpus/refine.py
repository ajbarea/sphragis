"""The label rules found by the Stage 1 data audit, applied to built examples.

Two things passed the build's filters that are not what an adapter should learn, and the first
reached one organization and not the other (research log, 2026-09-23):

- comments written by a bot, which enforce written rules (`sphragis.corpus.automated`);
- Gerrit's one-click "Acknowledged", which the acknowledgement list missed.

A third removes the examples whose target the reviewer wrote: Gerrit's "Suggest edit" fences the
reviewer's rewrite of the commented lines in a ```suggestion block, and the one-click "Fix
applied." reply follows a fix taken as given (both read from Gerrit's `gr-comment.ts`). The
target is then text the prompt already carries, a copy rather than a convention; the suggestion
body equals the target in most of them, and their share differs by organization (research log,
2026-09-28). The whole example goes, not just the comment, since its target is the edit.

A fourth rule guards a case the corpus turned out not to contain: a next patch set that is a
rebase or a message-only edit, where the "rewrite" would be whatever the rebase swept in. It is
applied per change (`examples.revision_kind`), never through the Change-Id alone, which a
cherry-pick shares across branches.

One scrub correction is carried here too: an older scrub pseudonymized only the first address of
a chained one ("a@b.com@example.com"), leaving the later domains behind the pseudonym, and
those are removed (`sweep_address_residue`).

These are applied here and only here, never by the build, so a change to them is a re-refine from
the examples and raw snapshots already on disk, nothing refetched; the build keeps what needs the
network or the comment's author. Every removal is counted by reason, as the build counts its own.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sphragis.corpus.automated import matched_bot, normalize_line_breaks
from sphragis.corpus.build import is_acknowledgement
from sphragis.corpus.examples import revision_kind, successor_changed_code
from sphragis.corpus.rules import RULES_VERSION, rules_version

__all__ = [
    "ADDRESS_RESIDUE",
    "KEPT_UNCHECKED",
    "REFINE_REASONS",
    "RULES_VERSION",
    "index_changes",
    "refine",
    "reviewer_wrote_target",
    "rules_version",
    "successor_kind",
]

REFINE_REASONS = (
    "automated_comment",
    "acknowledgement_comment",
    "automated_only",
    "acknowledgement_only",
    "not_rework_successor",
    "suggested_edit",
    "applied_fix",
)
# Counted: an example whose successor kind was never recorded, or whose change cannot be told
# apart from another with the same Change-Id, project and creation time. It is kept unless
# another rule removes it.
KEPT_UNCHECKED = "successor_kind_unknown"
# Counted, and the text kept: an address domain left behind a pseudonym by an older scrub.
ADDRESS_RESIDUE = "address_residue"


ChangeIndex = dict[str, Any]

# The fence Gerrit's "Suggest edit" opens (USER_SUGGESTION_START_PATTERN), on a line of its own.
_SUGGESTION_FENCE = re.compile(r"^[ \t]*```suggestion[ \t]*$", re.MULTILINE)
# Gerrit's reply when a fix is applied from a comment.
_APPLIED_FIX = "fix applied"


def reviewer_wrote_target(comments: Iterable[str]) -> str | None:
    """ "suggested_edit" or "applied_fix" when the target is the reviewer's own edit, else None."""
    texts = [normalize_line_breaks(text) for text in comments]
    if any(_SUGGESTION_FENCE.search(text) for text in texts):
        return "suggested_edit"
    if any(text.strip().lower().rstrip(" .!") == _APPLIED_FIX for text in texts):
        return "applied_fix"
    return None


# What the scrub before chained addresses were one address left of one: the domains after the
# first, behind the 12-hex pseudonym written for the first ("<pseudonym>@example.com"). The scrub
# now takes the whole chain, so this matches only what an earlier build left.
_RESIDUE = re.compile(r"(?<![0-9A-Za-z])([0-9a-f]{12})((?:@[A-Za-z0-9.-]+\.[A-Za-z]{2,})+)")


def sweep_address_residue(text: str) -> tuple[str, int]:
    """Text with any domain left behind a pseudonym removed, and how many were."""
    return _RESIDUE.subn(r"\1", text)


def _identity(record: Mapping[str, Any]) -> tuple[str, str, str]:
    return (str(record.get("change_id")), str(record.get("project")), str(record.get("created")))


def index_changes(changes: Iterable[Mapping[str, Any]]) -> ChangeIndex:
    """Raw changes by number, and by (Change-Id, project, created) for examples without one.

    A number seen twice with different content (the same change fetched in two months, say) is
    ambiguous rather than resolved to whichever file was read last.
    """
    by_number: dict[int, Mapping[str, Any] | None] = {}
    by_identity: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for change in changes:
        by_identity[_identity(change)].append(change)
        if change.get("_number") is None:
            continue
        number = int(change["_number"])
        if number in by_number and by_number[number] != change:
            by_number[number] = None
        elif number not in by_number:
            by_number[number] = change
    return {"by_number": by_number, "by_identity": by_identity}


def successor_kind(row: Mapping[str, Any], index: ChangeIndex) -> str | None:
    """The kind of this example's next patch set, from its own change; None if unresolvable."""
    change: Mapping[str, Any] | None
    if row.get("change_number") is not None:
        change = index["by_number"].get(int(row["change_number"]))
    else:
        candidates = index["by_identity"].get(_identity(row), [])
        change = candidates[0] if len(candidates) == 1 else None
    if change is None or row.get("patch_set") is None:
        return None
    return revision_kind(change, int(row["patch_set"]) + 1)


def refine(
    rows: Sequence[Mapping[str, Any]], index: ChangeIndex
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Examples under the audit's rules, and the count for each reason.

    Comments are removed one at a time, so an example with a bot's hint and a reviewer's
    instruction keeps the instruction; an example left with no comment is dropped.
    """
    counts: Counter[str] = Counter(
        dict.fromkeys((*REFINE_REASONS, KEPT_UNCHECKED, ADDRESS_RESIDUE), 0)
    )
    kept: list[dict[str, Any]] = []
    for row in rows:
        kind = successor_kind(row, index)
        if kind is None:
            counts[KEPT_UNCHECKED] += 1
        elif not successor_changed_code(kind):
            counts["not_rework_successor"] += 1
            continue
        written = reviewer_wrote_target(row["comments"])
        if written is not None:
            counts[written] += 1
            continue
        comments: list[str] = []
        automated = acknowledged = 0
        for text in row["comments"]:
            if matched_bot(text) is not None:
                automated += 1
            elif is_acknowledgement(text):
                acknowledged += 1
            else:
                swept, residues = sweep_address_residue(text)
                counts[ADDRESS_RESIDUE] += residues
                comments.append(swept)
        counts["automated_comment"] += automated
        counts["acknowledgement_comment"] += acknowledged
        if not comments:
            counts["automated_only" if automated else "acknowledgement_only"] += 1
            continue
        kept.append({**row, "comments": comments})
    return kept, dict(counts)
