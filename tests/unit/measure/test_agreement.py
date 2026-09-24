"""The label audit's agreement and proportion statistics, against hand-worked values."""

from __future__ import annotations

import math

import pytest

from sphragis.measure.agreement import (
    ac1_interval,
    cohen_kappa,
    confusion,
    gwet_ac1,
    kappa_interval,
    specific_agreement,
    wilson_interval,
)


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


# The kappa paradox (Feinstein and Cicchetti 1990): 118 yes/yes, 5 yes/no, 2 no/yes, 0 no/no.
# po = 0.944, yet kappa is below zero because one label dominates both raters' marginals.
PARADOX_FIRST = ["y"] * 123 + ["n"] * 2
PARADOX_SECOND = ["y"] * 118 + ["n"] * 5 + ["y"] * 2


def test_ac1_matches_a_worked_two_by_two() -> None:
    # pi_y = (25 + 30) / 100 = 0.55; pe = (0.55 * 0.45 + 0.45 * 0.55) / (2 - 1) = 0.495.
    first = ["y"] * 25 + ["n"] * 25
    second = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    assert gwet_ac1(first, second, ["y", "n"]) == pytest.approx((0.7 - 0.495) / (1 - 0.495))


def test_ac1_resists_the_prevalence_paradox_where_kappa_does_not() -> None:
    # pi_y = (123 + 120) / 250 = 0.972; pe = 2 * 0.972 * 0.028 = 0.054432.
    expected = (0.944 - 0.054432) / (1 - 0.054432)
    assert gwet_ac1(PARADOX_FIRST, PARADOX_SECOND, ["y", "n"]) == pytest.approx(expected)
    assert cohen_kappa(PARADOX_FIRST, PARADOX_SECOND) < 0


def test_ac1_counts_every_category_on_the_scale_not_only_those_used() -> None:
    # A five-label rubric with two labels used: pe divides by Q - 1 = 4, not by 1.
    first = ["y"] * 25 + ["n"] * 25
    second = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    pe = (0.55 * 0.45 + 0.45 * 0.55) / 4
    scale = ["y", "n", "a", "b", "c"]
    assert gwet_ac1(first, second, scale) == pytest.approx((0.7 - pe) / (1 - pe))


def test_ac1_is_one_at_perfect_agreement_and_refuses_labels_off_the_scale() -> None:
    assert gwet_ac1(["a", "b"], ["a", "b"], ["a", "b"]) == 1
    with pytest.raises(ValueError, match="scale"):
        gwet_ac1(["a", "z"], ["a", "b"], ["a", "b"])
    with pytest.raises(ValueError, match="two categories"):
        gwet_ac1(["a"], ["a"], ["a"])


def test_the_ac1_interval_brackets_the_estimate() -> None:
    low, high = ac1_interval(PARADOX_FIRST, PARADOX_SECOND, ["y", "n"], resamples=2000, seed=1)
    assert low < gwet_ac1(PARADOX_FIRST, PARADOX_SECOND, ["y", "n"]) < high


def test_specific_agreement_per_label() -> None:
    # yes: 2 * 20 / (25 + 30) = 0.727...; no: 2 * 15 / (25 + 20) = 0.666...; unused label: None.
    first = ["y"] * 25 + ["n"] * 25
    second = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    shares = specific_agreement(first, second, ["y", "n", "z"])
    assert shares["y"] == pytest.approx(40 / 55)
    assert shares["n"] == pytest.approx(30 / 45)
    assert shares["z"] is None
