"""The planted convention that calibrates the contrast."""

from __future__ import annotations

import pytest

from sphragis.experiment.planted import (
    PLANT_CONVENTIONS,
    flip_quotes,
    halves,
    is_python,
    plant,
    planted_corpora,
    to_double,
    to_single,
    would_change,
)


def _rows(n: int, *, quoted: bool = True, per_change: int = 2) -> list[dict]:
    after = "x = 'value'" if quoted else "x = 1"
    return [
        {"change_id": f"I{i}", "before": "y = 2", "after": after, "id": f"I{i}:{j}"}
        for i in range(n)
        for j in range(per_change)
    ]


def test_flip_quotes_rewrites_a_simple_literal() -> None:
    assert flip_quotes("x = 'value'") == 'x = "value"'


def test_flip_quotes_preserves_semantics_by_refusing_hard_cases() -> None:
    """A transform that half-understands a literal plants noise, not a convention."""
    assert flip_quotes("""x = 'it\\'s'""") == """x = 'it\\'s'"""
    assert flip_quotes("""x = 'say "hi"'""") == """x = 'say "hi"'"""
    assert flip_quotes("x = 'a\nb'") == "x = 'a\nb'"


def test_flip_quotes_does_not_read_an_apostrophe_as_an_opening_delimiter() -> None:
    """Prose in a diff is full of apostrophes; each one would otherwise open a literal."""
    assert flip_quotes("don't = 'x'") == 'don\'t = "x"'


def test_flip_quotes_handles_two_literals_on_one_line() -> None:
    assert flip_quotes("a = 'p' + 'q'") == 'a = "p" + "q"'


def test_flip_quotes_leaves_already_double_quoted_text_alone() -> None:
    assert flip_quotes('x = "value"') == 'x = "value"'


def test_would_change_reports_eligibility() -> None:
    assert would_change("x = 'a'")
    assert not would_change("x = 1")


def test_halves_never_split_a_change() -> None:
    left, right = halves(_rows(40), seed=0)
    assert {r["change_id"] for r in left}.isdisjoint({r["change_id"] for r in right})
    assert len(left) + len(right) == 80


def test_halves_are_about_equal() -> None:
    left, right = halves(_rows(41), seed=0)
    assert abs(len(left) - len(right)) <= 2


def test_planting_everything_changes_every_eligible_row() -> None:
    planted, report = plant(_rows(20), fraction=1.0, seed=0)
    assert all(r["after"] == 'x = "value"' for r in planted)
    assert report["realised_fraction"] == pytest.approx(1.0)
    assert report["realised_of_eligible"] == pytest.approx(1.0)


def test_planting_nothing_leaves_the_corpus_identical() -> None:
    rows = _rows(20)
    planted, report = plant(rows, fraction=0.0, seed=0)
    assert planted == rows
    assert report["changed"] == 0.0


def test_the_realised_rate_is_not_the_nominal_rate_when_rows_are_ineligible() -> None:
    """Half the corpus has no quotes to flip, so asking for all of it gets half of it."""
    rows = _rows(20) + _rows(20, quoted=False)
    _, report = plant(rows, fraction=1.0, seed=0)
    assert report["eligible"] == 40.0
    assert report["realised_fraction"] == pytest.approx(0.5)
    assert report["realised_of_eligible"] == pytest.approx(1.0)


def test_planting_touches_the_target_and_never_the_prompt() -> None:
    planted, _ = plant(_rows(10), fraction=1.0, seed=0)
    assert all(r["before"] == "y = 2" for r in planted)


def test_plant_does_not_mutate_its_input() -> None:
    rows = _rows(10)
    plant(rows, fraction=1.0, seed=0)
    assert all(r["after"] == "x = 'value'" for r in rows)


def test_plant_refuses_a_fraction_outside_the_unit_interval() -> None:
    with pytest.raises(ValueError, match="must lie in"):
        plant(_rows(4), fraction=1.5, seed=0)


def test_planted_corpora_leaves_the_first_half_untouched() -> None:
    left, right, report = planted_corpora(_rows(40), fraction=1.0, seed=0)
    assert all(r["after"] == "x = 'value'" for r in left)
    assert all(r["after"] == 'x = "value"' for r in right)
    assert report["realised_fraction"] == pytest.approx(1.0)


def test_planted_corpora_at_zero_is_a_true_null() -> None:
    """The floor of the sweep has to be two halves that differ in nothing at all."""
    left, right, report = planted_corpora(_rows(40), fraction=0.0, seed=0)
    assert report["changed"] == 0.0
    assert {r["after"] for r in left} == {r["after"] for r in right}


def test_append_marker_applies_to_any_non_empty_refinement() -> None:
    from sphragis.experiment.planted import MARKER, append_marker, would_change

    assert append_marker("x = 1") == "x = 1" + MARKER
    assert would_change("x = 1", append_marker)
    assert would_change("x = 'a'", append_marker)


def test_append_marker_is_idempotent() -> None:
    from sphragis.experiment.planted import append_marker

    once = append_marker("x = 1")
    assert append_marker(once) == once


def test_append_marker_leaves_empty_text_alone() -> None:
    from sphragis.experiment.planted import append_marker, would_change

    assert append_marker("") == ""
    assert not would_change("", append_marker)


def test_the_marker_reaches_almost_every_row_where_quotes_do_not() -> None:
    """The reason the ceiling convention exists: quote style cannot reach a strong signal."""
    from sphragis.experiment.planted import append_marker

    rows = _rows(20) + _rows(20, quoted=False)
    _, quotes = plant(rows, fraction=1.0, seed=0)
    _, marker = plant(rows, fraction=1.0, seed=0, transform=append_marker)
    assert quotes["realised_fraction"] == pytest.approx(0.5)
    assert marker["realised_fraction"] == pytest.approx(1.0)


def test_symmetric_planting_gives_each_half_its_own_convention() -> None:
    from sphragis.experiment.planted import MARKER, MARKER_OTHER, symmetric_planted_corpora

    left, right, report = symmetric_planted_corpora(_rows(40), fraction=1.0, seed=0)
    assert all(r["after"].endswith(MARKER_OTHER) for r in left)
    assert all(r["after"].endswith(MARKER) for r in right)
    assert report["a"]["realised_fraction"] == pytest.approx(1.0)
    assert report["b"]["realised_fraction"] == pytest.approx(1.0)


def test_symmetric_planting_keeps_the_conventions_distinct() -> None:
    """If the two annotations were the same, the halves would not differ at all."""
    from sphragis.experiment.planted import MARKER, MARKER_OTHER

    assert MARKER != MARKER_OTHER
    assert len(MARKER) == len(MARKER_OTHER), "neither side may get the easier rule"


def test_symmetric_planting_at_zero_is_the_same_null_as_the_asymmetric_design() -> None:
    from sphragis.experiment.planted import symmetric_planted_corpora

    left, right, report = symmetric_planted_corpora(_rows(40), fraction=0.0, seed=0)
    assert report["a"]["changed"] == report["b"]["changed"] == 0.0
    assert {r["after"] for r in left} == {r["after"] for r in right}


def test_the_two_halves_are_still_split_by_change() -> None:
    from sphragis.experiment.planted import symmetric_planted_corpora

    left, right, _ = symmetric_planted_corpora(_rows(40), fraction=0.5, seed=0)
    assert {r["change_id"] for r in left}.isdisjoint({r["change_id"] for r in right})


def test_the_quote_styles_are_mirror_images() -> None:
    assert to_double("x = 'a' + \"b\"") == 'x = "a" + "b"'
    assert to_single("x = 'a' + \"b\"") == "x = 'a' + 'b'"


@pytest.mark.parametrize(
    "hard",
    [
        '    """Doc."""',
        "    '''Doc.'''",
        "f'{x}'",
        'b"raw"',
        "it's",
        "x = 'say \"hi\"'",
        "LOG.error(\"Instance '%s' not found\", x)",
        "x = f\"{d['k']}\"",
        "x = ('unterminated",
    ],
)
def test_a_literal_the_quote_styles_half_understand_is_left_alone(hard: str) -> None:
    assert to_double(hard) == hard
    assert to_single(hard) == hard


def test_the_quote_plant_touches_python_rows_only_and_names_what_it_changed() -> None:
    rows = [
        {"id": "a", "path": "m.py", "after": "x = 'v'"},
        {"id": "b", "path": "m.php", "after": "$x = 'v';"},
        {"id": "c", "path": "m.py", "after": "x = 1"},
    ]
    transforms, applies = PLANT_CONVENTIONS["quotes"]
    out, report = plant(
        rows, fraction=1.0, seed=0, transform=transforms[1], applies=applies, record_ids=True
    )
    assert [r["after"] for r in out] == ['x = "v"', "$x = 'v';", "x = 1"]
    assert (report["eligible"], report["changed"], report["changed_ids"]) == (1.0, 1.0, ["a"])
    assert is_python({"path": "a/b.py"})
    assert not is_python({"path": None})


def test_a_plant_names_its_changed_ids_only_when_asked() -> None:
    _, report = plant([{"id": "a", "after": "x = 'v'"}], fraction=1.0, seed=0)
    assert "changed_ids" not in report


@pytest.mark.parametrize("separator", ["\x0c", " "])
def test_a_line_separator_the_tokenizer_does_not_split_on_leaves_offsets_true(separator) -> None:
    text = f"x = 1  # a{separator}b\ny = 'v'\n"
    assert to_double(text) == f'x = 1  # a{separator}b\ny = "v"\n'
