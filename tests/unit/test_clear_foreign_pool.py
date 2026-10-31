"""The cleared pool is written only when it is the pool the stop rule found covering."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from sphragis.corpus.load import write_derived_file
from sphragis.experiment import decomposition
from sphragis.experiment.holdout import cleared_pool, pool_digest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "clear_foreign_pool", ROOT / "scripts" / "clear_foreign_pool.py"
)
assert _spec is not None and _spec.loader is not None
clear = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(clear)

BODY = "int compute_total(const std::vector<int>& values) { return accumulate(values); }"


def _row(i: int, before: str, after: str, project: str = "chromium/src") -> dict:
    return {
        "id": f"r{i}",
        "before": before,
        "after": after,
        "project": project,
        "created": "2025-01-15",
    }


POOL = [
    _row(1, BODY, BODY + " // done"),  # a copy of Qt's
    _row(2, "y = 1", "y = 2"),
    _row(3, "z = 1", "z = 2", project="v8/v8"),  # past the prefix
]
THEIRS = [{"before": BODY, "after": BODY + " // done"}]
KEPT = [POOL[1]]


def _stop(**overrides) -> dict:
    stop = {
        "against": "qt",
        "against_examples": 1,
        "projects": 1,
        "digest": pool_digest(KEPT),
        "covers_every_draw": True,
    }
    return {**stop, **overrides}


def test_the_stop_rules_prefix_pool_is_cleared_of_the_other_organizations_copies() -> None:
    assert clear.cleared(_stop(), ["chromium/src", "v8/v8"], POOL, THEIRS) == KEPT


@pytest.mark.parametrize(
    ("stop", "theirs", "message"),
    [
        (_stop(digest="0" * 64), THEIRS, "not the stop rule's"),
        (_stop(), [], "0 unsealed qt examples, not the stop rule's 1"),
        (_stop(projects=2), THEIRS, "not the stop rule's"),
    ],
)
def test_a_pool_other_than_the_stop_rules_is_never_written(
    stop: dict, theirs: list, message: str
) -> None:
    with pytest.raises(SystemExit, match=message):
        clear.cleared(stop, ["chromium/src", "v8/v8"], POOL, theirs)


@pytest.mark.parametrize(
    ("coverage", "org", "message"),
    [
        ({"source": "fuchsia", "stop_rule": {}}, "qt", "registered foreign source is chromium"),
        ({"source": "chromium", "stop_rule": {"covers_every_draw": False}}, "qt", "no covering"),
        ({"source": "chromium", "stop_rule": _stop()}, "wikimedia", "against qt, not wikimedia"),
        ({"source": "chromium", "stop_rule": _stop()}, "qt", "not the registered one"),
    ],
)
def test_only_the_registered_sources_covering_stop_rule_is_cleared(
    coverage: dict, org: str, message: str
) -> None:
    with pytest.raises(SystemExit, match=message):
        clear.stop_rule(coverage, org)


def test_the_registered_stop_rule_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(decomposition, "FOREIGN_POOL_SHA256", pool_digest(KEPT))
    assert clear.stop_rule({"source": "chromium", "stop_rule": _stop()}, "qt") == _stop()


def test_the_exploratory_source_is_cleared_only_as_exploratory_at_its_own_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fuchsia = {"source": "fuchsia", "stop_rule": _stop()}
    with pytest.raises(SystemExit, match="not the registered one"):
        clear.stop_rule(fuchsia, "qt", exploratory=True)
    monkeypatch.setattr(decomposition, "EXPLORATORY_POOL_SHA256", pool_digest(KEPT))
    assert clear.stop_rule(fuchsia, "qt", exploratory=True) == _stop()
    with pytest.raises(SystemExit, match="registered foreign source is chromium"):
        clear.stop_rule(fuchsia, "qt")
    with pytest.raises(SystemExit, match="exploratory foreign source is fuchsia, not chromium"):
        clear.stop_rule({"source": "chromium", "stop_rule": _stop()}, "qt", exploratory=True)


def test_the_arms_read_the_cleared_file_inside_the_span(tmp_path: Path) -> None:
    path = tmp_path / "chromium.jsonl"
    write_derived_file(path, KEPT, sources=[(tmp_path / "no-root", "chromium")])
    span = decomposition.FOREIGN_SPANS["chromium"]
    assert cleared_pool(path, "chromium", span) == KEPT
    with pytest.raises(ValueError, match="outside its span"):
        cleared_pool(path, "chromium", ("2025-02-01", "2025-03-01"))


def _source_rows(i: int, project: str, path: str, before: str, created: str) -> dict:
    row = _row(i, before, before + " + 1", project=project)
    return {**row, "path": path, "created": created}


def test_the_written_pool_is_the_one_the_coverage_stop_rule_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both scripts on one fixture: the coverage's stop-rule digest is the cleared file's."""
    import json
    import sys

    from sphragis.corpus.load import derived_file_rows
    from sphragis.experiment import holdout

    copied = "int qt_only(const QString& name) { return name.size(); }"
    rows = {
        "chromium": [
            _source_rows(1, "chromium/src", "a.cc", copied, "2025-01-15"),  # Qt's copy
            _source_rows(2, "chromium/src", "b.cc", "int a = 1;", "2025-02-15"),
            _source_rows(3, "chromium/src", "c.py", "x = 1", "2025-03-15"),
            _source_rows(5, "v8/v8", "e.cc", "int c = 3;", "2025-01-15"),  # past the prefix
        ],
        "qt": [_source_rows(9, "qt/qtbase", "q.cpp", copied, "2025-01-15")],
    }

    def refined(root: Path, org: str) -> list[dict]:
        return [dict(row) for row in rows[org]]

    cover = sys.modules["foreign_pool_coverage"]
    for module in (cover, clear, holdout):
        monkeypatch.setattr(module, "refined_examples", refined)
    monkeypatch.setattr(holdout, "run_dedup", lambda pool: (list(pool), {}))
    need = {
        "need": {"in_language": {"examples": 1}, "other": {"examples": 1}},
        "draws": {"4": {"qt-a": {"in_language": 1, "other": 1}}},
    }
    order = {"order": [{"project": "chromium/src"}, {"project": "v8/v8"}]}
    (tmp_path / "need.json").write_text(json.dumps(need))
    (tmp_path / "order.json").write_text(json.dumps(order))
    coverage = tmp_path / "coverage.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["c", "--source", "chromium", "--root", str(tmp_path / "pool")]
        + ["--need", str(tmp_path / "need.json"), "--order", str(tmp_path / "order.json")]
        + ["--against", f"qt={tmp_path / 'qt'}", "--out", str(coverage)],
    )
    cover.main()
    stop = json.loads(coverage.read_text())["stop_rule"]
    assert stop["projects"] == 1 and stop["covers_every_draw"]
    monkeypatch.setattr(decomposition, "FOREIGN_POOL_SHA256", stop["digest"])
    out = tmp_path / "cleared" / "chromium.jsonl"
    out.parent.mkdir()
    monkeypatch.setattr(
        sys,
        "argv",
        ["c", "--coverage", str(coverage), "--root", str(tmp_path / "pool")]
        + ["--against", f"qt={tmp_path / 'qt'}", "--pool", str(out)]
        + ["--out", str(tmp_path / "record.json")],
    )
    clear.main()
    written = derived_file_rows(out)
    assert pool_digest(written) == stop["digest"]
    assert sorted(r["id"] for r in written) == ["r2", "r3"]
    record = json.loads((tmp_path / "record.json").read_text())
    assert record["cleared_pool"]["examples"] == stop["pool"]["examples"] == 2
