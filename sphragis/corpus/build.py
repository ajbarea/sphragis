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

# Sequence, not list: list is invariant, so a caller returning list[dict] would not
# satisfy a list[Mapping] parameter.
CommentFetcher = Callable[[int], Mapping[str, Sequence[Mapping[str, Any]]]]
DiffFetcher = Callable[[int, int, str, int], Mapping[str, Any]]

DROP_REASONS = (
    "metadata_file",
    "no_line_anchor",
    "no_successor",
    "diff_error",
    "no_anchored_hunk",
)


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
    revision_count = len(change.get("revisions", {}))
    examples: list[dict[str, Any]] = []

    for path, comments in fetch_comments(number).items():
        if not is_code_file(path):
            drops["metadata_file"] += len(comments)
            continue
        for comment in comments:
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
            examples.append(
                {
                    "id": f"{org}:{change['change_id']}:{path}:{patch_set}:{hit.before_start}",
                    "org": org,
                    "project": change.get("project"),
                    "change_id": change["change_id"],
                    "created": change.get("created"),
                    "path": path,
                    "patch_set": patch_set,
                    "before": "\n".join(hit.before),
                    "after": "\n".join(hit.after),
                    "comments": [str(comment["message"])],
                }
            )
    return examples, dict(drops)
