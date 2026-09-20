"""Leave-one-out source attribution and the organization-versus-content table."""

from __future__ import annotations

import pytest

from sphragis.measure.attribution import (
    Source,
    _arrangements,
    _distinct_arrangements,
    accuracy,
    group_permutation_p,
    leave_one_out,
    permutation_p,
    relation,
    relation_means,
)


def _blocks(labels: list[str], within: float, between: float) -> list[list[float]]:
    return [
        [1.0 if i == j else (within if a == b else between) for j, b in enumerate(labels)]
        for i, a in enumerate(labels)
    ]


def test_a_source_label_parses_at_every_altitude() -> None:
    s = Source.parse("qt:python/pyside-setup")
    assert (s.at("project"), s.at("organization"), s.at("content")) == (
        "qt/pyside-setup",
        "qt",
        "python",
    )


@pytest.mark.parametrize("label", ["qt", "qt:python", ":python/x", "qt:/x"])
def test_a_malformed_label_is_refused(label: str) -> None:
    with pytest.raises(ValueError, match="expected"):
        Source.parse(label)


def test_relations_run_from_most_to_least_shared() -> None:
    nova, neutron = Source.parse("os:python/nova"), Source.parse("os:python/neutron")
    docs, pyside = Source.parse("os:docs/docs"), Source.parse("qt:python/pyside")
    qtdoc = Source.parse("qt:docs/qtdoc")
    assert relation(nova, nova) == "same project"
    assert relation(nova, neutron) == "same organization and content, other project"
    assert relation(nova, pyside) == "other organization, same content"
    assert relation(nova, docs) == "same organization, other content"
    assert relation(nova, qtdoc) == "other organization and content"


def test_separated_classes_are_recovered_exactly() -> None:
    labels = ["a"] * 4 + ["b"] * 4
    assert accuracy(_blocks(labels, 0.5, 0.1), labels) == 1.0


def test_indistinguishable_classes_fall_to_a_tie_broken_by_name() -> None:
    labels = ["a"] * 3 + ["b"] * 3
    # 0.25 is exact in binary; 0.2 is not, and its rounding breaks the tie by class size.
    predictions = leave_one_out(_blocks(labels, 0.25, 0.25), labels)
    assert predictions == ["a"] * 6


def test_the_update_itself_never_votes() -> None:
    """Its cosine with itself is 1; counted, every update would pick its own class."""
    labels = ["a", "a", "b", "b"]
    cosine = _blocks(labels, 0.0, 0.3)
    assert accuracy(cosine, labels) == 0.0


def test_a_clear_separation_is_significant_and_none_is_not() -> None:
    labels = ["a"] * 5 + ["b"] * 5
    assert permutation_p(_blocks(labels, 0.6, 0.1), labels, draws=500, seed=0) < 0.01
    assert permutation_p(_blocks(labels, 0.25, 0.25), labels, draws=500, seed=0) > 0.2


def test_relation_means_count_each_pair_once() -> None:
    sources = [Source.parse(x) for x in ("o:p/a", "o:p/a", "q:p/b")]
    cosine = [[1, 0.5, 0.1], [0.5, 1, 0.3], [0.1, 0.3, 1]]
    table = relation_means(cosine, sources)
    assert table["same project"] == {"mean": 0.5, "pairs": 1}
    assert table["other organization, same content"] == {"mean": pytest.approx(0.2), "pairs": 2}


def test_distinct_arrangements_are_counted_and_listed_once_each() -> None:
    items = ["a"] * 6 + ["b"] * 7
    assert _arrangements(items) == 1716
    listed = list(_distinct_arrangements(["a", "a", "b", "c"]))
    assert len(listed) == len(set(listed)) == _arrangements(["a", "a", "b", "c"]) == 12


def test_leaving_the_group_out_removes_what_siblings_alone_explain() -> None:
    """Projects recognisable only from their own siblings carry no organization signal."""
    groups = ["p1", "p1", "p2", "p2", "p3", "p3", "p4", "p4"]
    labels = ["x", "x", "x", "x", "y", "y", "y", "y"]
    cosine = [
        [1.0 if i == j else (0.5 if g == h else 0.1) for j, h in enumerate(groups)]
        for i, g in enumerate(groups)
    ]
    assert accuracy(cosine, labels) == 1.0
    predictions_without_siblings = accuracy(cosine, labels, groups)
    assert predictions_without_siblings < 1.0


def test_an_organization_shared_across_its_projects_survives_leaving_them_out() -> None:
    groups = [f"p{i // 2}" for i in range(12)]
    labels = ["x"] * 6 + ["y"] * 6
    cosine = [
        [1.0 if i == j else (0.4 if labels[i] == labels[j] else 0.1) for j in range(12)]
        for i in range(12)
    ]
    assert accuracy(cosine, labels, groups) == 1.0
    p, used = group_permutation_p(cosine, labels, groups, draws=1000, seed=0)
    assert used == 20  # C(6, 3) relabelings of six projects, three per organization
    assert p == pytest.approx(2 / 20)  # the observed labelling and its mirror image


def test_a_group_with_two_labels_is_refused() -> None:
    with pytest.raises(ValueError, match="more than one label"):
        group_permutation_p([[1, 0], [0, 1]], ["x", "y"], ["g", "g"], draws=10, seed=0)
