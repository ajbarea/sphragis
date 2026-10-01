"""The design-weighted estimator that sizes GitHub candidate organizations."""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "github_sizing_report", ROOT / "scripts" / "github_sizing_report.py"
)
assert _spec is not None and _spec.loader is not None
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)


def _draw(month: str, n_day: int, threads: int, *, days: int = 2, ai: bool = False) -> dict:
    return {"month": month, "days": days, "n_day": n_day, "threads": threads, "ai_author": ai}


def test_the_two_stage_estimate_is_unbiased_over_every_possible_draw() -> None:
    """Day 1 holds PRs with 0 and 4 threads, day 2 one PR with 2: the month's total is 6."""
    population = {1: [0, 4], 2: [2]}
    outcomes = [
        (1 / len(population)) * (1 / len(prs)) * (len(population) * len(prs) * y)
        for prs in population.values()
        for y in prs
    ]
    assert sum(outcomes) == pytest.approx(6.0)
    draws = [_draw("2025-01", len(prs), y) for prs in population.values() for y in prs]
    weights = [1 / len(population) / len(prs) for prs in population.values() for _ in prs]
    month = report.estimate(draws, "threads")["2025-01"]
    assert sum(w * x for w, x in zip(weights, month, strict=True)) == pytest.approx(6.0)


def test_empty_days_and_bot_or_ai_authored_prs_count_zero() -> None:
    draws = [_draw("2025-01", 0, 9), _draw("2025-01", 3, 9, ai=True), _draw("2025-01", 3, 1)]
    assert report.estimate(draws, "threads")["2025-01"] == [0, 0, 6]


def test_the_total_sums_month_means_and_the_interval_brackets_it() -> None:
    draws = [_draw("2025-01", 5, y) for y in (0, 1, 2, 3)] + [_draw("2025-02", 5, 2)] * 4
    by_month = report.estimate(draws, "threads")
    assert report.total(by_month) == pytest.approx(2 * 5 * 1.5 + 2 * 5 * 2)
    low, high = report.interval(by_month, random.Random(0))
    assert low <= report.total(by_month) <= high
