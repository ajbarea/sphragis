"""The label audit's agreement and proportion statistics, against hand-worked values."""

from __future__ import annotations

import math

import pytest

from sphragis.measure.agreement import cohen_kappa, confusion, kappa_interval, wilson_interval


def test_kappa_matches_a_worked_two_by_two() -> None:
    # 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no: po = 0.7, pe = 0.5*0.6 + 0.5*0.4 = 0.5.
    first = ["y"] * 25 + ["n"] * 25
    second = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    assert cohen_kappa(first, second) == pytest.approx(0.4)


def test_perfect_agreement_is_one_and_chance_is_zero() -> None:
    labels = ["a", "b", "c", "a"]
    assert cohen_kappa(labels, labels) == 1
    assert cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == pytest.approx(0)


def test_kappa_is_undefined_when_both_raters_give_one_label() -> None:
    assert math.isnan(cohen_kappa(["a", "a"], ["a", "a"]))


def test_kappa_refuses_unpaired_labels() -> None:
    with pytest.raises(ValueError, match="same items"):
        cohen_kappa(["a"], ["a", "b"])


def test_the_kappa_interval_brackets_the_estimate() -> None:
    first = ["y"] * 25 + ["n"] * 25
    second = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    low, high = kappa_interval(first, second, resamples=2000, seed=1)
    assert low < 0.4 < high
    assert low >= -1 and high <= 1


def test_wilson_matches_a_worked_value() -> None:
    # 92 of 120: the first audit's reading, 95% [0.683, 0.833].
    low, high = wilson_interval(92, 120)
    assert (round(low, 3), round(high, 3)) == (0.683, 0.833)


def test_wilson_stays_inside_the_unit_interval_at_the_edges() -> None:
    assert wilson_interval(0, 10)[0] == 0
    assert wilson_interval(10, 10)[1] == 1


def test_confusion_counts_every_pair() -> None:
    table = confusion(["a", "a", "b"], ["a", "b", "b"], ["a", "b"])
    assert table == {"a": {"a": 1, "b": 1}, "b": {"a": 0, "b": 1}}
