"""What an aggregate of client updates reveals, RQ2's secure-aggregation threat model."""

from __future__ import annotations

import math
import random

import pytest

from sphragis.measure import aggregate
from sphragis.measure.aggregate import (
    direction_score,
    gram,
    membership_auc,
    organization_membership_auc,
    paired_subset_difference,
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


def test_the_reference_split_does_not_follow_the_order_clients_arrive_in() -> None:
    """Clients arrive grouped by project; an ordered split measured project distance instead."""
    rng = random.Random(7)
    first = [[rng.gauss(0, 1) for _ in range(40)] for _ in range(6)]
    second = [[v + 6.0 * (d == 0) for d, v in enumerate(row)] for row in first]
    # Six clients of one project, then six of another, in that order, as a listing gives them.
    vectors = first + second + [[rng.gauss(0, 1) for _ in range(40)] for _ in range(24)]
    products = [[sum(a * b for a, b in zip(u, v, strict=True)) for v in vectors] for u in vectors]
    detected = organization_membership_auc(
        products, members=range(12), everyone=range(36), size=8, draws=200, seed=0, splits=8
    )
    assert detected["auc"] > 0.5, "an ordered split can run the detector backwards"
    assert detected["auc_sd_over_splits"] < 0.25, "and not by luck of one split"


def test_a_round_that_cannot_be_filled_without_the_target_is_refused() -> None:
    """The stranger baseline holds one outsider out, which once left the sample short and
    crashed the attack after it had printed its results and before it wrote any."""
    products = _products(_vectors(20, 12, seed=30))
    with pytest.raises(ValueError, match="needs 8 others"):
        paired_subset_difference(
            products, target=0, others=range(1, 8), reference=range(8, 11), size=8, draws=5, seed=0
        )


def test_splitting_over_groups_stops_a_project_being_read_as_its_organization() -> None:
    """Two projects that look nothing alike still belong to one organization: split by client and
    the detector finds the target's own project in its reference and calls it the organization."""
    rng = random.Random(11)
    dimension = 60
    first = [[rng.gauss(0, 1) for _ in range(dimension)] for _ in range(6)]
    second = [[rng.gauss(0, 1) for _ in range(dimension)] for _ in range(6)]
    # Each project shares a direction of its own; the organization shares nothing.
    for rows, axis in ((first, 0), (second, 1)):
        for row in rows:
            row[axis] += 9.0
    outsiders = [[rng.gauss(0, 1) for _ in range(dimension)] for _ in range(24)]
    vectors = first + second + outsiders
    products = [[sum(a * b for a, b in zip(u, v, strict=True)) for v in vectors] for u in vectors]
    groups = ["p1"] * 6 + ["p2"] * 6 + [f"o{i}" for i in range(24)]
    by_client = organization_membership_auc(
        products, members=range(12), everyone=range(36), size=8, draws=150, seed=0
    )
    by_group = organization_membership_auc(
        products, members=range(12), everyone=range(36), size=8, draws=150, seed=0, groups=groups
    )
    assert by_client["auc"] > 0.8, "the project's own clients carry it"
    assert by_group["auc"] < by_client["auc"] - 0.2, "and must not, once the split is by project"
    assert by_group["split_over_groups"] is True


def test_an_organization_of_one_group_cannot_be_told_from_that_group() -> None:
    products = _products(_vectors(20, 10, seed=12))
    with pytest.raises(ValueError, match="two groups"):
        organization_membership_auc(
            products,
            members=range(4),
            everyone=range(20),
            size=4,
            draws=10,
            seed=0,
            groups=["only"] * 20,
        )


def test_the_low_false_positive_point_is_what_an_auc_can_hide() -> None:
    """A detector can order well on average and still catch nobody at a usable threshold."""
    # Members sit just above the bulk of non-members, but ten non-members outrank every member:
    # the ordering is good (high AUC) and no threshold with a 1% false-positive rate catches one.
    absent = [float(i) for i in range(100)] + [1000.0 + i for i in range(10)]
    present = [99.5 for _ in range(100)]
    assert aggregate.tpr_at_fpr(present, absent, 0.01)["tpr"] == 0.0
    assert aggregate.tpr_at_fpr(present, absent, 0.20)["tpr"] == 1.0
    points = aggregate._operating_points(present, absent)
    assert points["auc"] > 0.88, "the average-case metric calls this a strong attack"
    assert points["tpr_at_1pct_fpr"] == 0.0, "and the operating point says it catches nobody"


def test_the_achieved_false_positive_rate_is_reported_not_the_requested_one() -> None:
    """Twenty draws cannot resolve 1%, and the point says so rather than implying it did."""
    point = aggregate.tpr_at_fpr([1.0] * 20, [float(i) for i in range(20)], 0.01)
    assert point["fpr_achieved"] == 0.0, "no non-member may outrank the threshold at m = 0"
    assert point["resolution"] == 0.05
    assert point["tpr"] == 0.0, "a member tied with the top non-member does not outrank it"


def test_a_separated_detector_reaches_its_members_at_a_low_false_positive_rate() -> None:
    point = aggregate.tpr_at_fpr(
        [100.0 + i for i in range(50)], [float(i) for i in range(50)], 0.01
    )
    assert point["tpr"] == 1.0
    assert point["fpr_achieved"] == 0.0
