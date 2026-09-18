"""Leave-one-out source attribution and the organization-versus-content table."""

from __future__ import annotations

import pytest

from sphragis.measure.attribution import (
    Source,
    accuracy,
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
