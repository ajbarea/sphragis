"""Pairs cluster bootstrap, and the pre-registered pass rule as code."""

from __future__ import annotations

import pytest

from sphragis.measure.stats import (
    Cluster,
    cluster_bootstrap,
    excludes_zero,
    gate_verdict,
    paired_difference,
    percentile_ranks,
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
