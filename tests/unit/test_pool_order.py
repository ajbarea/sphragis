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


def _month(month: str) -> str:
    return f"status:merged after:{month}-01 before:{cli.next_month(month)}-01"


def _record(projects=POOL.projects, month: str = "2024-10") -> dict:
    return {"listings": [{"query": f"{_month(month)} (project:{p})"} for p in projects]}


def _whole(month: str) -> dict:
    """A month listed whole, as a fetch under a scope of every project records it."""
    return {"query": _month(month)}


def _change(number: int, project: str, created: str, comments: int) -> dict:
    return {
        "_number": number,
        "project": project,
        "branch": "main",
        "created": f"{created} 00:00:00.000000000",
        "total_comment_count": comments,
    }


def _write(root: Path, rows: dict[str, list[dict]], record=None) -> None:
    """Each month's snapshot; `record` maps a month to its record (default every project's)."""
    for month in MONTHS:
        made = record(month) if record else _record(month=month)
        write_snapshot(root, "chromium", month, rows.get(month, []), record=made)


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
    _write(tmp_path, {}, record=lambda m: _record(("p/a", "p/b"), m))
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
    _write(
        tmp_path,
        {
            "2024-10": [_change(1, "x/b", "2024-10-02", 1), _change(2, "x/a", "2024-10-03", 0)],
            "2024-11": [_change(3, "x/b", "2024-11-02", 2)],
        },
        record=_whole,
    )
    report = _main(tmp_path, monkeypatch)
    assert [row["project"] for row in report["order"]] == ["x/b", "x/a"]
    assert report["order"][0] == {"project": "x/b", "merged": 2, "commented": 2}


def test_a_scope_of_every_project_refuses_a_month_listed_by_project(tmp_path, monkeypatch) -> None:
    """The ranking listings name no project; a pool's own listings live in another root."""
    monkeypatch.setitem(cli.REST_PERMITTED, HOST, cli.REST_PERMITTED[HOST]._replace(scope=EVERY))
    _write(tmp_path, {}, record=lambda m: _record(("x/a",), m))
    with pytest.raises(SystemExit, match="name no project"):
        _main(tmp_path, monkeypatch)


def test_a_scope_of_every_project_refuses_the_hosts_own_project(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(cli.REST_PERMITTED, HOST, cli.REST_PERMITTED[HOST]._replace(scope=EVERY))
    _write(tmp_path, {"2024-10": [_change(1, "All-Projects", "2024-10-02", 0)]}, record=_whole)
    with pytest.raises(SystemExit, match="outside the grant"):
        _main(tmp_path, monkeypatch)


@pytest.mark.parametrize("branch", ["refs/meta/config", "refs/meta/dashboards/team", "", None])
def test_a_scope_of_every_project_refuses_a_change_off_a_code_branch(
    tmp_path, monkeypatch, branch: str | None
) -> None:
    monkeypatch.setitem(cli.REST_PERMITTED, HOST, cli.REST_PERMITTED[HOST]._replace(scope=EVERY))
    config = {**_change(1, "x/a", "2024-10-02", 0), "branch": branch}
    _write(tmp_path, {"2024-10": [config]}, record=_whole)
    with pytest.raises(SystemExit, match="outside the grant"):
        _main(tmp_path, monkeypatch)


@pytest.mark.parametrize(
    "query",
    [
        "status:merged after:2024-10-15 before:2024-11-01",
        "status:merged after:2024-09-01 before:2024-10-01",
        "status:merged after:2024-10-01 before:2024-10-99",
        "status:merged",
    ],
)
def test_a_scope_of_every_project_refuses_a_listing_that_is_not_its_whole_month(
    tmp_path, monkeypatch, query: str
) -> None:
    monkeypatch.setitem(cli.REST_PERMITTED, HOST, cli.REST_PERMITTED[HOST]._replace(scope=EVERY))
    _write(tmp_path, {}, record=lambda m: {"query": query} if m == "2024-10" else _whole(m))
    with pytest.raises(SystemExit, match="not its month"):
        _main(tmp_path, monkeypatch)


def test_a_project_listed_for_another_month_is_refused(tmp_path, monkeypatch) -> None:
    _write(tmp_path, {}, record=lambda m: _record(month="2024-11" if m == "2024-10" else m))
    with pytest.raises(SystemExit, match="not its month"):
        _main(tmp_path, monkeypatch)


RANGED = EVERY._replace(listing_days=2)


def _ranged(month: str, ranges=None, projects=(None,), truncated=()) -> dict:
    """A month listed in `ranges` (default the scope's two-day ranges), once per project, with
    the `truncated` ranges recorded as attempts the host would have cut short."""
    spans = cli.day_ranges(month, 2) if ranges is None else ranges

    def query(a: str, b: str, p: str | None) -> str:
        return f"status:merged after:{a} before:{b}" + (f" (project:{p})" if p else "")

    return {
        "listings": [{"query": query(a, b, p)} for a, b in spans for p in projects],
        "truncated": [
            {"query": query(a, b, p), "pages": 1} for a, b in truncated for p in projects
        ],
    }


def _ranged_scope(monkeypatch: pytest.MonkeyPatch, scope=RANGED) -> None:
    monkeypatch.setitem(cli.REST_PERMITTED, HOST, cli.REST_PERMITTED[HOST]._replace(scope=scope))


def test_a_month_listed_in_its_ranges_is_ranked(tmp_path, monkeypatch) -> None:
    _ranged_scope(monkeypatch)
    _write(tmp_path, {"2024-11": [_change(1, "x/a", "2024-11-02", 1)]}, record=_ranged)
    report = _main(tmp_path, monkeypatch)
    assert report["order"] == [{"project": "x/a", "merged": 1, "commented": 1}]


def test_a_range_listed_again_as_its_halves_is_accepted(tmp_path, monkeypatch) -> None:
    """The halves a fetch lists when the host would truncate a range (research log, 2026-10-19)."""
    _ranged_scope(monkeypatch, RANGED._replace(listing_days=4))
    whole = cli.day_ranges("2024-10", 4)
    # 2024-10-01 to 05 as its halves, the first of them halved again.
    split = [
        ("2024-10-01", "2024-10-02"),
        ("2024-10-02", "2024-10-03"),
        ("2024-10-03", "2024-10-05"),
        *whole[1:],
    ]
    cut = [("2024-10-01", "2024-10-05"), ("2024-10-01", "2024-10-03")]

    def record(month: str) -> dict:
        if month == "2024-10":
            return _ranged(month, split, truncated=cut)
        return _ranged(month, cli.day_ranges(month, 4))

    _write(tmp_path, {}, record=record)
    assert _main(tmp_path, monkeypatch)["changes"] == 0


@pytest.mark.parametrize(
    "cut",
    [
        # halves the record does not say the host would have cut short
        [],
        [("2024-10-01", "2024-10-05")],
        # a truncated attempt with no halves listed in its place
        [("2024-10-01", "2024-10-05"), ("2024-10-01", "2024-10-03"), ("2024-10-05", "2024-10-09")],
        # a truncated attempt recorded twice
        [("2024-10-01", "2024-10-05"), ("2024-10-01", "2024-10-03"), ("2024-10-01", "2024-10-03")],
    ],
)
def test_halves_stand_only_where_the_record_names_their_range_truncated(
    tmp_path, monkeypatch, cut
) -> None:
    _ranged_scope(monkeypatch, RANGED._replace(listing_days=4))
    whole = cli.day_ranges("2024-10", 4)
    split = [
        ("2024-10-01", "2024-10-02"),
        ("2024-10-02", "2024-10-03"),
        ("2024-10-03", "2024-10-05"),
        *whole[1:],
    ]

    def record(month: str) -> dict:
        if month == "2024-10":
            return _ranged(month, split, truncated=cut)
        return _ranged(month, cli.day_ranges(month, 4))

    _write(tmp_path, {}, record=record)
    with pytest.raises(SystemExit, match="not its month"):
        _main(tmp_path, monkeypatch)


def _one_month_refused(tmp_path, monkeypatch, made: dict, scope=RANGED) -> None:
    _ranged_scope(monkeypatch, scope)
    _write(tmp_path, {}, record=lambda m: made if m == "2024-10" else _ranged(m))
    with pytest.raises(SystemExit, match="not its month"):
        _main(tmp_path, monkeypatch)


RANGES = cli.day_ranges("2024-10", 2)


@pytest.mark.parametrize(
    "ranges",
    [
        # a gap
        RANGES[:3] + RANGES[4:],
        # an overlap, and a range listed twice
        [*RANGES, ("2024-10-02", "2024-10-04")],
        [*RANGES, RANGES[5]],
        # a range listed and its halves too
        [*RANGES, ("2024-10-01", "2024-10-02"), ("2024-10-02", "2024-10-03")],
        # a range from another month
        [*RANGES, ("2024-11-01", "2024-11-03")],
        # the month whole where ranges are required
        [("2024-10-01", "2024-11-01")],
    ],
)
def test_a_month_not_partitioned_into_its_ranges_is_refused(tmp_path, monkeypatch, ranges) -> None:
    _one_month_refused(tmp_path, monkeypatch, _ranged("2024-10", ranges))


def test_a_split_that_is_not_the_ranges_halves_is_refused(tmp_path, monkeypatch) -> None:
    scope = RANGED._replace(listing_days=4)
    whole = cli.day_ranges("2024-10", 4)
    split = [("2024-10-01", "2024-10-02"), ("2024-10-02", "2024-10-05"), *whole[1:]]
    _ranged_scope(monkeypatch, scope)
    cut = [("2024-10-01", "2024-10-05")]
    _write(
        tmp_path,
        {},
        record=lambda m: (
            _ranged(m, split, truncated=cut) if m == "2024-10" else _ranged(m, cli.day_ranges(m, 4))
        ),
    )
    with pytest.raises(SystemExit, match="not its month"):
        _main(tmp_path, monkeypatch)


def test_a_month_listed_whole_is_not_accepted_as_halves(tmp_path, monkeypatch) -> None:
    """A scope that lists months whole registers no halving."""
    halves = [("2024-10-01", "2024-10-16"), ("2024-10-16", "2024-11-01")]
    cut = [("2024-10-01", "2024-11-01")]
    monkeypatch.setitem(cli.REST_PERMITTED, HOST, cli.REST_PERMITTED[HOST]._replace(scope=EVERY))
    _write(
        tmp_path,
        {},
        record=lambda m: _ranged(m, halves, truncated=cut) if m == "2024-10" else _whole(m),
    )
    with pytest.raises(SystemExit, match="not its month"):
        _main(tmp_path, monkeypatch)


def test_a_ranked_root_naming_a_project_in_its_ranges_is_refused(tmp_path, monkeypatch) -> None:
    _ranged_scope(monkeypatch)
    _write(tmp_path, {}, record=lambda m: _ranged(m, projects=("x/a",)))
    with pytest.raises(SystemExit, match="name no project"):
        _main(tmp_path, monkeypatch)


def test_a_named_scope_needs_each_project_in_each_range(tmp_path, monkeypatch) -> None:
    named = POOL._replace(listing_days=2)
    _ranged_scope(monkeypatch, named)
    _write(tmp_path, {}, record=lambda m: _ranged(m, projects=named.projects))
    assert _main(tmp_path, monkeypatch)["changes"] == 0
    # p/c missing from one range of 2024-10.
    gap = _ranged("2024-10", projects=named.projects)
    gap["listings"] = [
        q
        for q in gap["listings"]
        if q["query"] != ("status:merged after:2024-10-03 before:2024-10-05 (project:p/c)")
    ]
    _one_month_refused(tmp_path / "gap", monkeypatch, gap, named)
    # p/c missing from every range.
    _ranged_scope(monkeypatch, named)
    _write(tmp_path / "missing", {}, record=lambda m: _ranged(m, projects=("p/a", "p/b")))
    with pytest.raises(SystemExit, match="lists 2 projects"):
        _main(tmp_path / "missing", monkeypatch)


def test_a_listing_recorded_without_its_query_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(cli.REST_PERMITTED, HOST, cli.REST_PERMITTED[HOST]._replace(scope=EVERY))

    def record(month: str) -> dict:
        return {"listings": [_whole(month), {"pages": 1}]}

    _write(tmp_path, {}, record=record)
    with pytest.raises(SystemExit, match="names no query"):
        _main(tmp_path, monkeypatch)
