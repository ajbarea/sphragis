"""A foreign pool covers a draw only when it holds the draw's C++ and other examples both."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "foreign_pool_coverage", ROOT / "scripts" / "foreign_pool_coverage.py"
)
assert _spec is not None and _spec.loader is not None
cover = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cover)


def test_a_draw_is_covered_only_when_the_pool_holds_both_strata() -> None:
    draws = {
        "4": {"qt-a": {"in_language": 10, "other": 5}, "qt-b": {"in_language": 12, "other": 3}},
        "11": {"qt-a": {"in_language": 9, "other": 6}, "qt-b": {"in_language": 10, "other": 7}},
    }
    rows = cover.coverage(10, 6, draws)
    assert [(r["seed"], r["half"], r["covered"]) for r in rows] == [
        ("4", "qt-a", True),
        ("4", "qt-b", False),
        ("11", "qt-a", True),
        ("11", "qt-b", False),
    ]
    assert all(r["covered"] for r in cover.coverage(12, 7, draws))


def test_a_partition_is_covered_only_when_both_its_arms_are() -> None:
    draws = cover.coverage(
        10,
        6,
        {
            "4": {"qt-a": {"in_language": 10, "other": 5}, "qt-b": {"in_language": 12, "other": 3}},
            "11": {"qt-a": {"in_language": 9, "other": 6}, "qt-b": {"in_language": 8, "other": 2}},
        },
    )
    assert cover.partitions_covered(draws) == 1


def _row(before: str, after: str) -> dict:
    return {"before": before, "after": after}


def test_the_pool_drops_what_copies_or_nearly_copies_the_other_organizations_rows() -> None:
    body = "int compute_total(const std::vector<int>& values) { return accumulate(values); }"
    theirs = [_row(body, body + " // done"), _row("x = 1", "x = 2")]
    pool = [
        _row(body, body + " // done"),  # a copy
        _row(body, body + " // dome"),  # a near copy
        _row("y = 1", "y = 2"),  # neither
    ]
    assert cover.against(pool, theirs) == [pool[2]]
    assert cover.against(pool, []) == pool
