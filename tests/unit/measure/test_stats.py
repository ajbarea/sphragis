"""Pairs cluster bootstrap, and the pre-registered pass rule as code."""

from __future__ import annotations

from collections.abc import Callable
from statistics import fmean

import pytest

from sphragis.measure.stats import (
    ESTIMATORS,
    Cluster,
    change_averaged_difference,
    cluster_bootstrap,
    crossed_bootstrap,
    excludes_zero,
    gate_verdict,
    paired_difference,
    percentile_interval,
    percentile_ranks,
    stratified_crossed_draws,
    supports_direction,
)


def _clusters(n: int, treatment: float, control: float, size: int = 4) -> list[Cluster]:
    return [Cluster(f"I{i}", tuple([treatment] * size), tuple([control] * size)) for i in range(n)]


def test_paired_difference_pools_examples_across_clusters() -> None:
    clusters = [Cluster("I1", (1.0, 0.0), (0.0, 0.0)), Cluster("I2", (1.0,), (1.0,))]
    assert paired_difference(clusters) == pytest.approx(2 / 3 - 1 / 3)


def test_paired_difference_is_zero_when_arms_agree() -> None:
    assert paired_difference(_clusters(5, 1.0, 1.0)) == 0.0


def _mixed(n: int) -> list[Cluster]:
    """Heterogeneous clusters, so different resamples actually give different draws."""
    return [
        Cluster(f"I{i}", tuple([float(i % 2)] * (1 + i % 3)), tuple([float(i % 3 == 0)] * 2))
        for i in range(n)
    ]


def test_cluster_bootstrap_is_deterministic_for_a_seed() -> None:
    clusters = _mixed(30)
    a = cluster_bootstrap(clusters, seed=7, resamples=500)
    b = cluster_bootstrap(clusters, seed=7, resamples=500)
    assert a == b
    assert cluster_bootstrap(clusters, seed=8, resamples=500) != a


def test_a_homogeneous_sample_gives_the_same_interval_under_any_seed() -> None:
    # Not a determinism bug: if every cluster is identical, every resample is identical.
    clusters = _clusters(30, 1.0, 0.0)
    assert cluster_bootstrap(clusters, seed=7, resamples=200) == cluster_bootstrap(
        clusters, seed=8, resamples=200
    )


def test_cluster_bootstrap_interval_brackets_the_estimate() -> None:
    result = cluster_bootstrap(_clusters(40, 1.0, 0.0), seed=1, resamples=500)
    assert result["low"] <= result["estimate"] <= result["high"]
    assert result["resamples"] == 500


def test_a_clear_effect_produces_an_interval_excluding_zero() -> None:
    result = cluster_bootstrap(_clusters(40, 1.0, 0.0), seed=1, resamples=1000)
    assert excludes_zero(result) is True


def test_no_effect_produces_an_interval_containing_zero() -> None:
    result = cluster_bootstrap(_clusters(40, 1.0, 1.0), seed=1, resamples=1000)
    assert excludes_zero(result) is False


def test_resampling_is_by_cluster_not_by_example() -> None:
    clusters = [Cluster("big", tuple([1.0] * 20), tuple([0.0] * 20))]
    clusters += [Cluster(f"I{i}", (0.0,), (0.0,)) for i in range(20)]
    result = cluster_bootstrap(clusters, seed=3, resamples=1000)
    assert result["low"] < result["estimate"]


def test_cluster_bootstrap_rejects_an_empty_sample() -> None:
    with pytest.raises(ValueError, match="at least"):
        cluster_bootstrap([], seed=1)


def test_cluster_bootstrap_refuses_the_degenerate_single_cluster() -> None:
    """One cluster means every resample is that cluster: zero width, and always a pass."""
    with pytest.raises(ValueError, match="at least"):
        cluster_bootstrap([Cluster("I1", (1.0,), (0.0,))], seed=1)


def test_a_reversed_effect_is_not_a_pass() -> None:
    """The gate is directional. An interval entirely BELOW zero refutes RQ1.

    `excludes_zero` is true for it, which is why the gate must not read that.
    """
    reversed_effect = {"low": -0.30, "high": -0.10}
    supporting = {"low": 0.05, "high": 0.20}
    assert excludes_zero(reversed_effect)
    assert not supports_direction(reversed_effect)
    assert gate_verdict({"a": reversed_effect, "b": reversed_effect}) == "fail"
    assert gate_verdict({"a": supporting, "b": reversed_effect}) == "mixed"
    assert gate_verdict({"a": supporting, "b": supporting}) == "pass"


def test_percentile_ranks_match_efrons_convention_at_the_registered_settings() -> None:
    # Efron's (R+1)a convention puts the bounds at order statistics 250 and 9751, which
    # are zero-based ranks 249 and 9750.
    assert percentile_ranks(10_000, 0.95) == (249, 9750)


@pytest.mark.parametrize("confidence", [0.80, 0.90, 0.95, 0.99])
def test_percentile_ranks_exclude_equally_many_draws_at_each_end(confidence: float) -> None:
    """(1.0 - 0.90) / 2.0 is 0.04999999999999999, which truncated the lower rank alone."""
    resamples = 10_000
    low, high = percentile_ranks(resamples, confidence)
    assert low + 1 == resamples - high, f"{low + 1} below against {resamples - high} above"


def test_gate_verdict_passes_only_when_every_organization_excludes_zero() -> None:
    both = {"openstack": {"low": 0.01, "high": 0.2}, "qt": {"low": 0.02, "high": 0.3}}
    assert gate_verdict(both) == "pass"


def test_gate_verdict_fails_when_no_organization_excludes_zero() -> None:
    neither = {"openstack": {"low": -0.01, "high": 0.2}, "qt": {"low": -0.05, "high": 0.3}}
    assert gate_verdict(neither) == "fail"


def test_gate_verdict_is_mixed_when_exactly_one_excludes_zero() -> None:
    one = {"openstack": {"low": 0.01, "high": 0.2}, "qt": {"low": -0.05, "high": 0.3}}
    assert gate_verdict(one) == "mixed"


def test_gate_verdict_rejects_a_single_organization() -> None:
    with pytest.raises(ValueError, match="two organizations"):
        gate_verdict({"openstack": {"low": 0.01, "high": 0.2}})


def test_the_two_estimands_disagree_when_change_sizes_differ() -> None:
    """One big change carrying the whole effect is the case that separates them."""
    clusters = [
        Cluster("big", tuple([1.0] * 20), tuple([0.0] * 20)),
        *(Cluster(f"small{i}", (0.0,), (0.0,)) for i in range(9)),
    ]
    assert paired_difference(clusters) == pytest.approx(20 / 29)
    assert change_averaged_difference(clusters) == pytest.approx(1 / 10)


def test_the_two_estimands_agree_when_every_change_is_one_example() -> None:
    clusters = [Cluster(f"I{i}", (1.0,), (0.0,)) for i in range(10)]
    assert paired_difference(clusters) == pytest.approx(change_averaged_difference(clusters))


def test_change_averaged_difference_ignores_a_cluster_with_no_examples() -> None:
    clusters = [*_clusters(10, 1.0, 0.0), Cluster("empty", (), ())]
    assert change_averaged_difference(clusters) == pytest.approx(1.0)


def test_change_averaged_difference_is_zero_on_an_empty_sample() -> None:
    assert change_averaged_difference([]) == 0.0


def test_the_bootstrap_resamples_under_the_estimator_it_was_given() -> None:
    """Both the point estimate and the interval have to move together, or the interval
    would be built around a statistic nobody computed."""
    clusters = [
        Cluster("big", tuple([1.0] * 20), tuple([0.0] * 20)),
        *(Cluster(f"small{i}", (0.0,), (0.0,)) for i in range(12)),
    ]
    pooled = cluster_bootstrap(clusters, seed=0, resamples=500)
    averaged = cluster_bootstrap(
        clusters, seed=0, resamples=500, estimator=change_averaged_difference
    )
    assert pooled["estimate"] != pytest.approx(averaged["estimate"])
    assert averaged["low"] <= averaged["estimate"] <= averaged["high"]
    assert averaged["high"] < pooled["estimate"]


def test_cluster_bootstrap_defaults_to_the_pooled_estimand() -> None:
    """The registered behaviour must not move because the other estimand now exists."""
    clusters = _clusters(12, 1.0, 0.0)
    assert cluster_bootstrap(clusters, seed=3, resamples=200) == cluster_bootstrap(
        clusters, seed=3, resamples=200, estimator=paired_difference
    )


def test_estimators_registry_names_both_and_nothing_else() -> None:
    assert dict(ESTIMATORS) == {
        "pooled": paired_difference,
        "change_averaged": change_averaged_difference,
    }


def _shifted(clusters: list[Cluster], shift: float) -> list[Cluster]:
    """The same changes and examples, with the treatment arm moved by `shift` everywhere."""
    return [
        Cluster(c.change_id, tuple(v + shift for v in c.treatment), c.control) for c in clusters
    ]


def test_crossed_bootstrap_is_deterministic_for_a_seed() -> None:
    runs = [_mixed(20), _shifted(_mixed(20), 0.1), _shifted(_mixed(20), -0.1)]
    assert crossed_bootstrap(runs, seed=4, resamples=300) == crossed_bootstrap(
        runs, seed=4, resamples=300
    )


def test_crossed_estimate_is_the_mean_of_the_per_seed_estimates() -> None:
    runs = [_mixed(15), _shifted(_mixed(15), 0.3), _shifted(_mixed(15), 0.6)]
    expected = sum(paired_difference(run) for run in runs) / 3
    assert crossed_bootstrap(runs, seed=0, resamples=200)["estimate"] == pytest.approx(expected)


@pytest.mark.parametrize("estimator", [paired_difference, change_averaged_difference])
def test_pooling_across_seeds_equals_averaging_the_seeds(estimator) -> None:
    """The docstring's claim, which is what lets the resample average per-seed statistics."""
    runs = [_mixed(12), _shifted(_mixed(12), 0.25), _shifted(_mixed(12), -0.4)]
    pooled = estimator([cluster for run in runs for cluster in run])
    assert pooled == pytest.approx(sum(estimator(run) for run in runs) / 3)


def test_a_seed_shift_widens_the_crossed_interval_beyond_any_single_seed() -> None:
    """A shift common to every change is what one seed's interval cannot see."""
    base = _mixed(40)
    runs = [_shifted(base, -0.6), base, _shifted(base, 0.6)]
    crossed = crossed_bootstrap(runs, seed=1, resamples=2000)
    for run in runs:
        single = cluster_bootstrap(run, seed=1, resamples=2000)
        assert crossed["high"] - crossed["low"] > 1.5 * (single["high"] - single["low"])


def test_identical_seed_runs_give_the_single_seed_interval() -> None:
    """No seed variance to carry, so nothing is added: the draws depend only on the changes."""
    run = _mixed(30)
    crossed = crossed_bootstrap([run, run, run], seed=2, resamples=2000)
    single = cluster_bootstrap(run, seed=2, resamples=2000)
    assert crossed["estimate"] == pytest.approx(single["estimate"])
    width = single["high"] - single["low"]
    assert crossed["high"] - crossed["low"] == pytest.approx(width, rel=0.15)


def test_crossed_bootstrap_refuses_a_single_seed() -> None:
    with pytest.raises(ValueError, match="at least two seed runs"):
        crossed_bootstrap([_mixed(20)], seed=0, resamples=10)


def test_crossed_bootstrap_refuses_runs_over_different_changes() -> None:
    other = [Cluster(f"J{i}", c.treatment, c.control) for i, c in enumerate(_mixed(20))]
    with pytest.raises(ValueError, match="same changes"):
        crossed_bootstrap([_mixed(20), other], seed=0, resamples=10)


def test_crossed_bootstrap_refuses_runs_over_different_examples() -> None:
    """Same change ids, one change scored on an extra example in the second run."""
    first = _mixed(20)
    second = list(first)
    head = second[0]
    second[0] = Cluster(head.change_id, (*head.treatment, 1.0), (*head.control, 0.0))
    with pytest.raises(ValueError, match="same changes and examples"):
        crossed_bootstrap([first, second], seed=0, resamples=10)


def test_crossed_bootstrap_enforces_the_cluster_floor() -> None:
    with pytest.raises(ValueError, match="at least 10 clusters"):
        crossed_bootstrap([_mixed(5), _mixed(5)], seed=0, resamples=10)


def _stratum(
    wins: Callable[[int, int], bool], n: int, seeds: int = 3, size: int = 1
) -> list[list[Cluster]]:
    """`seeds` runs of `n` changes; treatment wins change i at seed k when `wins(k, i)`."""
    return [
        [
            Cluster(f"c{i}", (1.0,) * size, (0.0,) * size if wins(k, i) else (1.0,) * size)
            for i in range(n)
        ]
        for k in range(seeds)
    ]


def _draws(strata: list[list[list[Cluster]]], resamples: int = 2_000) -> list[float]:
    return stratified_crossed_draws({"c": strata}, seed=0, resamples=resamples)[1]["c"]


def test_stratified_draws_weight_every_stratum_equally_in_every_draw() -> None:
    # A small half won everywhere, a large half won nowhere and ten times the examples. With
    # no variance inside either half, every equally weighted draw is exactly 0.5; pooling by
    # examples would put every draw near 0.024.
    small = _stratum(lambda k, i: True, n=10, size=1)
    large = _stratum(lambda k, i: False, n=40, size=10)
    estimates, draws = stratified_crossed_draws({"c": [small, large]}, seed=0, resamples=200)
    assert estimates["c"] == pytest.approx(0.5)
    assert all(d == pytest.approx(0.5) for d in draws["c"])


def test_each_stratum_is_resampled_from_its_own_changes() -> None:
    # Half A is won on its first five of ten changes, half B on its last twenty of forty. A draw
    # that reused A's indices for B would only ever see B's first ten changes, all lost.
    a = _stratum(lambda k, i: i < 5, n=10)
    b = _stratum(lambda k, i: i >= 20, n=40)
    draws = _draws([a, b])
    assert fmean(draws) == pytest.approx(0.5, abs=0.02)


def test_changes_are_resampled() -> None:
    draws = _draws([_stratum(lambda k, i: i % 2 == 0, n=20)])
    assert max(draws) - min(draws) > 0.2


def test_seeds_are_resampled_and_every_seed_counts() -> None:
    # Seed 0 wins every change, seeds 1 and 2 none: the estimate is a third, and a draw that
    # never resampled seeds, or read seed 0 alone, would have no spread or sit at 1.0.
    draws = _draws([_stratum(lambda k, i: k == 0, n=12)])
    assert fmean(draws) == pytest.approx(1 / 3, abs=0.03)
    assert min(draws) == pytest.approx(0.0)
    assert max(draws) == pytest.approx(1.0)


def test_stratified_draws_share_resamples_across_contrasts() -> None:
    a = _stratum(lambda k, i: i < 5, n=12)
    _, draws = stratified_crossed_draws({"x": [a], "y": [a]}, seed=3, resamples=200)
    assert draws["x"] == draws["y"]


def test_stratified_draws_refuse_contrasts_over_different_changes() -> None:
    a = _stratum(lambda k, i: i < 5, n=12)
    b = _stratum(lambda k, i: i < 5, n=13)
    with pytest.raises(ValueError, match="different changes"):
        stratified_crossed_draws({"x": [a], "y": [b]}, seed=0, resamples=10)


def test_stratified_draws_refuse_seed_runs_over_different_changes() -> None:
    runs = _stratum(lambda k, i: i < 5, n=12)
    runs[2] = [Cluster(f"other{i}", c.treatment, c.control) for i, c in enumerate(runs[2])]
    with pytest.raises(ValueError, match="same changes"):
        stratified_crossed_draws({"x": [runs]}, seed=0, resamples=10)


def test_stratified_draws_refuse_strata_with_different_seed_counts() -> None:
    with pytest.raises(ValueError, match="seed count"):
        stratified_crossed_draws(
            {
                "x": [
                    _stratum(lambda k, i: i < 5, 12, seeds=3),
                    _stratum(lambda k, i: i < 5, 12, seeds=5),
                ]
            },
            seed=0,
            resamples=10,
        )


def test_stratified_draws_enforce_the_cluster_floor_per_stratum() -> None:
    with pytest.raises(ValueError, match="floor"):
        stratified_crossed_draws(
            {"x": [_stratum(lambda k, i: i < 5, 12), _stratum(lambda k, i: i < 2, 4)]},
            seed=0,
            resamples=10,
        )


@pytest.mark.parametrize(
    ("confidence", "expected"), [(0.95, (249.0, 9750.0)), (0.975, (124.0, 9875.0))]
)
def test_percentile_interval_reads_the_ranks_of_its_level(
    confidence: float, expected: tuple[float, float]
) -> None:
    assert percentile_interval([float(i) for i in range(10_000)], confidence) == expected
