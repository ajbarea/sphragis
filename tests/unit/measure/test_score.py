"""The metric ladder: one binding metric, two reported alongside it."""

from __future__ import annotations

from sphragis.measure.score import (
    edit_similarity,
    exact_match,
    normalize_formatting,
    normalized_exact_match,
    score,
)


def test_exact_match_is_byte_identity() -> None:
    assert exact_match("x = 1", "x = 1") is True
    assert exact_match("x = 1", "x = 1 ") is False
    assert exact_match("x=1", "x = 1") is False


def test_normalize_formatting_collapses_whitespace_and_trims_blank_edges() -> None:
    assert normalize_formatting("  x   =  1  ") == "x = 1"
    assert normalize_formatting("\n\nx = 1\n\n") == "x = 1"
    assert normalize_formatting("x =\n    1") == "x = 1"


def test_normalized_exact_match_forgives_wrapping_but_not_content() -> None:
    assert normalized_exact_match("x =\n    1", "x = 1") is True
    assert normalized_exact_match("x = 1 ", "x = 1") is True
    assert normalized_exact_match("x = 2", "x = 1") is False


def test_normalized_exact_match_does_not_forgive_identifier_style() -> None:
    assert normalized_exact_match("fooBar = 1", "foo_bar = 1") is False


def test_edit_similarity_is_bounded_and_ordered() -> None:
    assert edit_similarity("abc", "abc") == 1.0
    assert edit_similarity("", "") == 1.0
    assert edit_similarity("abc", "") == 0.0
    near = edit_similarity("return x + 1", "return x + 2")
    far = edit_similarity("return x + 1", "raise ValueError()")
    assert 0.0 < far < near < 1.0


def test_score_reports_all_three_as_floats() -> None:
    result = score("x = 1", "x = 1")
    assert result == {"exact_match": 1.0, "normalized_exact_match": 1.0, "edit_similarity": 1.0}
    partial = score("x =\n    1", "x = 1")
    assert partial["exact_match"] == 0.0
    assert partial["normalized_exact_match"] == 1.0
    assert 0.0 < partial["edit_similarity"] < 1.0
