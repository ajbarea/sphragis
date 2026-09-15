"""Pilot power analysis: the minimum effect the planned window could detect."""

from __future__ import annotations

import random

from sphragis.experiment.power import minimum_detectable_effect, realised_difference, simulate_power
from sphragis.measure.stats import Cluster


def _pilot(n: int, seed: int = 0) -> list[Cluster]:
    rng = random.Random(seed)
    return [
        Cluster(
            f"I{i}",
            tuple(float(rng.random() < 0.3) for _ in range(rng.randint(1, 4))),
            tuple(float(rng.random() < 0.3) for _ in range(rng.randint(1, 4))),
        )
        for i in range(n)
    ]


def test_zero_effect_gives_power_near_the_false_positive_rate() -> None:
    power = simulate_power(_pilot(40), lift=0.0, seed=1, trials=100, resamples=100)
    assert power < 0.25


def test_a_large_effect_is_detected_almost_always() -> None:
    power = simulate_power(_pilot(40), lift=0.6, seed=1, trials=100, resamples=100)
    assert power > 0.9


def test_power_rises_with_the_effect() -> None:
    small = simulate_power(_pilot(40), lift=0.1, seed=1, trials=100, resamples=100)
    large = simulate_power(_pilot(40), lift=0.4, seed=1, trials=100, resamples=100)
    assert large > small


def test_power_rises_with_the_sample() -> None:
    few = simulate_power(_pilot(15), lift=0.2, seed=1, trials=100, resamples=100)
    many = simulate_power(_pilot(120), lift=0.2, seed=1, trials=100, resamples=100)
    assert many > few


def test_simulate_power_is_deterministic_for_a_seed() -> None:
    a = simulate_power(_pilot(30), lift=0.2, seed=5, trials=60, resamples=60)
    b = simulate_power(_pilot(30), lift=0.2, seed=5, trials=60, resamples=60)
    assert a == b


def test_minimum_detectable_effect_is_in_range_and_achieves_the_target() -> None:
    pilot = _pilot(60)
    mde = minimum_detectable_effect(pilot, seed=2, trials=60, resamples=60, tolerance=0.02)
    assert mde.reached_target
    assert 0.0 < mde.lift < 1.0
    achieved = simulate_power(pilot, lift=mde.lift, seed=2, trials=60, resamples=60)
    assert achieved >= 0.7


def test_a_bigger_pilot_detects_a_smaller_effect() -> None:
    small = minimum_detectable_effect(_pilot(20), seed=3, trials=60, resamples=60, tolerance=0.02)
    large = minimum_detectable_effect(_pilot(200), seed=3, trials=60, resamples=60, tolerance=0.02)
    assert large.difference < small.difference


def test_the_simulated_study_size_is_the_planned_window_not_the_pilot() -> None:
    """Section 4 asks for the MDE at the test window's change count."""
    pilot = _pilot(20)
    small = simulate_power(pilot, lift=0.2, seed=1, n_changes=20, trials=80, resamples=100)
    large = simulate_power(pilot, lift=0.2, seed=1, n_changes=200, trials=80, resamples=100)
    assert large > small


def test_the_detectable_difference_is_reported_in_exact_match_points() -> None:
    """A lift is a flip probability; the report states an exact-match difference."""
    pilot = _pilot(30)
    result = minimum_detectable_effect(pilot, seed=2, trials=40, resamples=60, tolerance=0.05)
    assert result.reached_target
    assert 0.0 < result.difference <= result.lift
    assert result.n_changes == 30


def test_a_lift_of_zero_realises_no_difference() -> None:
    assert realised_difference(_pilot(20), lift=0.0, seed=1) == 0.0


def _leaning_pilot(n: int, *, treatment: float, control: float) -> list[Cluster]:
    """A pilot whose arms already differ, the way a real pilot's observed contrast does."""
    return [Cluster(f"I{i}", (treatment, 0.0, 1.0), (control, 0.0, 1.0)) for i in range(n)]


def test_a_pilot_that_already_favours_treatment_does_not_manufacture_power() -> None:
    """Resampling the pilot as observed treated its own +difference as part of the null."""
    leaning = _leaning_pilot(40, treatment=1.0, control=0.0)
    power = simulate_power(leaning, lift=0.0, seed=3, n_changes=200, trials=80, resamples=100)
    assert power < 0.2, f"no added effect, yet power {power:.2f}"


def test_a_pilot_that_leans_either_way_gives_the_same_detectable_difference() -> None:
    up = _leaning_pilot(40, treatment=1.0, control=0.0)
    down = _leaning_pilot(40, treatment=0.0, control=1.0)
    kwargs = {"seed": 5, "n_changes": 120, "trials": 40, "resamples": 60, "tolerance": 0.05}
    a = minimum_detectable_effect(up, **kwargs)
    b = minimum_detectable_effect(down, **kwargs)
    assert abs(a.lift - b.lift) <= 0.1, f"{a.lift:.3f} against {b.lift:.3f}"
