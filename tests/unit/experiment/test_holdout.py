"""The pilot's held-out split: changes whole on one side, and content leakage measured."""

from __future__ import annotations

import random
from typing import Any

import pytest

from sphragis.experiment.holdout import (
    equalize_training,
    holdout_by_change,
    split_by_window,
    verbatim_overlap,
)


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


def test_equalize_cuts_every_organization_to_the_smallest() -> None:
    train = {"openstack": _rows(10, per_change=1), "qt": _rows(30, per_change=1, prefix="Q")}
    out = equalize_training(train, seed=1)
    assert {org: len(rows) for org, rows in out.items()} == {"openstack": 10, "qt": 10}


def test_equalize_draws_a_subset_not_a_prefix_and_is_reproducible() -> None:
    qt = _rows(30, per_change=1, prefix="Q")
    train = {"openstack": _rows(10, per_change=1), "qt": qt}
    first = equalize_training(train, seed=1)["qt"]
    assert first == equalize_training(train, seed=1)["qt"]
    assert first != qt[:10], "a prefix would bias toward the earliest examples"
    assert {r["id"] for r in first} <= {r["id"] for r in qt}
    assert first != equalize_training(train, seed=2)["qt"]


def test_equalize_refuses_an_organization_with_nothing_to_train_on() -> None:
    with pytest.raises(ValueError, match="no training examples"):
        equalize_training({"openstack": [], "qt": _rows(3)}, seed=0)


WINDOWED = {
    "pilot": _rows(3, prefix="P"),
    "train": _rows(5, prefix="T"),
    "dev": _rows(2, prefix="D"),
    "test": _rows(9, prefix="X"),
}


def test_split_by_window_takes_the_two_named_windows() -> None:
    train, held_out = split_by_window(WINDOWED, train_window="train", eval_window="dev")
    assert {r["change_id"] for r in train} == {f"T{i}" for i in range(5)}
    assert {r["change_id"] for r in held_out} == {f"D{i}" for i in range(2)}


@pytest.mark.parametrize(
    ("train_window", "eval_window"), [("train", "test"), ("test", "dev"), ("test", "test")]
)
def test_split_by_window_refuses_the_sealed_window(train_window: str, eval_window: str) -> None:
    """One typo away from spending the confirmatory set before acceptance."""
    with pytest.raises(ValueError, match="sealed"):
        split_by_window(WINDOWED, train_window=train_window, eval_window=eval_window)


def test_split_by_window_refuses_an_unknown_window() -> None:
    with pytest.raises(ValueError, match="no window named"):
        split_by_window(WINDOWED, train_window="train", eval_window="validation")


def test_split_by_window_refuses_to_evaluate_on_the_training_window() -> None:
    with pytest.raises(ValueError, match="both"):
        split_by_window(WINDOWED, train_window="train", eval_window="train")


def test_split_by_window_copies_rather_than_aliasing_the_windows() -> None:
    train, _ = split_by_window(WINDOWED, train_window="train", eval_window="dev")
    train[0]["change_id"] = "mutated"
    assert WINDOWED["train"][0]["change_id"] == "T0"
