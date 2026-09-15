"""The pilot's held-out split: changes whole on one side, and content leakage measured."""

from __future__ import annotations

import random
from typing import Any

import pytest

from sphragis.experiment.holdout import holdout_by_change, verbatim_overlap


def _rows(n_changes: int, per_change: int = 3, prefix: str = "I") -> list[dict[str, Any]]:
    return [
        {
            "id": f"{prefix}{c}:{e}",
            "change_id": f"{prefix}{c}",
            "before": f"b{prefix}{c}.{e}",
            "after": f"a{prefix}{c}.{e}",
        }
        for c in range(n_changes)
        for e in range(per_change)
    ]


def test_no_change_appears_on_both_sides() -> None:
    train, held_out = holdout_by_change(_rows(50), seed=0)
    assert {r["change_id"] for r in train}.isdisjoint({r["change_id"] for r in held_out})
    assert len(train) + len(held_out) == 150


def test_the_split_is_reproducible_and_seed_dependent() -> None:
    rows = _rows(50)
    assert holdout_by_change(rows, seed=3) == holdout_by_change(rows, seed=3)
    assert holdout_by_change(rows, seed=3)[1] != holdout_by_change(rows, seed=4)[1]


def test_the_held_out_share_is_a_share_of_changes_not_examples() -> None:
    uneven = _rows(10, per_change=1) + _rows(1, per_change=40, prefix="BIG")
    _, held_out = holdout_by_change(uneven, seed=0, eval_fraction=0.2)
    assert len({r["change_id"] for r in held_out}) == 11 - int(0.8 * 11)


def test_the_first_pilot_split_is_reproduced_exactly() -> None:
    """scripts/pilot.py used int(0.8 * n) on a seeded shuffle; moving it must not move it."""
    rows = _rows(88)
    changes = sorted({r["change_id"] for r in rows})
    random.Random(0).shuffle(changes)
    expected = set(changes[int(0.8 * len(changes)) :])
    _, held_out = holdout_by_change(rows, seed=0)
    assert {r["change_id"] for r in held_out} == expected


@pytest.mark.parametrize("fraction", [0.0, 1.0, -0.1])
def test_a_degenerate_fraction_is_refused(fraction: float) -> None:
    with pytest.raises(ValueError, match="eval_fraction"):
        holdout_by_change(_rows(10), seed=0, eval_fraction=fraction)


def test_verbatim_overlap_finds_the_same_edit_carried_by_two_changes() -> None:
    train = [{"id": "t", "change_id": "I1", "before": "x=1", "after": "x = 1"}]
    held_out = [
        {"id": "h1", "change_id": "I2", "before": "x=1", "after": "x = 1"},
        {"id": "h2", "change_id": "I3", "before": "y=1", "after": "y = 1"},
    ]
    assert verbatim_overlap(train, held_out) == ["h1"]
