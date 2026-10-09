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
    assert set(trial.high) == {0.975, 0.95}


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


def test_a_location_shift_moves_only_the_treatment_arm_by_the_effect() -> None:
    from sphragis.experiment.power import shift_by

    cluster = Cluster("c", (-1.25, -0.5), (-1.0, -0.75))
    moved = shift_by(cluster, 0.25)
    assert moved.treatment == (-1.0, -0.25) and moved.control == cluster.control
    assert paired_difference([moved]) == pytest.approx(paired_difference([cluster]) + 0.25)


def test_continuous_runs_shift_each_run_whole_by_its_own_draw() -> None:
    """A run's departure from the truth is one shift for every treatment outcome in it."""
    from sphragis.experiment.power import continuous_runs

    truth = [Cluster(f"c{i}", (-1.0 - i / 10,), (-1.2,)) for i in range(5)]
    runs = continuous_runs(truth, seeds=400, sigma_b=0.05, noise=(0.0, 0.0), rng=random.Random(3))
    shifts = []
    for run in runs:
        moved = {c.treatment[0] - t.treatment[0] for c, t in zip(run, truth, strict=True)}
        assert len({round(m, 12) for m in moved}) == 1
        assert all(c.control == t.control for c, t in zip(run, truth, strict=True))
        shifts.append(moved.pop())
    mean = sum(shifts) / len(shifts)
    sd = (sum((s - mean) ** 2 for s in shifts) / (len(shifts) - 1)) ** 0.5
    assert abs(mean) < 0.01 and 0.04 < sd < 0.06
    assert continuous_runs(truth, seeds=2, sigma_b=0.0, noise=(0.0, 0.0), rng=random.Random(3)) == [
        truth,
        truth,
    ]


def test_change_by_run_noise_has_a_shared_and_a_per_example_part() -> None:
    from sphragis.experiment.power import continuous_runs

    truth = [Cluster(f"c{i}", (-1.0, -1.0), (-1.2, -1.2)) for i in range(400)]
    (shared,) = continuous_runs(truth, seeds=1, sigma_b=0.0, noise=(0.1, 0.0), rng=random.Random(5))
    assert all(c.treatment[0] == c.treatment[1] for c in shared)
    (apart,) = continuous_runs(truth, seeds=1, sigma_b=0.0, noise=(0.0, 0.1), rng=random.Random(5))
    assert sum(c.treatment[0] != c.treatment[1] for c in apart) == len(apart)
    moved = [c.treatment[0] - t.treatment[0] for c, t in zip(apart, truth, strict=True)]
    mean = sum(moved) / len(moved)
    sd = (sum((m - mean) ** 2 for m in moved) / (len(moved) - 1)) ** 0.5
    assert abs(mean) < 0.02 and 0.085 < sd < 0.115


def _pilot_run(seed: int, contrasts: dict[str, tuple[str, str, float]]) -> tuple[dict, int]:
    """One run: each example (id -> change, half, own score), its sibling scoring -1.0."""
    a, b = "o-a", "o-b"
    results: dict = {f"adapter:{x}|{y}|s{seed}": [] for x in (a, b) for y in (a, b)}
    for example, (change, half, value) in contrasts.items():
        sibling = b if half == a else a
        results[f"adapter:{half}|{half}|s{seed}"].append(
            {"id": example, "change_id": change, "lp": value}
        )
        results[f"adapter:{sibling}|{half}|s{seed}"].append(
            {"id": example, "change_id": change, "lp": -1.0}
        )
    return results, seed


def test_averaged_truth_averages_each_example_and_splits_its_noise() -> None:
    from sphragis.experiment.power import averaged_truth

    runs = [
        _pilot_run(1, {"x": ("cx", "o-a", -0.8), "y": ("cy", "o-b", -0.9)}),
        _pilot_run(2, {"x": ("cx", "o-b", -0.6), "y": ("cy", "o-a", -0.9)}),
    ]
    truth, (shared, per_example), shrink = averaged_truth(runs, halves=("o-a", "o-b"), metric="lp")
    assert [c.change_id for c in truth] == ["cx", "cy"]
    # x's contrasts 0.2 and 0.4 around 0.3, y's 0.1 twice: squares 0.02 over four, times 2 / 1.
    assert shared == 0.0 and per_example == pytest.approx(0.1)
    # Mean contrasts 0.3 and 0.1 spread 0.02 around 0.2, of which 0.01 / 2 is noise: shrink 0.75,
    # so x's treatment is its control -1.0 plus 0.2 + 0.75 * 0.1.
    assert shrink == pytest.approx(0.75)
    assert truth[0].treatment == pytest.approx((-0.725,)) and truth[0].control == (-1.0,)


def test_examples_that_move_together_read_as_shared_noise() -> None:
    from sphragis.experiment.power import averaged_truth

    runs = [
        _pilot_run(1, {"x": ("c", "o-a", -0.8), "y": ("c", "o-a", -0.8)}),
        _pilot_run(2, {"x": ("c", "o-b", -0.6), "y": ("c", "o-b", -0.6)}),
    ]
    _, (shared, per_example), _ = averaged_truth(runs, halves=("o-a", "o-b"), metric="lp")
    # Residuals of +-0.1 on both: variance 0.01 times 2 / 1 for centering, all of it shared.
    assert shared == pytest.approx(0.02**0.5) and per_example == pytest.approx(0.0, abs=1e-6)


def test_averaged_truth_recovers_known_noise_and_shrinks_by_its_share() -> None:
    """Synthetic runs with a known spread of true contrasts and known change-by-run noise."""
    from sphragis.experiment.power import averaged_truth

    rng = random.Random(11)
    shared_sd, example_sd, signal_sd, n_runs = 0.05, 0.10, 0.06, 22
    sizes = {f"c{i}": 1 + i % 3 for i in range(150)}
    true = {(c, j): rng.gauss(0.02, signal_sd) for c, m in sizes.items() for j in range(m)}
    runs = []
    for seed in range(1, n_runs + 1):
        contrasts = {}
        for c, m in sizes.items():
            common, half = rng.gauss(0.0, shared_sd), rng.choice(("o-a", "o-b"))
            for j in range(m):
                value = -1.0 + true[c, j] + common + rng.gauss(0.0, example_sd)
                contrasts[f"{c}-{j}"] = (c, half, value)
        runs.append(_pilot_run(seed, contrasts))
    _, (shared, per_example), shrink = averaged_truth(runs, halves=("o-a", "o-b"), metric="lp")
    assert shared == pytest.approx(shared_sd, abs=0.015)
    assert per_example == pytest.approx(example_sd, rel=0.05)
    expected = signal_sd**2 / (signal_sd**2 + (shared_sd**2 + example_sd**2) / n_runs)
    assert shrink == pytest.approx(expected, abs=0.05)


@pytest.mark.parametrize("problem", ["one run", "a repeated seed", "an example changing change"])
def test_averaged_truth_refuses_runs_it_cannot_read(problem: str) -> None:
    from sphragis.experiment.power import averaged_truth

    first = _pilot_run(1, {"x": ("cx", "o-a", -0.8)})
    if problem == "one run":
        runs = [first]
    elif problem == "a repeated seed":
        runs = [first, _pilot_run(1, {"x": ("cx", "o-b", -0.6)})]
    else:
        runs = [first, _pilot_run(2, {"x": ("cz", "o-b", -0.6)})]
    with pytest.raises(ValueError):
        averaged_truth(runs, halves=("o-a", "o-b"), metric="lp")
