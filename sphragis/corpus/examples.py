"""Refinement pairs: a commented hunk at patch set n and its rewrite at n+1."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any


@dataclass(frozen=True)
class Hunk:
    """One changed region, as it reads before and after the rewrite."""

    before_start: int
    before: tuple[str, ...]
    after_start: int
    after: tuple[str, ...]


def changed_hunks(before: Sequence[str], after: Sequence[str]) -> list[Hunk]:
    """Every region that differs between two revisions of one file, 1-indexed."""
    matcher = SequenceMatcher(a=list(before), b=list(after), autojunk=False)
    return [
        Hunk(i1 + 1, tuple(before[i1:i2]), j1 + 1, tuple(after[j1:j2]))
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


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
