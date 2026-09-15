"""Outcome-neutral tests, each exercised passing and failing, with no model."""

from __future__ import annotations

from typing import Any

import pytest

from sphragis.experiment.neutral import (
    Check,
    apparatus_holds,
    leakage_check,
    manipulation_check,
    near_duplicate_rate,
    non_degeneracy,
    positive_control,
)


def _rows(n_changes: int, value: float, per_change: int = 2) -> list[dict[str, Any]]:
    return [
        {"id": f"I{c}:{e}", "change_id": f"I{c}", "exact_match": value}
        for c in range(n_changes)
        for e in range(per_change)
    ]


def test_positive_control_passes_when_the_adapter_clearly_beats_base() -> None:
    check = positive_control(_rows(20, 1.0), _rows(20, 0.0), label="alpha", bootstrap_seed=0)
    assert check.passed and check.name == "positive_control:alpha"
    assert check.evidence["interval"]["low"] > 0


def test_positive_control_fails_when_adaptation_does_nothing() -> None:
    check = positive_control(_rows(20, 0.5), _rows(20, 0.5), label="alpha", bootstrap_seed=0)
    assert not check.passed


def test_manipulation_passes_when_loss_falls_and_the_adapter_moved() -> None:
    losses = [1.0, 0.9, 0.8, 0.7, 0.5, 0.4, 0.3, 0.2]
    assert manipulation_check(losses, epochs=2, adapter_weight_norm=3.2, label="a").passed


def test_manipulation_reads_epoch_means_not_the_noisy_last_step() -> None:
    """A last step holding one example can land anywhere; the final epoch's mean decides."""
    losses = [1.0, 0.9, 0.8, 1.5, 0.3, 0.2, 0.1, 2.0]
    check = manipulation_check(losses, epochs=2, adapter_weight_norm=1.0, label="a")
    assert check.passed
    assert check.evidence["final_span_mean_loss"] < check.evidence["first_span_mean_loss"]


@pytest.mark.parametrize(
    ("losses", "norm"),
    [([0.5, 0.5, 0.5, 0.5], 1.0), ([1.0, 0.5, 0.4, 0.3], 0.0), ([], 1.0)],
)
def test_manipulation_fails_on_flat_loss_an_unmoved_adapter_or_no_steps(
    losses: list[float], norm: float
) -> None:
    assert not manipulation_check(losses, epochs=2, adapter_weight_norm=norm, label="a").passed


def _pair(example_id: str, before: str, after: str) -> dict[str, Any]:
    return {"id": example_id, "before": before, "after": after}


def test_near_duplicate_rate_counts_held_out_examples_that_repeat_training() -> None:
    train = [_pair("t1", "value = compute(a, b)", "value = compute(a, b, c)")]
    held_out = [
        _pair("h1", "value = compute(a, b)", "value = compute(a, b, c)"),
        _pair("h2", "totally different code here", "and a different fix entirely"),
    ]
    rate, hits = near_duplicate_rate(train, held_out)
    assert rate == 0.5 and hits == ["h1"]


def test_leakage_check_compares_against_the_threshold_it_was_given() -> None:
    train = [_pair("t1", "value = compute(a, b)", "value = compute(a, b, c)")]
    held_out = [_pair("h1", "value = compute(a, b)", "value = compute(a, b, c)")] + [
        _pair(f"h{i}", f"unrelated {i} before text", f"unrelated {i} after text")
        for i in range(2, 11)
    ]
    assert not leakage_check(train, held_out, max_rate=0.05, label="alpha").passed
    passing = leakage_check(train, held_out, max_rate=0.2, label="alpha")
    assert passing.passed and passing.evidence["rate"] == pytest.approx(0.1)


def test_non_degeneracy_fails_on_a_condition_stuck_at_zero_or_one() -> None:
    ok = {"base|a": _rows(3, 0.0) + _rows(1, 1.0), "adapter:a|a|s1": _rows(3, 1.0) + _rows(1, 0.0)}
    assert non_degeneracy(ok).passed
    stuck = {**ok, "base|b": _rows(4, 0.0)}
    check = non_degeneracy(stuck)
    assert not check.passed and check.evidence["degenerate"] == ["base|b"]


def test_apparatus_holds_only_when_every_check_passed() -> None:
    assert apparatus_holds([Check("a", True), Check("b", True)])
    assert not apparatus_holds([Check("a", True), Check("b", False)])
    assert not apparatus_holds([]), "no checks run is not an apparatus that holds"
