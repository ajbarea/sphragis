"""What an aggregate of client updates reveals, RQ2's secure-aggregation threat model."""

from __future__ import annotations

import math
import random

import pytest

from sphragis.measure.aggregate import (
    direction_score,
    gram,
    membership_auc,
    organization_membership_auc,
    paired_subset_difference,
    rounds,
)


def _vectors(n: int, dimension: int, seed: int, shift: float = 0.0) -> list[list[float]]:
    rng = random.Random(seed)
    return [
        [rng.gauss(0.0, 1.0) + (shift if d == 0 else 0.0) for d in range(dimension)]
        for _ in range(n)
    ]


def _products(vectors: list[list[float]]) -> list[list[float]]:
    return [[sum(a * b for a, b in zip(u, v, strict=True)) for v in vectors] for u in vectors]


def test_gram_rebuilds_the_inner_products_from_cosines_and_norms() -> None:
    vectors = _vectors(4, 5, seed=0)
    products = _products(vectors)
    norms = [math.sqrt(products[i][i]) for i in range(4)]
    cosine = [[products[i][j] / (norms[i] * norms[j]) for j in range(4)] for i in range(4)]
    rebuilt = gram(cosine, norms)
    for i in range(4):
        for j in range(4):
            assert rebuilt[i][j] == pytest.approx(products[i][j])


def test_an_aggregate_aligns_perfectly_with_itself() -> None:
    products = _products(_vectors(6, 8, seed=1))
    assert direction_score(products, [0, 1, 2], [0, 1, 2]) == pytest.approx(1.0)


def test_rounds_enumerate_when_few_and_sample_when_many() -> None:
    rng = random.Random(0)
    assert len(rounds(range(5), size=2, draws=100, rng=rng)) == 10
    drawn = rounds(range(40), size=3, draws=50, rng=rng)
    assert len(drawn) == 50 and len(set(drawn)) == 50


def test_a_source_with_its_own_direction_is_detectable_in_an_aggregate() -> None:
    """Target clients share a direction the others lack; the round's alignment should show it."""
    target = _vectors(6, 40, seed=2, shift=8.0)
    others = _vectors(20, 40, seed=3)
    products = _products(target + others)
    detected = membership_auc(
        products,
        target=range(3),
        others=range(6, 26),
        reference=range(3, 6),
        size=8,
        draws=200,
        seed=0,
    )
    assert detected["auc"] > 0.9
    assert detected["mean_with"] > detected["mean_without"]


def test_a_source_without_a_direction_of_its_own_is_not_detectable() -> None:
    products = _products(_vectors(26, 40, seed=4))
    detected = membership_auc(
        products,
        target=range(3),
        others=range(6, 26),
        reference=range(3, 6),
        size=8,
        draws=200,
        seed=0,
    )
    assert 0.35 < detected["auc"] < 0.65


def test_paired_subsets_recover_the_target_direction_and_not_a_stranger_s() -> None:
    target = _vectors(6, 40, seed=5, shift=8.0)
    others = _vectors(20, 40, seed=6)
    products = _products(target + others)
    own = paired_subset_difference(
        products, target=0, others=range(6, 26), reference=range(3, 6), size=8, draws=60, seed=1
    )
    stranger = paired_subset_difference(
        products, target=6, others=range(7, 26), reference=range(3, 6), size=8, draws=60, seed=1
    )
    assert own > 0.3
    assert own > stranger


def test_a_round_bigger_than_the_pool_is_refused() -> None:
    with pytest.raises(ValueError, match="needs 5 clients"):
        rounds(range(3), size=5, draws=10, rng=random.Random(0))


def test_a_mixed_round_still_betrays_an_organization_with_its_own_direction() -> None:
    """Rounds drawn from everyone, so a round without the target is not a round of strangers."""
    target = _vectors(6, 40, seed=7, shift=8.0)
    others = _vectors(24, 40, seed=8)
    products = _products(target + others)
    detected = organization_membership_auc(
        products, members=range(6), everyone=range(30), size=8, draws=150, seed=0
    )
    assert detected["auc"] > 0.9
    assert detected["mean_present"] > detected["mean_absent"]


def test_an_organization_without_a_direction_of_its_own_is_not_detectable_in_a_mixed_round() -> (
    None
):
    products = _products(_vectors(30, 40, seed=9))
    detected = organization_membership_auc(
        products, members=range(6), everyone=range(30), size=8, draws=150, seed=0
    )
    assert 0.35 < detected["auc"] < 0.65


def test_a_mixed_round_needs_enough_clients_outside_the_target() -> None:
    products = _products(_vectors(12, 5, seed=10))
    with pytest.raises(ValueError, match="needs 11 others"):
        organization_membership_auc(
            products, members=range(2), everyone=range(12), size=11, draws=10, seed=0
        )


def test_both_classes_are_scored_against_the_same_reference() -> None:
    """Different reference sizes per class read reference size, not membership: it read 1.000."""
    products = _products(_vectors(30, 40, seed=11))
    detected = organization_membership_auc(
        products, members=range(6), everyone=range(30), size=8, draws=150, seed=0
    )
    assert detected["reference_clients"] == 3.0


def test_the_reference_split_does_not_follow_the_order_clients_arrive_in() -> None:
    """Clients arrive grouped by project; an ordered split measured project distance instead."""
    rng = random.Random(7)
    first = [[rng.gauss(0, 1) for _ in range(40)] for _ in range(6)]
    second = [[v + 6.0 * (d == 0) for d, v in enumerate(row)] for row in first]
    # Six clients of one project, then six of another, in that order, as a listing gives them.
    vectors = first + second + [[rng.gauss(0, 1) for _ in range(40)] for _ in range(24)]
    products = [[sum(a * b for a, b in zip(u, v, strict=True)) for v in vectors] for u in vectors]
    detected = organization_membership_auc(
        products, members=range(12), everyone=range(36), size=8, draws=200, seed=0
    )
    assert detected["auc"] > 0.5, "an ordered split can run the detector backwards"
