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
