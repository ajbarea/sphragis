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
    assert clean > 0.9
    assert masked < 0.75


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
    assert many > one
