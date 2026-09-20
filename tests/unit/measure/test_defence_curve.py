"""The defence curve: noise on an update, and what averaging rounds does to it."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "defence_curve.py"
_spec = importlib.util.spec_from_file_location("defence_curve", _SCRIPT)
assert _spec and _spec.loader
defence = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(defence)


def _population(rng, members: int = 8, outsiders: int = 24, dimension: int = 256):
    shared = rng.normal(size=dimension)
    shared /= np.linalg.norm(shared)
    mine = rng.normal(size=(members, dimension)) + 10.0 * shared
    theirs = rng.normal(size=(outsiders, dimension))
    return np.vstack([mine, theirs]), np.arange(members), np.arange(members, members + outsiders)


def test_auc_is_one_when_every_present_score_is_higher() -> None:
    assert defence.auc([1.0, 2.0], [0.0, 0.5]) == 1.0
    assert defence.auc([0.0, 0.0], [0.0, 0.0]) == 0.5


def test_without_noise_the_source_is_detected_and_enough_noise_hides_it() -> None:
    rng = np.random.default_rng(0)
    vectors, mine, outside = _population(rng)
    clean = defence.masked_rounds(
        vectors,
        members=mine[4:],
        outside=outside,
        reference=mine[:4],
        size=8,
        rounds=1,
        noise=0.0,
        draws=80,
        rng=rng,
    )
    masked = defence.masked_rounds(
        vectors,
        members=mine[4:],
        outside=outside,
        reference=mine[:4],
        size=8,
        rounds=1,
        noise=30.0,
        draws=80,
        rng=rng,
    )
    assert clean["auc"] > 0.9
    assert masked["auc"] < 0.75
    assert clean["mean_present"] > clean["mean_absent"], "the separation's size, not only its sign"


def test_averaging_rounds_recovers_what_one_round_of_noise_hid() -> None:
    """Fresh noise per round averages away; the target's own direction does not."""
    rng = np.random.default_rng(1)
    vectors, mine, outside = _population(rng)
    one = defence.masked_rounds(
        vectors,
        members=mine[4:],
        outside=outside,
        reference=mine[:4],
        size=8,
        rounds=1,
        noise=30.0,
        draws=80,
        rng=rng,
    )
    many = defence.masked_rounds(
        vectors,
        members=mine[4:],
        outside=outside,
        reference=mine[:4],
        size=8,
        rounds=40,
        noise=30.0,
        draws=60,
        rng=rng,
    )
    assert many["auc"] > one["auc"]


def test_a_target_free_difference_is_the_null_the_absent_class_measures() -> None:
    """Both classes difference two sets; only one holds the target, so 0.5 is no signal."""
    rng = np.random.default_rng(3)
    vectors = rng.normal(size=(32, 256))
    scored = defence.masked_rounds(
        vectors,
        members=np.arange(4),
        outside=np.arange(8, 32),
        reference=np.arange(4, 8),
        size=8,
        rounds=5,
        noise=0.0,
        draws=120,
        rng=rng,
    )
    assert 0.35 < scored["auc"] < 0.65


def test_the_curve_is_read_at_a_low_false_positive_rate_too() -> None:
    """A defence that only moves the average case would leave this number where it was."""
    rng = np.random.default_rng(7)
    vectors, mine, outside = _population(rng)
    clean = defence.masked_rounds(
        vectors,
        members=mine[4:],
        outside=outside,
        reference=mine[:4],
        size=8,
        rounds=1,
        noise=0.0,
        draws=200,
        rng=rng,
    )
    masked = defence.masked_rounds(
        vectors,
        members=mine[4:],
        outside=outside,
        reference=mine[:4],
        size=8,
        rounds=1,
        noise=30.0,
        draws=200,
        rng=rng,
    )
    assert clean["tpr_at_1pct_fpr"] > masked["tpr_at_1pct_fpr"]
    assert masked["fpr_achieved_at_1pct"] <= 0.01, "the threshold holds the rate it claims"
