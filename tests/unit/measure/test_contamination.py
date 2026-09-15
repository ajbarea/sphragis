"""The contamination battery: three methods, each a pre/post comparison."""

from __future__ import annotations

import math

import pytest

from sphragis.measure.contamination import (
    battery_report,
    compare_windows,
    guided_completion_rate,
    min_k_percent,
    min_k_plus_plus,
    min_k_plus_plus_scores,
)


def test_min_k_percent_averages_only_the_least_likely_tokens() -> None:
    logprobs = [-0.1, -0.2, -5.0, -6.0]
    assert min_k_percent(logprobs, k=50.0) == pytest.approx(-5.5)


def test_min_k_percent_keeps_at_least_one_token() -> None:
    assert min_k_percent([-1.0, -2.0, -3.0], k=1.0) == pytest.approx(-3.0)


def test_min_k_percent_over_memorized_text_is_higher_than_over_novel_text() -> None:
    memorized = min_k_percent([-0.01] * 10)
    novel = min_k_percent([-4.0] * 10)
    assert memorized > novel


def test_min_k_percent_rejects_an_empty_sequence() -> None:
    with pytest.raises(ValueError, match="at least one token"):
        min_k_percent([])


def test_guided_completion_rate_counts_verbatim_matches_after_normalization() -> None:
    assert guided_completion_rate([("x = 1", "x = 1"), ("a", "b")]) == 0.5
    assert guided_completion_rate([("x =  1", "x = 1")]) == 1.0
    assert guided_completion_rate([]) == 0.0


def test_compare_windows_reports_both_sides_and_the_gap() -> None:
    result = compare_windows(post_cutoff=[1.0, 2.0], pre_cutoff=[3.0, 5.0])
    assert result["post_cutoff"] == 1.5
    assert result["pre_cutoff"] == 4.0
    assert result["gap"] == pytest.approx(-2.5)


def test_min_k_plus_plus_standardizes_each_token_against_its_own_distribution() -> None:
    # (log p - mu) / sigma, checked by hand: (-1 - -2) / sqrt(4) = 0.5.
    scores, undefined = min_k_plus_plus_scores([(-1.0, -2.0, 4.0), (-3.0, -1.0, 1.0)])
    assert scores == pytest.approx([0.5, -2.0])
    assert undefined == 0


def test_min_k_plus_plus_averages_the_lowest_k_percent_of_standardized_scores() -> None:
    tokens = [(-1.0, -2.0, 4.0), (-3.0, -1.0, 1.0), (0.0, -1.0, 1.0), (-2.0, -2.0, 9.0)]
    # scores 0.5, -2.0, 1.0, 0.0; lowest 50% are -2.0 and 0.0.
    assert min_k_plus_plus(tokens, k=50.0) == pytest.approx(-1.0)


def test_a_token_the_model_expected_scores_higher_than_one_it_did_not() -> None:
    """Same raw probability, different context: Min-K%++ separates them, Min-K% cannot."""
    expected = min_k_plus_plus([(-2.0, -4.0, 1.0)] * 5)  # likelier than the typical token
    surprising = min_k_plus_plus([(-2.0, -0.5, 1.0)] * 5)  # less likely than typical
    assert expected > surprising
    assert min_k_percent([-2.0] * 5) == min_k_percent([-2.0] * 5)


@pytest.mark.parametrize("bad", [(-1.0, -1.0, 0.0), (-1.0, -1.0, -1e-9), (-math.inf, -1.0, 1.0)])
def test_a_position_with_no_defined_score_is_excluded_and_counted(
    bad: tuple[float, float, float],
) -> None:
    scores, undefined = min_k_plus_plus_scores([(-1.0, -2.0, 4.0), bad])
    assert scores == pytest.approx([0.5])
    assert undefined == 1


def test_min_k_plus_plus_refuses_a_sequence_with_no_defined_position() -> None:
    with pytest.raises(ValueError, match="defined score"):
        min_k_plus_plus([(-1.0, -1.0, 0.0)])


def test_battery_report_carries_evidence_and_never_a_verdict() -> None:
    novel = [(-4.0, -1.0, 1.0)] * 5
    memorized = [(-0.01, -1.0, 1.0)] * 5
    report = battery_report(
        post_tokens=[novel],
        pre_tokens=[memorized],
        post_completions=[("a", "b")],
        pre_completions=[("a", "a")],
        corpus_starts="2024-10-01",
        model_published="2024-09-17",
    )
    assert report["time_partition"]["corpus_starts_after_model"] is True
    assert report["min_k_plus_plus"]["gap"] < 0
    assert report["min_k_percent"]["gap"] < 0
    assert report["min_k_plus_plus"]["undefined_positions"] == {"post": 0, "pre": 0}
    assert report["guided_completion"]["gap"] < 0
    assert "verdict" not in report and "contaminated" not in report


def test_battery_report_flags_a_corpus_that_predates_the_model() -> None:
    tokens = [[(-1.0, -1.0, 1.0)]]
    report = battery_report(
        post_tokens=tokens,
        pre_tokens=tokens,
        post_completions=[("a", "a")],
        pre_completions=[("a", "a")],
        corpus_starts="2024-01-01",
        model_published="2024-09-17",
    )
    assert report["time_partition"]["corpus_starts_after_model"] is False
