"""The registered reading of a membership score against the blind baseline."""

from __future__ import annotations

import pytest

from sphragis.measure.blind import exposure_reading


def _sep(low: float, high: float, key: str = "bag_of_words - min_k_plus_plus") -> dict:
    return {key: {"estimate": (low + high) / 2, "low": low, "high": high}}


def test_a_blind_baseline_that_separates_more_leaves_exposure_untold() -> None:
    # The 2026-10-07 pilot: bag of words minus Min-K%++ is [0.100, 0.183].
    reading = exposure_reading(_sep(0.100, 0.183))
    assert reading["reading"] == "not_told_from_drift"
    assert reading["membership_minus_blind"]["high"] == pytest.approx(-0.100)


def test_membership_above_the_blind_baseline_reads_as_exposure() -> None:
    assert exposure_reading(_sep(-0.09, -0.01))["reading"] == "exposure"


def test_an_interval_touching_zero_is_not_exposure() -> None:
    assert exposure_reading(_sep(-0.05, 0.0))["reading"] == "not_told_from_drift"


def test_the_difference_is_read_in_either_key_order() -> None:
    key = "min_k_plus_plus - zz_blind"
    reading = exposure_reading(_sep(0.02, 0.05, key), blind="zz_blind")
    assert reading["reading"] == "exposure"
