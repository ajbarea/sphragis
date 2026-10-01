"""The design-weighted estimator that sizes GitHub candidate organizations."""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


report = _script("github_sizing_report")
sizing = _script("github_sizing")
conversion = _script("github_conversion")


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


def _comment(login: str, *, at: str = "2025-01-01T00:00:00Z", **extra: object) -> dict:
    return {
        "user": {"login": login, "type": "User"},
        "line": 3,
        "created_at": at,
        "path": "src/a.py",
        "body": "rename this",
        **extra,
    }


def test_a_thread_is_a_reviewer_opening_on_a_line_before_the_last_commit() -> None:
    author = {"login": "dev", "type": "User"}
    comments = [
        _comment("rev"),
        _comment("rev", in_reply_to_id=1),
        _comment("rev", line=None),
        _comment("dev"),
        _comment("rev", at="2025-02-01T00:00:00Z"),
        _comment("rev", body="```suggestion\nx = 1\n```"),
        _comment("helper[bot]"),
    ]
    counted = sizing.pr_threads(comments, ["2025-01-02T00:00:00Z"], author)
    assert counted == {
        "threads": 1,
        "suggestion_threads": 1,
        "bot_threads": 1,
        "top_dirs": ["src"],
    }


def test_only_the_first_page_of_comments_is_counted_as_the_sizing_read_it() -> None:
    comments = [_comment("rev")] * (sizing.PAGE + 5)
    counted = sizing.pr_threads(comments, ["2025-01-02T00:00:00Z"], {"login": "dev"})
    assert counted["threads"] == sizing.PAGE


def test_the_conversion_interval_brackets_the_pooled_ratio() -> None:
    prs = [(4, 1), (0, 1), (10, 2), (2, 0), (6, 2)]
    low, high = conversion.ratio_interval(prs, random.Random(0))
    assert low <= sum(e for _, e in prs) / sum(t for t, _ in prs) <= high
    assert (low, high) == conversion.ratio_interval(prs, random.Random(0))


def test_an_organization_without_its_own_pilot_borrows_the_measured_span(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    draws = []
    for org in ("alpha", "beta"):
        path = tmp_path / f"draws-{org}.jsonl"
        rows = [
            {"org": org, "draw": 0, "month": m, "days": 1, "n_day": 1, "threads": 1000}
            for m in report.TRAIN_MONTHS
        ]
        path.write_text("\n".join(json.dumps(r) for r in [*rows, {"org": org, "done": True}]))
        draws.append(str(path))
    measured = []
    for org, span in (("alpha", [0.1, 0.3]), ("gamma", [0.2, 0.5])):
        path = tmp_path / f"conversion-{org}.json"
        fields = {"month": "2024-11", "prs": 9, "reviewer_threads": 50, "refined": 10}
        path.write_text(json.dumps({"org": org, **fields, "rate": 0.2, "rate_95": span}))
        measured.append(str(path))
    out = tmp_path / "report.json"
    argv = ["x", "--draws", *draws, "--github", *measured, "--out", str(out)]
    monkeypatch.setattr(sys, "argv", argv)
    report.main()
    orgs = json.loads(out.read_text())["orgs"]
    total = 1000 * len(report.TRAIN_MONTHS)
    assert orgs["alpha"]["conversion"] == "own pilot"
    assert orgs["beta"]["conversion"] == "borrowed"
    assert orgs["alpha"]["examples_range"] == [round(total * 0.1), round(total * 0.3)]
    assert orgs["beta"]["examples_range"] == [round(total * 0.1), round(total * 0.5)]


class _Census:
    """A route listing of three PRs: one reviewed, one by a bot, one gone since collection."""

    def merged_prs(self, owner: str, day: str, *, qualifier: str) -> list[dict]:
        if not day.endswith("-01"):
            return []
        people = {"id": 1, "login": "dev", "type": "User"}
        bot = {"id": 2, "login": "deps[bot]", "type": "Bot"}
        return [
            {"repo": "o/r", "number": 1, "user": people},
            {"repo": "o/r", "number": 2, "user": bot},
            {"repo": "o/r", "number": 3, "user": people},
        ]

    def pr_comments(self, repo: str, number: int) -> list[dict]:
        if number == 3:
            raise conversion.Gone("404")
        return [_comment("rev"), _comment("rev", body="```suggestion\nx\n```")]

    def graphql(self, query: str, variables: dict) -> dict:
        nodes = [{"commit": {"committedDate": "2025-01-02T00:00:00Z"}}]
        return {"repository": {"pullRequest": {"commits": {"nodes": nodes}}}}


def _record(**extra: object) -> dict:
    return {"listed": 3, "rows": 1, "failed": 0, "failures": [], "github_rules": "x", **extra}


def test_the_census_counts_listed_prs_with_the_sizing_rule() -> None:
    per_pr = conversion.census(_Census(), "llvm", "2025-01")
    assert [(r["pr"], r.get("threads"), r.get("gone", False)) for r in per_pr] == [
        ("o/r#1", 1, False),
        ("o/r#2", 0, False),
        ("o/r#3", None, True),
    ]


def test_the_conversion_pools_counted_prs_and_recounts_the_month_draws() -> None:
    per_pr = conversion.census(_Census(), "llvm", "2025-01")
    draws = [{"repo": "o/r", "number": 1, "threads": 1, "suggestion_threads": 1}]
    report = conversion.summarize(
        per_pr, _record(), Counter({"o/r#1": 3}), Counter({"o/r#1": 2, "o/r#2": 1}), draws
    )
    assert (report["refined"], report["reviewer_threads"], report["rate"]) == (3, 1, 3.0)
    assert report["refined_from_zero_thread_prs"] == 1 and report["prs_gone"] == 1
    assert (report["draws_recounted"], report["draws_agreeing"]) == (1, 1)


def test_a_failed_pr_counts_on_neither_side() -> None:
    per_pr = conversion.census(_Census(), "llvm", "2025-01")
    record = _record(failed=1, failures=["o/r#2: GitHub kept refusing"])
    report = conversion.summarize(per_pr, record, Counter(), Counter({"o/r#1": 2}), [])
    assert report["prs_counted"] == 1 and report["prs_failed"] == 1 and report["refined"] == 2


@pytest.mark.parametrize(
    ("record", "refined", "match"),
    [
        (_record(listed=4), Counter(), "not the same month"),
        (_record(), Counter({"o/r#9": 1}), "listing lacks"),
        (_record(failed=60, failures=["o/r#2: x"]), Counter(), "rerun the fetch"),
    ],
)
def test_the_conversion_refuses_a_month_it_cannot_read(
    record: dict, refined: Counter, match: str
) -> None:
    per_pr = conversion.census(_Census(), "llvm", "2025-01")
    with pytest.raises(SystemExit, match=match):
        conversion.summarize(per_pr, record, Counter(), refined, [])


def test_a_month_with_no_reviewer_threads_has_no_conversion() -> None:
    per_pr = [{"pr": "o/r#1", "bot_or_ai_author": True, "threads": 0}]
    with pytest.raises(SystemExit, match="no reviewer threads"):
        conversion.summarize(per_pr, _record(listed=1), Counter(), Counter(), [])


def test_a_resample_without_threads_is_drawn_again() -> None:
    low, high = conversion.ratio_interval([(0, 1), (2, 1)], random.Random(0))
    assert 0.5 <= low <= high <= 1.0, "every replicate holds the reviewed PR at least once"
