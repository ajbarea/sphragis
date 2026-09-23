"""Pilot power analysis: the minimum effect the planned window could detect."""

from __future__ import annotations

import random

import pytest

from sphragis.experiment.power import (
    lift_for_effect,
    minimum_detectable_effect,
    realised_difference,
    seed_runs,
    seed_trial,
    simulate_power,
    stratified_seed_trial,
)
from sphragis.measure.stats import Cluster, paired_difference


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


def _rows(n: int, rate: float, size: int = 4) -> list[Cluster]:
    rng = random.Random(9)
    return [
        Cluster(
            f"c{i}",
            tuple(1.0 if rng.random() < rate else 0.0 for _ in range(size)),
            tuple(1.0 if rng.random() < rate else 0.0 for _ in range(size)),
        )
        for i in range(n)
    ]


def test_the_seed_runs_are_crossed_over_one_set_of_changes() -> None:
    truth = _rows(30, 0.3)
    runs = seed_runs(truth, seeds=3, sigma_b=0.01, redraw=0.05, rng=random.Random(0))
    for run in runs:
        assert [(c.change_id, len(c.treatment), len(c.control)) for c in run] == [
            (c.change_id, len(c.treatment), len(c.control)) for c in truth
        ]


def test_churn_alone_keeps_the_contrast_where_it_was() -> None:
    truth = _rows(400, 0.3)
    base = paired_difference(truth)
    shifts = [
        paired_difference(run) - base
        for run in seed_runs(truth, seeds=40, sigma_b=0.0, redraw=0.05, rng=random.Random(1))
    ]
    assert abs(sum(shifts) / len(shifts)) < 0.003


def test_the_seed_effect_moves_contrasts_by_about_its_size() -> None:
    truth = _rows(600, 0.3)
    base = paired_difference(truth)
    shifts = [
        paired_difference(run) - base
        for run in seed_runs(truth, seeds=200, sigma_b=0.02, redraw=0.0, rng=random.Random(2))
    ]
    mean = sum(shifts) / len(shifts)
    sd = (sum((x - mean) ** 2 for x in shifts) / (len(shifts) - 1)) ** 0.5
    assert 0.016 < sd < 0.024


def test_a_seed_trial_is_deterministic_for_its_seed() -> None:
    def once():
        return seed_trial(
            _rows(30, 0.3),
            lift=0.1,
            n_changes=20,
            seeds=3,
            sigma_b=0.01,
            redraw=0.05,
            resamples=100,
            seed=5,
        )

    assert once() == once()


def test_the_lift_found_realises_the_effect_asked_for() -> None:
    pilot = _rows(60, 0.3)
    lift = lift_for_effect(pilot, 0.05, seed=3, draws=200)
    assert realised_difference(pilot, lift=lift, seed=3, draws=200) == pytest.approx(
        0.05, abs=0.003
    )


def _half(n: int, treatment: float, control: float) -> list[Cluster]:
    return [Cluster(f"c{i}", (treatment,) * 2, (control,) * 2) for i in range(n)]


def _trial(halves, lift: float, seed: int = 0):
    return stratified_seed_trial(
        halves,
        lift=lift,
        n_changes=[40, 40],
        seeds=3,
        sigma_b=0.0,
        redraw=0.0,
        resamples=400,
        seed=seed,
        confidences=[0.975, 0.95],
        sesoi=0.01,
    )


def test_a_large_lift_is_supported_at_both_levels() -> None:
    halves = [_half(30, 0.0, 0.0), _half(30, 0.0, 0.0)]
    trial = _trial(halves, lift=0.6)
    assert trial.supported == {0.975: True, 0.95: True}
    assert trial.absent == {0.975: False, 0.95: False}


def test_identical_arms_read_absent_and_unsupported() -> None:
    halves = [_half(30, 1.0, 1.0), _half(30, 0.0, 0.0)]
    trial = _trial(halves, lift=0.0)
    assert trial.absent == {0.975: True, 0.95: True}
    assert trial.supported == {0.975: False, 0.95: False}


def test_the_sign_flip_null_is_not_supported_on_average() -> None:
    halves = [_half(30, 1.0, 0.0), _half(30, 0.0, 1.0)]
    rate = sum(_trial(halves, lift=0.0, seed=s).supported[0.975] for s in range(40)) / 40
    assert rate < 0.1


def test_one_planned_size_per_half_is_required() -> None:
    with pytest.raises(ValueError, match="one planned size per half"):
        stratified_seed_trial(
            [_half(10, 0.0, 0.0)],
            lift=0.1,
            n_changes=[10, 10],
            seeds=3,
            sigma_b=0.0,
            redraw=0.0,
            resamples=100,
            seed=0,
            confidences=[0.95],
            sesoi=0.01,
        )
