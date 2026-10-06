"""Held-out splits for pilot-scale runs, where the sealed windows do not exist yet.

The confirmatory study splits by time through `sphragis.corpus.split`. A pilot on one month
has no time axis to split on, so it holds out a random share of changes instead, and every
pilot script takes that split from here. Two copies of it would be two chances to split by
example again, which is how a leaked 0.512 was nearly reported.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sphragis.corpus.cli import WINDOWS
from sphragis.corpus.load import refined_examples
from sphragis.corpus.pipeline import run_dedup, run_split


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
    changes. Performance grows roughly logarithmically with training-set size, and the
    standard control is subsampling each source to equal size.

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


SEALED_WINDOWS = ("test",)


def split_by_window(
    windows: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    train_window: str,
    eval_window: str,
    sealed: Sequence[str] = SEALED_WINDOWS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Training and held-out rows taken from two named time windows.

    This is the study's own split, where `holdout_by_change` is the pilot's stand-in for it:
    the design separates windows by change creation time, not at random, because a model
    evaluated on refinements contemporaneous with its training data is an easier test than
    the one the report claims to run.

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
    root: Path, org: str, *, train_window: str = "train", eval_window: str = "dev"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """One corpus's training and held-out rows as the registered runner reads them.

    Deduplicated as its own corpus, split into the study's time windows, and refused if any
    change straddles a boundary or falls outside every window. Returns the two row sets and a
    summary of where they came from. Every reader of a built corpus takes its rows from here,
    so a comparator is evaluated on the examples the adapters were.
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
    train, held_out = split_by_window(windows, train_window=train_window, eval_window=eval_window)
    summary = {
        "source": f"{train_window} -> {eval_window} windows under {root / org / 'refined'}",
        "examples": len(rows),
        "dedup_removed": removed,
    }
    return train, held_out, summary
