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


def test_score_reports_the_whole_ladder_as_floats() -> None:
    result = score("x = 1", "x = 1")
    assert result == {
        "exact_match": 1.0,
        "exact_match_raw": 1.0,
        "normalized_exact_match": 1.0,
        "edit_similarity": 1.0,
    }
    partial = score("x =\n    1", "x = 1")
    assert partial["exact_match"] == 0.0
    assert partial["normalized_exact_match"] == 1.0
    assert 0.0 < partial["edit_similarity"] < 1.0


def test_extract_code_takes_the_fenced_block_and_drops_the_preamble() -> None:
    from sphragis.measure.score import extract_code

    raw = "Here is the revised code:\n\n```python\nreturn x + 1\n```"
    assert extract_code(raw) == "return x + 1"


def test_extract_code_handles_a_fence_with_no_language_tag() -> None:
    from sphragis.measure.score import extract_code

    assert extract_code("```\nx = []\n```") == "x = []"


def test_extract_code_returns_bare_output_unchanged() -> None:
    from sphragis.measure.score import extract_code

    assert extract_code("x = []") == "x = []"


def test_extract_code_takes_the_first_block_when_several_are_emitted() -> None:
    from sphragis.measure.score import extract_code

    raw = "before:\n```python\nold\n```\nafter:\n```python\nnew\n```"
    assert extract_code(raw) == "old"


def test_extract_code_preserves_indentation_inside_the_block() -> None:
    from sphragis.measure.score import extract_code

    assert extract_code("```python\n    return x + 1\n```") == "    return x + 1"


def test_score_reports_both_raw_and_extracted_exact_match() -> None:
    raw = "Here is the revised code:\n\n```python\nx = []\n```"
    result = score(raw, "x = []")
    assert result["exact_match"] == 1.0, "the pass-rule metric scores the extracted code"
    assert result["exact_match_raw"] == 0.0, "the unextracted score stays visible"
