"""Raw changes to refinement examples.

One example is a hunk a reviewer commented on at patch set n, paired with the same hunk
at n+1. Every drop is counted by reason rather than swallowed: the drop profile is what
the Stage 1 report's sampling section reports, and a silent drop would make the corpus
look cleaner than it is.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sphragis.corpus.examples import (
    Hunk,
    has_successor_revision,
    hunks_from_diff,
    is_code_file,
)
from sphragis.corpus.wellposed import ILL_POSED_REASONS, classify

# Sequence, not list: list is invariant, so a caller returning list[dict] would not
# satisfy a list[Mapping] parameter.
CommentFetcher = Callable[[int], Mapping[str, Sequence[Mapping[str, Any]]]]
DiffFetcher = Callable[[int, int, str, int], Mapping[str, Any]]

DROP_REASONS = (
    *(f"ill_posed_{reason}" for reason in ILL_POSED_REASONS),
    "metadata_file",
    "author_comment",
    "acknowledgement",
    "no_line_anchor",
    "no_successor",
    "diff_error",
    "no_anchored_hunk",
)


def is_reviewer_comment(comment: Mapping[str, Any], owner_id: Any) -> bool:
    """False when the change's own author wrote it.

    Measured on live OpenStack data 2026-09-14: 52% of code-file comments are authored by
    the change owner and 24% are literally "Done". Those are the author acknowledging a
    fix, not an instruction to make one. Including them pollutes the model's input and
    leaks that the edit was applied, which is the answer the model is meant to produce.

    A comment with no author survives: guessing is worse than keeping.
    """
    author = comment.get("author")
    if not isinstance(author, Mapping) or "_account_id" not in author:
        return True
    author_id = author["_account_id"]
    if isinstance(author_id, str) != isinstance(owner_id, str):
        raise TypeError(
            "comparing a scrubbed id against an unscrubbed one: "
            f"author={type(author_id).__name__} owner={type(owner_id).__name__}. "
            "Scrub comments with the same salt as the changes before building."
        )
    return author_id != owner_id


# Whole-message acknowledgements carrying no instruction. Deliberately small and exact:
# a comment is dropped only when its entire normalized text is one of these, so "done, but
# rename the variable" survives. Measured on the OpenStack 2024-10 build after the author
# filter: "Done" 16 times, "ditto" 4, "+1" 3, and 4 of 201 examples whose only comments
# were of this kind, leaving a target the prompt gives no way to reach. "ditto" points at
# another comment the prompt does not carry, so it is no instruction either. A
# pre-registration item: it changes which examples exist.
ACKNOWLEDGEMENTS = frozenset(
    {"done", "ditto", "+1", "ack", "acked", "fixed", "thanks", "thank you", "ok", "lgtm"}
)
_TRAILING = " .!:)"


def is_acknowledgement(message: str) -> bool:
    """True when a comment is only an acknowledgement, with nothing to act on."""
    return message.strip().lower().rstrip(_TRAILING).strip() in ACKNOWLEDGEMENTS


def _covers(hunk: Hunk, line: int) -> bool:
    end = hunk.before_start + max(len(hunk.before), 1) - 1
    return hunk.before_start <= line <= end


def build_from_change(
    org: str,
    change: Mapping[str, Any],
    fetch_comments: CommentFetcher,
    fetch_diff: DiffFetcher,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Every refinement example one change yields, with the reason for each drop."""
    drops: Counter[str] = Counter(dict.fromkeys(DROP_REASONS, 0))
    number = int(change["_number"])
    owner_id = (change.get("owner") or {}).get("_account_id")
    revision_count = len(change.get("revisions", {}))
    examples: list[dict[str, Any]] = []

    for path, comments in fetch_comments(number).items():
        if not is_code_file(path):
            drops["metadata_file"] += len(comments)
            continue
        # Group by hunk before emitting. The spec pairs a hunk with the comments anchored
        # inside it, plural: one example per comment would emit identical before/after rows
        # that dedup later discards as duplicates, losing every comment but the first.
        grouped: dict[tuple[int, int], tuple[Hunk, list[str]]] = {}
        for comment in comments:
            if owner_id is not None and not is_reviewer_comment(comment, owner_id):
                drops["author_comment"] += 1
                continue
            if is_acknowledgement(str(comment.get("message", ""))):
                drops["acknowledgement"] += 1
                continue
            patch_set, line = comment.get("patch_set"), comment.get("line")
            if not isinstance(line, int) or patch_set is None:
                drops["no_line_anchor"] += 1
                continue
            if not has_successor_revision(patch_set=patch_set, revision_count=revision_count):
                drops["no_successor"] += 1
                continue
            try:
                diff = fetch_diff(number, patch_set + 1, path, patch_set)
            except Exception:
                drops["diff_error"] += 1
                continue
            hit = next((h for h in hunks_from_diff(diff) if _covers(h, line)), None)
            if hit is None:
                drops["no_anchored_hunk"] += 1
                continue
            key = (patch_set, hit.before_start)
            grouped.setdefault(key, (hit, []))[1].append(str(comment["message"]))

        for (patch_set, start), (hunk, messages) in grouped.items():
            candidate = {
                "before": "\n".join(hunk.before),
                "after": "\n".join(hunk.after),
            }
            ill_posed = classify(candidate)
            if ill_posed is not None:
                drops[f"ill_posed_{ill_posed}"] += 1
                continue
            examples.append(
                {
                    "id": f"{org}:{change['change_id']}:{path}:{patch_set}:{start}",
                    "org": org,
                    "project": change.get("project"),
                    "change_id": change["change_id"],
                    "created": change.get("created"),
                    "path": path,
                    "patch_set": patch_set,
                    "before": "\n".join(hunk.before),
                    "after": "\n".join(hunk.after),
                    "comments": messages,
                }
            )

    return examples, dict(drops)
