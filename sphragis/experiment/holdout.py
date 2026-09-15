"""Held-out splits for pilot-scale runs, where the sealed windows do not exist yet.

The confirmatory study splits by time through `sphragis.corpus.split`. A pilot on one month
has no time axis to split on, so it holds out a random share of changes instead, and every
pilot script takes that split from here. Two copies of it would be two chances to split by
example again, which is how a leaked 0.512 was nearly reported.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from typing import Any


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
