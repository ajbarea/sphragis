"""The structural filter, and why each threshold is where it is."""

from __future__ import annotations

import pytest

from sphragis.corpus.wellposed import ILL_POSED_REASONS, classify, is_well_posed


def _ex(before: str, after: str) -> dict[str, str]:
    return {"id": "x", "before": before, "after": after}


def test_a_normal_edit_is_well_posed() -> None:
    assert is_well_posed(_ex("    return x+1", "    return x + 1")) is True
    assert classify(_ex("    return x+1", "    return x + 1")) is None


def test_a_pure_deletion_is_ill_posed() -> None:
    # 21.5% of the real October corpus. The target is the empty string, which a
    # generative model cannot produce naturally, so exact match scores it a miss for a
    # structural reason rather than a modelling one.
    assert classify(_ex("import unused", "")) == "empty_after"
    assert is_well_posed(_ex("import unused", "")) is False


def test_whitespace_only_counts_as_empty() -> None:
    assert classify(_ex("x = 1", "   \n  ")) == "empty_after"


def test_a_pure_insertion_is_ill_posed() -> None:
    assert classify(_ex("", "x = 1")) == "empty_before"


def test_a_hunk_that_over_captured_is_ill_posed() -> None:
    # Worst real case anchored one line against a thirty-line block: a ratio of 1148.
    assert classify(_ex("- import_tasks: a.yml", "y" * 500)) == "expansion"


def test_a_mass_deletion_is_ill_posed() -> None:
    assert classify(_ex("z" * 500, "small")) == "contraction"


def test_the_ratio_bounds_are_inclusive() -> None:
    # Exactly 5x and exactly 0.2x are kept; the filter removes what is beyond them.
    assert is_well_posed(_ex("a" * 10, "b" * 50)) is True
    assert is_well_posed(_ex("a" * 50, "b" * 10)) is True
    assert is_well_posed(_ex("a" * 10, "b" * 51)) is False


def test_every_reason_is_declared() -> None:
    # The drop profile is reported in the Stage 1 sampling section, so a reason that is
    # returned but undeclared would go missing from the table.
    for bad in (_ex("x", ""), _ex("", "x"), _ex("x", "y" * 100), _ex("y" * 100, "x")):
        assert classify(bad) in ILL_POSED_REASONS


@pytest.mark.parametrize("ratio_max,expected", [(5.0, False), (100.0, True)])
def test_thresholds_are_parameters_not_constants(ratio_max: float, expected: bool) -> None:
    example = _ex("a" * 10, "b" * 200)
    assert is_well_posed(example, ratio_max=ratio_max) is expected
