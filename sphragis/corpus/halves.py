"""The registered split of one organization's projects into two halves, and its criteria.

Projects are sorted by training-window example count, largest first, and each goes to whichever
half is smaller so far: no seed and no search, so the halves are a function of the corpus alone.
An organization enters a confirmatory H1 cell only if its halves meet `split_criteria` against
OpenStack's own halves (spec `docs/superpowers/specs/2026-09-22-granularity-redesign.md`,
"Admitted organizations").
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any

MIN_PROJECTS_A_HALF = 3
MAX_SHARE_OF_HALF = 0.5


def assign(counts: dict[str, int]) -> dict[str, int]:
    """Each project to a side, largest first, always to the smaller side.

    Deterministic and unseeded on purpose. Ties in count break by name, so the assignment is
    a function of the corpus alone and re-running it cannot produce a different control.
    """
    sides = [0, 0]
    out: dict[str, int] = {}
    for project in sorted(counts, key=lambda p: (-counts[p], p)):
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


def total_variation(p: Mapping[str, int], q: Mapping[str, int]) -> float:
    """Half the L1 distance between two count vectors, each normalised to a distribution."""
    np_, nq = sum(p.values()), sum(q.values())
    if not np_ or not nq:
        raise ValueError("total variation needs two non-empty distributions")
    return 0.5 * sum(abs(p.get(k, 0) / np_ - q.get(k, 0) / nq) for k in set(p) | set(q))


def halves(
    built: Iterable[Mapping[str, Any]], train: Iterable[Mapping[str, Any]], window: tuple[str, str]
) -> list[dict[str, Any]]:
    """Both halves of one organization's training window: projects, examples and suffix mix.

    Sides are assigned on the refined rows before dedup, as the placebo corpus assigns them;
    what each half holds is read from `train`, the deduplicated training window it trains on.
    """
    side_of = assign(dict(project_counts(built, window)))
    train = [row for row in train if row["project"] in side_of]
    out = []
    for side in (0, 1):
        members = [row for row in train if side_of[row["project"]] == side]
        per_project = Counter(row["project"] for row in members)
        largest, top = per_project.most_common(1)[0] if per_project else (None, 0)
        out.append(
            {
                "projects": len(per_project),
                "train_examples": len(members),
                "largest": largest,
                "largest_share": top / len(members) if members else 1.0,
                "suffixes": dict(Counter(suffix(row["path"]) for row in members).most_common()),
            }
        )
    return out


def split_criteria(own: list[dict[str, Any]], ref: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether an organization's `halves` qualify, each criterion read against the reference's.

    The reference is OpenStack's halves: its smaller half is the size floor and its suffix mix
    the language-mix ceiling.
    """
    floor = min(h["train_examples"] for h in ref)
    tv = total_variation(own[0]["suffixes"], own[1]["suffixes"])
    ceiling = total_variation(ref[0]["suffixes"], ref[1]["suffixes"])
    checks = {
        "projects": all(h["projects"] >= MIN_PROJECTS_A_HALF for h in own),
        "largest_share": all(h["largest_share"] <= MAX_SHARE_OF_HALF for h in own),
        "size": all(h["train_examples"] >= floor for h in own),
        "language_mix": tv <= ceiling,
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
