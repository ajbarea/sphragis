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


def _stop_rule_run(tmp_path: Path, monkeypatch, name: str, cache: type = dict) -> dict:
    """The stop rule on a pool that holds only its covering project, as Chromium's did."""
    import json
    import sys

    from sphragis.experiment import holdout

    copied = "int qt_only(const QString& name) { return name.size(); }"

    def row(i: int, path: str, before: str) -> dict:
        return {
            "id": f"r{i}",
            "before": before,
            "after": before + " + 1",
            "project": "chromium/src" if i < 9 else "qt/qtbase",
            "path": path,
            "created": f"2025-0{1 + i % 8}-15",
        }

    rows = {
        "chromium": [row(1, "a.cc", copied), row(2, "b.cc", "int a = 1;"), row(3, "c.py", "x")],
        "qt": [row(9, "q.cpp", copied)],
    }
    for module in (cover, holdout):
        monkeypatch.setattr(module, "refined_examples", lambda root, org: list(rows[org]))
    monkeypatch.setattr(cover, "_DEDUPED", cache())
    monkeypatch.setattr(cover, "_CLEARED", cache())
    need = {
        "need": {"in_language": {"examples": 1}, "other": {"examples": 1}},
        "draws": {"4": {"qt-a": {"in_language": 1, "other": 1}}},
    }
    order = {"order": [{"project": "chromium/src"}, {"project": "v8/v8"}]}
    (tmp_path / "need.json").write_text(json.dumps(need))
    (tmp_path / "order.json").write_text(json.dumps(order))
    out = tmp_path / f"{name}.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["c", "--source", "chromium", "--root", str(tmp_path / "pool")]
        + ["--need", str(tmp_path / "need.json"), "--order", str(tmp_path / "order.json")]
        + ["--against", f"qt={tmp_path / 'qt'}", "--out", str(out)],
    )
    cover.main()
    report = json.loads(out.read_text())
    report.pop("provenance")
    return report


def test_the_stop_rule_deduplicates_the_pool_once(tmp_path: Path, monkeypatch) -> None:
    """The pool, its whole order and its covering prefix are one set of rows here: one dedup,
    and Qt's one, where each used to run its own."""
    from sphragis.corpus import pipeline

    calls: list[int] = []
    real = pipeline.dedup

    def counted(examples, **options):
        calls.append(len(examples))
        return real(examples, **options)

    monkeypatch.setattr(pipeline, "dedup", counted)
    report = _stop_rule_run(tmp_path, monkeypatch, "once")
    assert report["stop_rule"]["projects"] == 1
    assert sorted(calls) == [1, 3]


class _Forgetful(dict):
    """A cache that never holds an entry."""

    def __contains__(self, key: object) -> bool:
        return False


def test_the_cached_stop_rule_reports_what_the_uncached_one_does(
    tmp_path: Path, monkeypatch
) -> None:
    cached = _stop_rule_run(tmp_path, monkeypatch, "cached")
    uncached = _stop_rule_run(tmp_path, monkeypatch, "uncached", cache=_Forgetful)
    assert cached == uncached
