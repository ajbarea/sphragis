"""Refinement pairs: a commented hunk at patch set n and its rewrite at n+1."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any


@dataclass(frozen=True)
class Hunk:
    """One changed region, as it reads before and after the rewrite.

    ``context_before`` and ``context_after`` are the unchanged lines either side. They are
    prompt material, never part of the target: the model is asked to rewrite the hunk, and
    scoring compares only the hunk.
    """

    before_start: int
    before: tuple[str, ...]
    after_start: int
    after: tuple[str, ...]
    context_before: tuple[str, ...] = ()
    context_after: tuple[str, ...] = ()
    #: Gerrit's `due_to_rebase` on the block: the parents made this edit, not the author.
    due_to_rebase: bool = False


def changed_hunks(before: Sequence[str], after: Sequence[str]) -> list[Hunk]:
    """Every region that differs between two revisions of one file, 1-indexed."""
    matcher = SequenceMatcher(a=list(before), b=list(after), autojunk=False)
    return [
        Hunk(i1 + 1, tuple(before[i1:i2]), j1 + 1, tuple(after[j1:j2]))
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


# Gerrit exposes the commit message and review-level notes as pseudo-files in the same
# namespace as source. The study measures code and the comments anchored in it, and
# excludes commit metadata by design, so these never become examples.
METADATA_FILES = frozenset({"/COMMIT_MSG", "/PATCHSET_LEVEL", "/MERGE_LIST"})


def is_code_file(path: str) -> bool:
    """False for Gerrit's pseudo-files, which are metadata rather than reviewed code."""
    return path not in METADATA_FILES


# Gerrit's ChangeKind for a patch set that changed the code. Every other kind (TRIVIAL_REBASE,
# TRIVIAL_REBASE_WITH_MESSAGE_UPDATE, MERGE_FIRST_PARENT_UPDATE, NO_CODE_CHANGE, NO_CHANGE)
# means the author edited nothing, so a hunk that differs across it is not the author's answer.
REWORK = "REWORK"


def revision_kind(change: Mapping[str, Any], patch_set: int) -> str | None:
    """The ChangeKind Gerrit recorded for one patch set of this change, or None if unrecorded.

    Read from the change itself, never from a table keyed on the Change-Id: a cherry-pick keeps
    its Change-Id on every branch it lands on, so that id names several changes whose patch sets
    differ in kind (a keyed table once reported every non-rework successor in the corpus falsely).
    """
    for revision in (change.get("revisions") or {}).values():
        if revision.get("_number") is not None and int(revision["_number"]) == patch_set:
            return revision.get("kind") or None
    return None


def successor_changed_code(kind: str | None) -> bool:
    """Whether a successor of this kind can carry the author's answer: a rework, or unrecorded.

    An unrecorded kind is kept: the git route records none, and detects rebase edits from
    parent commits instead.
    """
    return kind is None or kind == REWORK


def has_successor_revision(*, patch_set: int, revision_count: int) -> bool:
    """Whether patch set ``patch_set`` has an n+1 to diff against.

    A comment on the final patch set has no successor, so there is no author response to
    pair it with. Confirmed on live OpenStack data 2026-09-14 (1 of 9 comments in a clean
    sample). A drop condition rather than a fetch failure: counting these as errors would
    hide a normal property of the data. The overall rate is not yet established.
    """
    return patch_set < revision_count


def _context(
    blocks: Sequence[Mapping[str, Any]], index: int, direction: int, width: int
) -> tuple[str, ...]:
    """Up to ``width`` unchanged lines on one side of the block at ``index``."""
    if width <= 0:
        return ()
    neighbour = index + direction
    if not 0 <= neighbour < len(blocks):
        return ()
    common = blocks[neighbour].get("ab")
    if not common:
        return ()
    return tuple(common[-width:] if direction < 0 else common[:width])


def hunks_from_diff(diff: Mapping[str, Any], *, context: int = 0) -> list[Hunk]:
    """Changed regions from Gerrit's own diff between two patch sets.

    Gerrit returns `content` as an ordered list of blocks: `ab` lines are common to both
    sides, `a` lines exist only before, `b` lines only after. Line numbers have to be
    accumulated across every block including the common ones -- counting only changed
    blocks would misanchor every comment in the corpus.

    Using Gerrit's diff rather than re-diffing fetched file contents keeps the hunk
    boundaries identical to the ones the reviewer was looking at.
    """
    blocks = list(diff.get("content", []))
    hunks: list[Hunk] = []
    before_line = after_line = 1
    for index, block in enumerate(blocks):
        common = block.get("ab")
        if common is not None:
            before_line += len(common)
            after_line += len(common)
            continue
        removed = tuple(block.get("a", ()))
        added = tuple(block.get("b", ()))
        if removed or added:
            hunks.append(
                Hunk(
                    before_line,
                    removed,
                    after_line,
                    added,
                    _context(blocks, index, -1, context),
                    _context(blocks, index, 1, context),
                    bool(block.get("due_to_rebase")),
                )
            )
            before_line += len(removed)
            after_line += len(added)
    return hunks


def _anchored(hunk: Hunk, comments: Sequence[Mapping[str, Any]]) -> list[str]:
    end = hunk.before_start + max(len(hunk.before), 1) - 1
    return [
        str(c["message"])
        for c in comments
        if isinstance(c.get("line"), int) and hunk.before_start <= c["line"] <= end
    ]


def build_examples(
    *,
    org: str,
    change: Mapping[str, Any],
    path: str,
    before: Sequence[str],
    after: Sequence[str],
    comments: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Build the refinement examples for one file, with the reason each hunk was dropped."""
    examples: list[dict[str, Any]] = []
    drops: Counter[str] = Counter({"no_anchored_comment": 0})
    for hunk in changed_hunks(before, after):
        anchored = _anchored(hunk, comments)
        if not anchored:
            drops["no_anchored_comment"] += 1
            continue
        examples.append(
            {
                "id": f"{org}:{change['change_id']}:{path}:{hunk.before_start}",
                "org": org,
                "project": change.get("project"),
                "change_id": change["change_id"],
                "created": change.get("created"),
                "path": path,
                "before": "\n".join(hunk.before),
                "after": "\n".join(hunk.after),
                "comments": anchored,
            }
        )
    return examples, dict(drops)
