"""The registered split of one organization's projects into two halves, and its criteria.

Projects are sorted by training-window example count, largest first, and each goes to whichever
half is smaller so far: no seed and no search, so the halves are a function of the corpus alone.
An organization enters a confirmatory H1 cell only if its halves meet `split_criteria` against
OpenStack's own halves (spec `docs/superpowers/specs/2026-09-22-granularity-redesign.md`,
"Admitted organizations").
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any

MIN_PROJECTS_A_HALF = 3
MAX_SHARE_OF_HALF = 0.5


def assign(counts: dict[str, int], order_seed: int | None = None) -> dict[str, int]:
    """Each project to a side, largest first, always to the smaller side.

    Deterministic and unseeded on purpose. Ties in count break by name, so the assignment is
    a function of the corpus alone and re-running it cannot produce a different control.

    `order_seed` replaces largest-first with a seeded shuffle of the names, still each to the
    smaller side: an alternative balanced partition for measuring partition variance, never the
    registered split.
    """
    order = sorted(counts, key=lambda p: (-counts[p], p))
    if order_seed is not None:
        order = sorted(counts)
        random.Random(order_seed).shuffle(order)
    sides = [0, 0]
    out: dict[str, int] = {}
    for project in order:
        side = 0 if sides[0] <= sides[1] else 1
        out[project] = side
        sides[side] += counts[project]
    return out


def project_counts(rows: Iterable[Mapping[str, Any]], window: tuple[str, str]) -> Counter[str]:
    """Examples per project created inside one window: what `assign` balances."""
    start, end = window
    return Counter(row["project"] for row in rows if start <= row["created"][:10] < end)


def suffix(path: str) -> str:
    """A file's type as the criteria count it: its lowercased suffix, or its name if it has none."""
    pure = PurePosixPath(path)
    return pure.suffix.lower() or pure.name


# Below this a target matches a comment by chance ("private", "!== null").
TYPED_TARGET_MIN_CHARS = 20


def _lines(text: str) -> str:
    return "\n".join(line.strip() for line in text.strip().splitlines())


def target_in_comment(row: Mapping[str, Any]) -> bool:
    """Whether a reviewer typed the rewrite into a comment: reported per half, never filtered.

    The target, whitespace-normalized per line, appears verbatim in a comment. Gerrit's
    suggested edits are removed by `refine`; this is the same leak written by hand, which is
    ordinary reviewing and so is described rather than removed (research log, 2026-09-28).
    """
    after = _lines(str(row.get("after") or ""))
    if len(after) < TYPED_TARGET_MIN_CHARS or after == _lines(str(row.get("before") or "")):
        return False
    return any(after in _lines(str(c)) for c in row.get("comments") or [])


def total_variation(p: Mapping[str, int], q: Mapping[str, int]) -> float:
    """Half the L1 distance between two count vectors, each normalised to a distribution."""
    np_, nq = sum(p.values()), sum(q.values())
    if not np_ or not nq:
        raise ValueError("total variation needs two non-empty distributions")
    return 0.5 * sum(abs(p.get(k, 0) / np_ - q.get(k, 0) / nq) for k in set(p) | set(q))


def halves(
    built: Iterable[Mapping[str, Any]],
    train: Iterable[Mapping[str, Any]],
    window: tuple[str, str],
    order_seed: int | None = None,
) -> list[dict[str, Any]]:
    """Both halves of one organization's training window: projects, examples and suffix mix.

    Sides are assigned on the refined rows before dedup, as the placebo corpus assigns them;
    what each half holds is read from `train`, the deduplicated training window it trains on.
    """
    side_of = assign(dict(project_counts(built, window)), order_seed)
    train = [row for row in train if row["project"] in side_of]
    out = []
    for side in (0, 1):
        members = [row for row in train if side_of[row["project"]] == side]
        per_project = Counter(row["project"] for row in members)
        largest, top = per_project.most_common(1)[0] if per_project else (None, 0)
        out.append(
            {
                "assigned_projects": sum(1 for s in side_of.values() if s == side),
                "projects": len(per_project),
                "train_examples": len(members),
                "largest": largest,
                "largest_share": top / len(members) if members else 1.0,
                "suffixes": dict(Counter(suffix(row["path"]) for row in members).most_common()),
                "target_in_comment": sum(target_in_comment(row) for row in members),
            }
        )
    return out


def excluded_projects(built: Iterable[Mapping[str, Any]], window: tuple[str, str]) -> list[str]:
    """Projects with refined rows but none in the training window, excluded before the split."""
    rows = list(built)
    return sorted({row["project"] for row in rows} - set(project_counts(rows, window)))


def runner_train(
    built: list[dict[str, Any]],
    windows: Mapping[str, tuple[str, str]],
    order_seed: int | None = None,
) -> list[dict[str, Any]]:
    """The training rows each half trains on: every half deduplicated and split on its own, as
    the runner reads a placebo half, so a duplicate across halves survives in both."""
    from sphragis.corpus.pipeline import run_dedup, run_split

    side_of = assign(dict(project_counts(built, windows["train"])), order_seed)
    train: list[dict[str, Any]] = []
    for side in (0, 1):
        kept, _ = run_dedup([row for row in built if side_of.get(row["project"]) == side])
        split, _, _ = run_split(kept, dict(windows))
        train.extend(split["train"])
    return train


def split_criteria(own: list[dict[str, Any]], ref: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether an organization's `halves` qualify, each criterion read against the reference's.

    The reference is OpenStack's halves: its smaller half is the size floor and its suffix mix
    the language-mix ceiling. Projects are counted after dedup, as what a half trains on; a half
    left with no training rows does not qualify.
    """
    floor = min(h["train_examples"] for h in ref)
    empty = any(not h["suffixes"] for h in own)
    tv = None if empty else total_variation(own[0]["suffixes"], own[1]["suffixes"])
    ceiling = total_variation(ref[0]["suffixes"], ref[1]["suffixes"])
    checks = {
        "projects": all(h["projects"] >= MIN_PROJECTS_A_HALF for h in own),
        "largest_share": all(h["largest_share"] <= MAX_SHARE_OF_HALF for h in own),
        "size": all(h["train_examples"] >= floor for h in own),
        "language_mix": tv is not None and tv <= ceiling,
    }
    return {
        "halves": own,
        "reference_halves": ref,
        "size_floor": floor,
        "suffix_total_variation": tv,
        "suffix_total_variation_ceiling": ceiling,
        "checks": checks,
        "qualifies": all(checks.values()),
    }
