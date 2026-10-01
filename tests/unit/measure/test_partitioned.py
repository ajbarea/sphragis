"""The repeated-partition interval: runs and changes crossed, each run's halves weighted equally."""

from __future__ import annotations

import random
from statistics import fmean

import pytest

from sphragis.measure.stats import Cluster, paired_difference, partitioned_crossed_draws


def _change(i: int, won: bool, size: int = 1) -> Cluster:
    return Cluster(f"c{i}", (1.0 if won else 0.0,) * size, (0.0,) * size)


def _run(n: int, half_of, won, size=lambda i: 1) -> list[list[Cluster]]:
    """One partition of changes 0..n-1 into two halves, `half_of(i)` naming each change's half."""
    halves: list[list[Cluster]] = [[], []]
    for i in range(n):
        halves[half_of(i)].append(_change(i, won(i), size(i)))
    return halves


def test_the_estimate_is_the_mean_over_runs_of_equally_weighted_halves() -> None:
    # Run 0: half a (10 changes, 1 example each) wins all, half b (30 changes, 10 examples)
    # none: equally weighted 0.5. Run 1 swaps which changes are in which half and loses all.
    run0 = _run(40, lambda i: 0 if i < 10 else 1, lambda i: i < 10, lambda i: 1 if i < 10 else 10)
    run1 = _run(40, lambda i: 0 if i >= 30 else 1, lambda i: False, lambda i: 1 if i < 10 else 10)
    estimate, draws = partitioned_crossed_draws([run0, run1], seed=0, resamples=200)
    expected = fmean([fmean(paired_difference(h) for h in run) for run in (run0, run1)])
    assert estimate == pytest.approx(expected) == pytest.approx(0.25)
    assert len(draws) == 200


def test_every_draw_weights_a_run_s_halves_equally() -> None:
    # No variance inside either half and one run: every draw is exactly 0.5 whatever the
    # resampled changes, where pooling by examples would sit near 0.03.
    run = _run(40, lambda i: 0 if i < 10 else 1, lambda i: i < 10, lambda i: 1 if i < 10 else 10)
    _, draws = partitioned_crossed_draws([run, run], seed=1, resamples=300)
    assert all(d == pytest.approx(0.5) for d in draws)


def test_runs_are_resampled() -> None:
    # Run 0 wins every change, runs 1 and 2 none: draws span 0 to 1 around a third.
    wins = _run(30, lambda i: i % 2, lambda i: True)
    loses = _run(30, lambda i: i % 2, lambda i: False)
    _, draws = partitioned_crossed_draws([wins, loses, loses], seed=2, resamples=2_000)
    assert fmean(draws) == pytest.approx(1 / 3, abs=0.03)
    assert min(draws) == pytest.approx(0.0) and max(draws) == pytest.approx(1.0)


def test_changes_are_resampled_jointly_across_runs() -> None:
    # Both runs win exactly the even changes, under different partitions; a draw that
    # resampled each run's changes separately would decouple them. Every draw has run 0 and
    # run 1 agree only if the same change multiset is applied to both.
    rng = random.Random(0)
    won = {i: i % 2 == 0 for i in range(40)}
    first = _run(40, lambda i: i % 2, won.__getitem__)
    second = _run(40, lambda i: rng.random() < 0.5, won.__getitem__)
    _, draws = partitioned_crossed_draws([first, second], seed=3, resamples=500)
    assert max(draws) - min(draws) > 0.2


def test_runs_must_score_the_same_changes_and_examples() -> None:
    base = _run(20, lambda i: i % 2, lambda i: True)
    fewer = _run(21, lambda i: i % 2, lambda i: True)
    with pytest.raises(ValueError, match="same changes"):
        partitioned_crossed_draws([base, fewer], seed=0, resamples=10)
    resized = _run(20, lambda i: i % 2, lambda i: True, lambda i: 2 if i == 0 else 1)
    with pytest.raises(ValueError, match="same changes"):
        partitioned_crossed_draws([base, resized], seed=0, resamples=10)


def test_a_change_in_both_halves_of_a_run_is_refused() -> None:
    run = _run(20, lambda i: i % 2, lambda i: True)
    run[1].append(run[0][0])
    with pytest.raises(ValueError, match="both halves"):
        partitioned_crossed_draws([run, run], seed=0, resamples=10)


def test_fewer_than_two_runs_and_thin_halves_are_refused() -> None:
    run = _run(40, lambda i: i % 2, lambda i: True)
    with pytest.raises(ValueError, match="two runs"):
        partitioned_crossed_draws([run], seed=0, resamples=10)
    thin = _run(40, lambda i: 0 if i < 5 else 1, lambda i: True)
    with pytest.raises(ValueError, match="floor"):
        partitioned_crossed_draws([thin, thin], seed=0, resamples=10)


def test_draws_are_reproducible_from_the_seed() -> None:
    runs = [_run(30, lambda i, k=k: (i * k) % 2, lambda i: i % 3 == 0) for k in (1, 3)]
    first = partitioned_crossed_draws(runs, seed=9, resamples=100)
    assert first == partitioned_crossed_draws(runs, seed=9, resamples=100)


def test_the_estimate_is_the_mean_of_each_run_s_equal_halves() -> None:
    from sphragis.measure.stats import equal_halves

    runs = [_run(30, lambda i, k=k: (i * k) % 2, lambda i, k=k: i % 3 == k % 3) for k in (1, 3)]
    estimate, _ = partitioned_crossed_draws(runs, seed=0, resamples=1)
    assert estimate == pytest.approx(fmean(equal_halves(r) for r in runs))


def test_a_replicate_that_empties_a_half_is_drawn_again_not_read_as_zero() -> None:
    # Half 0 is one change that the adapter always gets right; half 1 never differs. Every
    # replicate with both halves reads (1 + 0) / 2; one that misses the lone change must not
    # enter as (0 + 0) / 2.
    lone = Cluster("a", (1.0,), (0.0,))
    rest = [Cluster(f"b{i}", (0.0,), (0.0,)) for i in range(9)]
    runs = [[[lone], rest], [[lone], rest]]
    estimate, draws = partitioned_crossed_draws(runs, seed=3, resamples=500, min_clusters=1)
    assert estimate == 0.5
    assert set(draws) == {0.5}


def test_halves_too_small_for_any_replicate_are_refused() -> None:
    # Each run's half 0 is a different lone change, so a replicate keeps both halves of a run
    # only when it draws that run's change; with many runs nearly every replicate fails.
    changes = [f"c{i}" for i in range(12)]
    runs = [
        [
            [Cluster(c, (1.0,), (0.0,)) for c in changes if c == lone],
            [Cluster(c, (0.0,), (0.0,)) for c in changes if c != lone],
        ]
        for lone in changes
    ]
    with pytest.raises(ValueError, match="too small"):
        partitioned_crossed_draws(runs, seed=1, resamples=200, min_clusters=1)
