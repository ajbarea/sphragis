"""A foreign pool's projects ranked by their merged changes inside the grant, from listings."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from sphragis.corpus import cli
from sphragis.corpus.storage import write_snapshot

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("pool_order", ROOT / "scripts" / "pool_order.py")
assert _spec is not None and _spec.loader is not None
order = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(order)

HOST = "chromium-review.googlesource.com"
POOL = cli.GrantScope(("p/a", "p/b", "p/c"), "2024-10-01", "2024-12-15", None)
MONTHS = ("2024-10", "2024-11", "2024-12")


@pytest.fixture(autouse=True)
def _pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(cli.REST_PERMITTED, HOST, cli.REST_PERMITTED[HOST]._replace(scope=POOL))


def _record(projects=POOL.projects) -> dict:
    return {"listings": [{"query": f"status:merged (project:{p})"} for p in projects]}


def _change(number: int, project: str, created: str, comments: int) -> dict:
    return {
        "_number": number,
        "project": project,
        "created": f"{created} 00:00:00.000000000",
        "total_comment_count": comments,
    }


def _write(root: Path, rows: dict[str, list[dict]], record=None) -> None:
    for month in MONTHS:
        write_snapshot(root, "chromium", month, rows.get(month, []), record=record or _record())


def _main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    out = tmp_path / "order.json"
    argv = ["pool_order", "--org", "chromium", "--root", str(tmp_path), "--out", str(out)]
    monkeypatch.setattr(sys, "argv", argv)
    order.main()
    return json.loads(out.read_text())


def test_projects_rank_by_merged_changes_then_name(tmp_path, monkeypatch) -> None:
    _write(
        tmp_path,
        {
            "2024-10": [_change(1, "p/b", "2024-10-02", 0), _change(2, "p/c", "2024-10-03", 4)],
            "2024-11": [_change(3, "p/b", "2024-11-02", 1), _change(2, "p/c", "2024-10-03", 4)],
            "2024-12": [_change(4, "p/c", "2024-12-14", 0)],
        },
    )
    report = _main(tmp_path, monkeypatch)
    assert report["order"] == [
        {"project": "p/b", "merged": 2, "commented": 1},
        {"project": "p/c", "merged": 2, "commented": 1},
        {"project": "p/a", "merged": 0, "commented": 0},
    ]
    assert report["changes"] == 4
    assert report["listed_twice"] == 1
    assert report["months"] == list(MONTHS)


def test_a_listing_missing_a_month_is_refused(tmp_path, monkeypatch) -> None:
    write_snapshot(tmp_path, "chromium", "2024-10", [], record=_record())
    with pytest.raises(SystemExit, match="months"):
        _main(tmp_path, monkeypatch)


def test_a_listing_missing_a_project_is_refused(tmp_path, monkeypatch) -> None:
    _write(tmp_path, {}, record=_record(("p/a", "p/b")))
    with pytest.raises(SystemExit, match="lists 2 projects"):
        _main(tmp_path, monkeypatch)


def test_a_change_created_outside_the_grant_is_refused(tmp_path, monkeypatch) -> None:
    _write(tmp_path, {"2024-12": [_change(9, "p/a", "2024-12-15", 0)]})
    with pytest.raises(SystemExit, match="outside the grant"):
        _main(tmp_path, monkeypatch)


EVERY = cli.GrantScope((), "2024-10-01", "2024-12-15", None, every_project=True)


def test_a_scope_of_every_project_ranks_the_projects_its_whole_months_return(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setitem(cli.REST_PERMITTED, HOST, cli.REST_PERMITTED[HOST]._replace(scope=EVERY))
    whole = {"query": "status:merged after:2024-10-01 before:2024-11-01"}
    _write(
        tmp_path,
        {
            "2024-10": [_change(1, "x/b", "2024-10-02", 1), _change(2, "x/a", "2024-10-03", 0)],
            "2024-11": [_change(3, "x/b", "2024-11-02", 2)],
        },
        record=whole,
    )
    report = _main(tmp_path, monkeypatch)
    assert [row["project"] for row in report["order"]] == ["x/b", "x/a"]
    assert report["order"][0] == {"project": "x/b", "merged": 2, "commented": 2}


def test_a_scope_of_every_project_refuses_a_month_listed_by_project(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(cli.REST_PERMITTED, HOST, cli.REST_PERMITTED[HOST]._replace(scope=EVERY))
    _write(tmp_path, {}, record=_record(("x/a",)))
    with pytest.raises(SystemExit, match="listed whole"):
        _main(tmp_path, monkeypatch)


def test_a_scope_of_every_project_refuses_the_hosts_own_project(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(cli.REST_PERMITTED, HOST, cli.REST_PERMITTED[HOST]._replace(scope=EVERY))
    whole = {"query": "status:merged after:2024-10-01 before:2024-11-01"}
    _write(tmp_path, {"2024-10": [_change(1, "All-Projects", "2024-10-02", 0)]}, record=whole)
    with pytest.raises(SystemExit, match="outside the grant"):
        _main(tmp_path, monkeypatch)
