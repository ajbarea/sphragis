"""Held-out splits for pilot-scale runs, where the sealed windows do not exist yet.

The confirmatory study splits by time through `sphragis.corpus.split`. A pilot on one month
has no time axis to split on, so it holds out a random share of changes instead, and every
pilot script takes that split from here. Two copies of it would be two chances to split by
example again, which is how a leaked 0.512 was nearly reported.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from sphragis.corpus.load import derived_file_rows, refined_examples
from sphragis.corpus.pipeline import run_dedup, run_split
from sphragis.corpus.windows import TEST_WINDOW_START, WINDOWS


def holdout_by_change(
    rows: Sequence[Mapping[str, Any]], *, seed: int, eval_fraction: float = 0.2
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Train and held-out rows, with every change wholly on one side.

    Consecutive hunks of one change are near-copies, so an example-level split puts
    siblings on both sides: measured at 63% of held-out examples sharing a change with
    training, inflating exact match from an honest figure to 0.512.
    """
    if not 0.0 < eval_fraction < 1.0:
        raise ValueError(f"eval_fraction must be strictly between 0 and 1, got {eval_fraction}")
    changes = sorted({str(r["change_id"]) for r in rows})
    random.Random(seed).shuffle(changes)
    cut = int((1.0 - eval_fraction) * len(changes))
    train_ids = set(changes[:cut])
    train = [dict(r) for r in rows if str(r["change_id"]) in train_ids]
    held_out = [dict(r) for r in rows if str(r["change_id"]) not in train_ids]
    return train, held_out


def verbatim_overlap(
    train: Sequence[Mapping[str, Any]], held_out: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Ids of held-out examples whose (before, after) pair also appears in training.

    Change grouping does not imply this is empty, and dedup does not fully guarantee it:
    two changes can carry the same edit. This is the check that measures content leakage
    across the boundary; asserting that change ids are disjoint cannot fail and proves
    nothing.
    """
    seen = {(str(r["before"]), str(r["after"])) for r in train}
    return [str(r["id"]) for r in held_out if (str(r["before"]), str(r["after"])) in seen]


def equalize_training(
    train: Mapping[str, Sequence[Mapping[str, Any]]], *, seed: int, size: int | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Every organization's training set cut to the smallest one's size, seeded.

    RQ1 compares an adapter trained on one organization against one trained on another. If
    one organization simply contributes more training examples, its adapter wins for that
    reason alone: in the first RQ1 pilot the Qt adapter trained on 422 examples against
    OpenStack's 145 and beat the matched OpenStack adapter on OpenStack's own held-out
    changes. Performance grows roughly logarithmically with training-set size
    (arXiv:1712.04008), and the standard control is subsampling each source to equal size.

    Subsampling is by example, not by change: grouping matters across the train/held-out
    boundary, which this does not touch, not within the training set.

    `size` cuts every set to a fixed size below the smallest, so contrasts between different
    pairs can be compared at one training size. The shuffle depends only on the seed and the
    organization, so a smaller size is a prefix of a larger one: the subsamples are nested.
    """
    if not train:
        raise ValueError("equalize_training needs at least one organization")
    smallest = min(len(rows) for rows in train.values())
    if smallest == 0:
        raise ValueError("an organization has no training examples to equalize to")
    if size is not None and not 0 < size <= smallest:
        raise ValueError(f"size must be between 1 and the smallest set, {smallest}; got {size}")
    size = smallest if size is None else size
    out: dict[str, list[dict[str, Any]]] = {}
    for org in sorted(train):
        rows = [dict(r) for r in train[org]]
        random.Random(f"equalize/{seed}/{org}").shuffle(rows)
        out[org] = rows[:size]
    return out


def matched_foreign_draw(
    sibling_draw: Sequence[Mapping[str, Any]],
    foreign_pool: Sequence[Mapping[str, Any]],
    *,
    in_language: Callable[[Mapping[str, Any]], bool],
    seed: int,
    sibling: str,
) -> list[dict[str, Any]]:
    """A foreign training set matched to one sibling draw, for H2's foreign arm.

    The draw holds as many `in_language` rows as `sibling_draw` does and fills the rest of its
    size from the pool's other rows, so the sibling and foreign adapters match in total and in
    in-language examples (research log, 2026-10-14). Each stratum is shuffled by the run's seed
    and the sibling it is matched to, so the draw is reproducible and two siblings of one run get
    independent draws. Refused when the pool cannot cover the draw: a short pool is a source that
    does not qualify, never a smaller arm. Refused too when the pool repeats an example id, since
    repeats would count as capacity and train twice. An `in_language` that raises propagates.
    """
    size = len(sibling_draw)
    if size == 0:
        raise ValueError("the sibling draw is empty")
    ids = [row["id"] for row in foreign_pool]
    if len(set(ids)) != len(ids):
        raise ValueError("the foreign pool repeats example ids; deduplicate it before drawing")
    wanted = sum(1 for row in sibling_draw if in_language(row))
    strata = {
        True: [dict(r) for r in foreign_pool if in_language(r)],
        False: [dict(r) for r in foreign_pool if not in_language(r)],
    }
    need = {True: wanted, False: size - wanted}
    for stratum, rows in strata.items():
        if len(rows) < need[stratum]:
            kind = "in-language" if stratum else "other"
            raise ValueError(
                f"the foreign pool holds {len(rows)} {kind} rows, the draw for {sibling} "
                f"needs {need[stratum]}"
            )
        random.Random(f"matched/{seed}/{sibling}/{stratum}").shuffle(rows)
    draw = strata[True][: need[True]] + strata[False][: need[False]]
    random.Random(f"matched/{seed}/{sibling}/order").shuffle(draw)
    return draw


def foreign_pool(
    root: Path, source: str, span: tuple[str, str]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """A foreign source's pool: its refined examples under `root`, deduplicated as one corpus.

    Refused when empty, when any row was created in the sealed test window or after it (a foreign
    arm trains on its draw, and the window is read only after acceptance), or when any row lies
    outside `span`, the created dates the source's pool was registered over (`created_from`
    inclusive, `created_before` exclusive).
    """
    pool, removed = run_dedup(refined_examples(root, source))
    if not pool:
        raise ValueError(f"no refined examples for {source} under {root}")
    return _in_span(pool, source, span), removed


def cleared_pool(path: Path, source: str, span: tuple[str, str]) -> list[dict[str, Any]]:
    """A foreign source's pool as `scripts/clear_foreign_pool.py` wrote it: the stop rule's
    prefix pool, deduplicated and cleared of the other organization's copies. Refused as
    `foreign_pool` refuses, and when the file is stale (`load.derived_file_rows`)."""
    return _in_span(derived_file_rows(path), source, span)


def _in_span(
    pool: list[dict[str, Any]], source: str, span: tuple[str, str]
) -> list[dict[str, Any]]:
    late = sum(1 for row in pool if str(row.get("created", ""))[:10] >= TEST_WINDOW_START)
    if late:
        raise ValueError(f"{source}'s pool holds {late} rows from the sealed window on")
    outside = sum(1 for row in pool if not span[0] <= str(row.get("created", ""))[:10] < span[1])
    if outside:
        raise ValueError(f"{source}'s pool holds {outside} rows outside its span {span}")
    return pool


def pool_digest(pool: Sequence[Mapping[str, Any]]) -> str:
    """One pool's identity: the sha256 of its examples' ids and texts, in id order, so a pool
    refined again under the same ids but other text is another pool."""
    rows = sorted((str(r["id"]), str(r["before"]), str(r["after"])) for r in pool)
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


SEALED_WINDOWS = ("test",)


def split_by_window(
    windows: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    train_window: str,
    eval_window: str,
    sealed: Sequence[str] = SEALED_WINDOWS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Training and held-out rows taken from two named time windows.

    The study's own split, by change creation time rather than at random: evaluating on
    refinements contemporaneous with the training data is an easier test than the one the
    report claims.
    `holdout_by_change` is the pilot's stand-in for it.

    Refuses to read a sealed window whatever the caller asks for. The test window is defined
    and hashed at Stage 1 and collected only after in-principle acceptance, and a driver that
    could name it as an evaluation set is one typo away from spending it early.
    """
    for name in (train_window, eval_window):
        if name in sealed:
            raise ValueError(f"{name!r} is sealed and cannot be read before acceptance")
        if name not in windows:
            raise ValueError(f"no window named {name!r}; have {sorted(windows)}")
    if train_window == eval_window:
        raise ValueError(f"train and eval windows are both {train_window!r}")
    return [dict(r) for r in windows[train_window]], [dict(r) for r in windows[eval_window]]


def window_split(
    root: Path,
    org: str,
    *,
    train_window: str = "train",
    eval_window: str = "dev",
    refuse_sealed_rows: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """One corpus's training and held-out rows as the registered runner reads them.

    Deduplicated as its own corpus, split into the study's time windows, and refused if any
    change straddles a boundary or falls outside every window. Returns the two row sets and a
    summary of where they came from. `rq1_pilot.py` and the retrieval comparator read through
    it, so a comparator is evaluated on the examples the adapters were.
    """
    rows = refined_examples(root, org)
    if not rows:
        raise ValueError(f"no refined examples under {root / org}; build and refine first")
    kept, removed = run_dedup(rows)
    windows, straddling, unassigned = run_split(kept, WINDOWS)
    if straddling or unassigned:
        raise ValueError(
            f"{org}: {len(straddling)} straddling and {len(unassigned)} unassigned changes"
        )
    # A reader that must not hold sealed rows at all, as the corpus readers that refuse them.
    if refuse_sealed_rows and windows.get("test"):
        raise ValueError(f"{org}: the corpus holds test-window examples; it is sealed")
    train, held_out = split_by_window(windows, train_window=train_window, eval_window=eval_window)
    summary = {
        "source": f"{train_window} -> {eval_window} windows under {root / org / 'refined'}",
        "examples": len(rows),
        "dedup_removed": removed,
    }
    return train, held_out, summary
