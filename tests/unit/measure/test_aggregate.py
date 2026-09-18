"""What an aggregate of client updates reveals, RQ2's secure-aggregation threat model."""

from __future__ import annotations

import math
import random

import pytest

from sphragis.measure.aggregate import (
    direction_score,
    gram,
    membership_auc,
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
