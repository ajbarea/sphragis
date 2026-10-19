"""A `--fewer-requests` build skips changes its snapshot records no comment on and asks each diff
once; without the flag the build is as it was."""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

from sphragis.corpus import cli
from sphragis.corpus.load import raw_digest
from sphragis.corpus.storage import write_snapshot

DIFF = {"content": [{"a": ["x=1"], "b": ["x = 1"]}]}


def _change(number: int, count: int | None) -> dict[str, Any]:
    change: dict[str, Any] = {
        "_number": number,
        "change_id": f"I{number}",
        "created": "2024-10-05 00:00:00.000000000",
        "owner": {"_account_id": "owner"},
        "revisions": {"a": {}, "b": {}},
    }
    if count is not None:
        change["total_comment_count"] = count
    return change


def _holding(root: Path) -> dict:
    return {
        "org": "openstack",
        "holds": True,
        "upper_95_share_with_comments": 0.009,
        "raw_digest": raw_digest(root, "openstack"),
    }


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *flags: str) -> tuple[list, dict, list]:
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    if not (tmp_path / "openstack" / "raw" / "2024-10.ndjson.gz").exists():
        changes = [_change(1, 0), _change(2, 2), _change(3, None)]
        write_snapshot(tmp_path, "openstack", "2024-10", changes, record={})
    asked: list[tuple] = []
    comments = {
        "f.py": [
            {"patch_set": 1, "line": 1, "message": "fix", "author": {"_account_id": "rev"}},
            {"patch_set": 1, "line": 1, "message": "and here", "author": {"_account_id": "rev2"}},
        ]
    }

    def fetch_comments(n: int):
        asked.append(("comments", n))
        return comments

    def fetch_diff(n: int, r: int, p: str, b: int):
        asked.append(("diff", n, r, p, b))
        return DIFF

    monkeypatch.setattr(cli, "http_transport", lambda **_: lambda url: (200, {}, ")]}'\n{}"))
    monkeypatch.setattr(
        cli, "scrubbed_comment_fetcher", lambda base, salt, transport: fetch_comments
    )
    monkeypatch.setattr(cli, "scrubbed_diff_fetcher", lambda base, transport: fetch_diff)
    if "--fewer-requests" in flags:
        probe = tmp_path / "probe.json"
        probe.write_text(json.dumps(_holding(tmp_path)))
        flags = (*flags, "--count-probe", str(probe))
    assert cli.main(["build", "--org", "openstack", "--root", str(tmp_path), *flags]) == 0
    examples = tmp_path / "openstack" / "examples"
    rows = [json.loads(line) for line in (examples / "2024-10.jsonl").read_text().splitlines()]
    drops = json.loads((examples / "2024-10.drops.json").read_text())
    return rows, drops, asked


def test_without_the_flag_every_change_is_asked_and_the_drops_carry_no_new_key(
    tmp_path, monkeypatch
) -> None:
    rows, drops, asked = _build(tmp_path, monkeypatch)
    assert [a[1] for a in asked if a[0] == "comments"] == [1, 2, 3]
    assert len(rows) == 3 and cli.UNCOMMENTED not in drops


def test_with_the_flag_an_uncommented_change_is_counted_not_asked(tmp_path, monkeypatch) -> None:
    rows, drops, asked = _build(tmp_path, monkeypatch, "--fewer-requests")
    assert [a[1] for a in asked if a[0] == "comments"] == [2, 3]  # no count: asked
    assert drops[cli.UNCOMMENTED] == 1
    assert len(rows) == 2


def test_with_the_flag_each_diff_is_asked_once_per_change(tmp_path, monkeypatch) -> None:
    _, _, asked = _build(tmp_path, monkeypatch, "--fewer-requests")
    diffs = [a for a in asked if a[0] == "diff"]
    assert len(diffs) == 2  # changes 2 and 3, one diff each though each has two comments
    _, _, plain = _build(tmp_path / "plain", monkeypatch)
    assert len([a for a in plain if a[0] == "diff"]) == 6


def test_a_failed_diff_is_not_remembered() -> None:
    calls: list[int] = []

    def flaky(number, patch_set, path, base):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("reset")
        return DIFF

    fetch = cli.cached_diffs(flaky)
    with pytest.raises(OSError):
        fetch(42, 2, "f.py", 1)
    assert fetch(42, 2, "f.py", 1) == DIFF == fetch(42, 2, "f.py", 1)
    assert len(calls) == 2


def test_a_month_built_in_the_other_mode_is_rebuilt(tmp_path, monkeypatch, capsys) -> None:
    _build(tmp_path, monkeypatch)
    record = tmp_path / "openstack" / "examples" / "2024-10.source.json"
    assert "fewer_requests" not in json.loads(record.read_text())
    _, drops, _ = _build(tmp_path, monkeypatch, "--fewer-requests")
    assert "built in the other request mode, rebuilding" in capsys.readouterr().out
    assert json.loads(record.read_text())["fewer_requests"] is True
    assert drops[cli.UNCOMMENTED] == 1 and "author_comment" in drops


def test_the_diff_cache_keeps_changes_patch_sets_and_bases_apart() -> None:
    asked: list[tuple] = []
    fetch = cli.cached_diffs(lambda *key: asked.append(key) or {"key": key})
    for key in ((1, 2, "f.py", 1), (2, 2, "f.py", 1), (1, 3, "f.py", 2), (1, 2, "g.py", 1)):
        assert fetch(*key) == {"key": key}
    assert fetch(1, 2, "f.py", 1) == {"key": (1, 2, "f.py", 1)}
    assert len(asked) == 4


def test_a_notedb_row_is_never_skipped_as_uncommented() -> None:
    assert not cli.uncommented({"total_comment_count": 0, cli.NOTEDB_KEY: {}})
    assert cli.uncommented({"total_comment_count": 0})


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"holds": False}, "does not hold for openstack"),
        ({"org": "qt"}, "does not hold for openstack"),
        ({"upper_95_share_with_comments": 0.05}, "bounds the skipped share at 0.05"),
        ({"upper_95_share_with_comments": None}, "bounds the skipped share at None"),
        ({"raw_digest": "other"}, "asked other raw snapshots"),
    ],
)
def test_fewer_requests_waits_on_a_tight_probe_of_these_snapshots(
    tmp_path, monkeypatch, change, message
) -> None:
    write_snapshot(tmp_path, "openstack", "2024-10", [_change(1, 0)], record={})
    path = tmp_path / "probe.json"
    path.write_text(json.dumps(_holding(tmp_path) | change))
    with pytest.raises(SystemExit, match=message):
        cli.main(
            [
                "build",
                "--org",
                "openstack",
                "--root",
                str(tmp_path),
                "--fewer-requests",
                "--count-probe",
                str(path),
            ]
        )


@pytest.mark.parametrize(
    ("flags", "content", "message"),
    [
        (["--fewer-requests"], None, "needs --count-probe"),
        (["--fewer-requests"], "{not json", "probe.json"),
        (["--fewer-requests"], "[]", "does not hold"),
        ([], "{}", "read only with --fewer-requests"),
    ],
)
def test_the_probe_gate_refuses_cleanly(tmp_path, flags, content, message) -> None:
    if content is not None:
        (tmp_path / "probe.json").write_text(content)
        flags = [*flags, "--count-probe", str(tmp_path / "probe.json")]
    with pytest.raises(SystemExit, match=message):
        cli.main(["build", "--org", "openstack", "--root", str(tmp_path), *flags])


def test_a_read_cap_reads_the_lowest_commented_numbers_and_counts_the_rest(
    tmp_path, monkeypatch
) -> None:
    rows, drops, asked = _build(tmp_path, monkeypatch, "--fewer-requests", "--read-cap", "1")
    chosen = cli.capped_numbers([_change(2, 2), _change(3, None)], 1, skip_uncommented=True)
    assert chosen is not None
    assert [a[1] for a in asked if a[0] == "comments"] == sorted(chosen)
    assert drops[cli.UNCOMMENTED] == 1 and drops[cli.OVER_READ_CAP] == 1
    assert len(rows) == 1
    record = tmp_path / "openstack" / "examples" / "2024-10.source.json"
    assert json.loads(record.read_text())["read_cap"] == 1


def test_the_cap_draws_in_a_seeded_order_not_by_snapshot_order_or_age() -> None:
    changes = [_change(n, 1) for n in range(100, 400)] + [_change(5, 0)]
    picked = cli.capped_numbers(changes, 30, skip_uncommented=True)
    assert picked is not None
    assert picked == cli.capped_numbers(changes[::-1], 30, skip_uncommented=True)
    assert len(picked) == 30 and 5 not in picked
    # Not the lowest numbers, which in a month's listing are the changes longest open.
    assert picked != set(range(100, 130))
    assert cli.capped_numbers(changes, None, skip_uncommented=True) is None
    # Without --fewer-requests the uncommented change is a candidate too.
    every = cli.capped_numbers(changes, 301, skip_uncommented=False)
    assert every is not None and 5 in every


def test_a_duplicated_number_takes_one_slot() -> None:
    changes = [_change(1, 1), _change(1, 1), _change(2, 1)]
    assert cli.capped_numbers(changes, 2, skip_uncommented=True) == {1, 2}


def test_a_month_built_under_another_cap_is_rebuilt(tmp_path, monkeypatch, capsys) -> None:
    _build(tmp_path, monkeypatch, "--fewer-requests", "--read-cap", "1")
    _, drops, asked = _build(tmp_path, monkeypatch, "--fewer-requests", "--read-cap", "2")
    assert "built under another read cap, rebuilding" in capsys.readouterr().out
    assert drops[cli.OVER_READ_CAP] == 0
    _, drops, _ = _build(tmp_path, monkeypatch, "--fewer-requests")
    assert cli.OVER_READ_CAP not in drops


def test_a_read_cap_is_a_positive_count(tmp_path) -> None:
    args = cli.build_parser().parse_args(["build", "--root", str(tmp_path), "--read-cap", "0"])
    with pytest.raises(SystemExit, match="--read-cap is a positive count"):
        cli.require_count_probe(args)


CAPPED = cli.GrantScope(("v8/v8",), "2024-11-01", "2025-09-01", 170)


@pytest.mark.parametrize("flags", [(), ("--read-cap", "171")])
def test_a_scoped_host_is_read_only_under_its_grants_cap(tmp_path, monkeypatch, flags) -> None:
    """A scope that sets a read cap, as V8's did (research log, 2026-10-16)."""
    host = "chromium-review.googlesource.com"
    monkeypatch.setitem(cli.REST_PERMITTED, host, cli.REST_PERMITTED[host]._replace(scope=CAPPED))
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    row = {**_change(1, 1), "project": "v8/v8", "created": "2024-11-05 00:00:00.000000000"}
    write_snapshot(tmp_path, "chromium", "2024-11", [row], record={})
    monkeypatch.setattr(cli, "http_transport", lambda **_: pytest.fail("no request"))
    with pytest.raises(SystemExit, match="reads at most 170 changes a month"):
        cli.main(["build", "--org", "chromium", "--root", str(tmp_path), *flags])


def test_chromiums_grant_builds_without_a_read_cap(tmp_path) -> None:
    args = cli.build_parser().parse_args(["build", "--org", "chromium", "--root", str(tmp_path)])
    cli.require_grant_cap(args)


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (("--project", "All-Projects", "--month", "2024-11", "--cutoff", "2024-11-01"), "name"),
        (("--project", "{x}OR{y}", "--month", "2024-11", "--cutoff", "2024-11-01"), "name"),
        (("--project", "v8/v8", "--project", "OR", "--month", "2024-11"), "may not name"),
        (("--month", "2024-10", "--cutoff", "2024-11-01"), "not 2024-10"),
        (("--project", "v8/v8", "--month", "2024-10", "--cutoff", "2024-11-01"), "not 2024-10"),
        (("--month", "2024-11"), "--cutoff 2024-11-01"),
    ],
)
def test_a_fetch_past_chromiums_grant_is_refused_before_any_request(
    tmp_path, monkeypatch, flags, message
) -> None:
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    monkeypatch.setattr(cli, "http_transport", lambda **_: pytest.fail("no request"))
    with pytest.raises(SystemExit, match=message):
        cli.main(["fetch", "--org", "chromium", "--root", str(tmp_path), *flags])


@pytest.mark.parametrize(
    ("query", "refused"),
    [
        ("status:merged after:2024-11-01 before:2024-12-01 (project:v8/v8)", False),
        ("status:merged after:2024-11-01 before:2024-11-03", False),
        ("status:merged after:2024-11-29 before:2024-12-01 (project:chromium/src)", False),
        ("status:merged after:2024-11-01 before:2024-11-03 (project:All-Projects)", True),
        (
            "(status:merged after:2024-11-01 before:2024-12-01 (project:v8/v8)) "
            'before:"2024-11-30 08:00:00 +0000"',
            False,
        ),
        ('((status:merged (project:v8/v8))) before:"2024-11-30 +0000"', True),
        ("status:merged project:chromium/src", True),
        ("message:Assisted-by after:2024-10-01", True),
        ("project:v8/v8 OR is:open", True),
        ("project:v8/v8 -is:wip", True),
        ("parentproject:v8/v8", True),
    ],
)
def test_a_scoped_host_answers_only_listings_of_its_granted_projects(query, refused) -> None:
    from urllib.parse import quote

    scope = cli.REST_PERMITTED["chromium-review.googlesource.com"].scope
    assert scope is not None
    why = cli.out_of_scope_listing(f"/changes/?q={quote(query)}&n=100", scope)
    assert (why is not None) is refused
    assert cli.out_of_scope_listing("/changes/123/comments", scope) is None
    twice = "/changes/?q=project%3Av8%2Fv8&q=status%3Amerged"
    assert (
        cli.out_of_scope_listing(twice, scope)
        == "a listing with parameters the client does not send"
    )


def test_a_month_outside_the_grant_is_not_built(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    write_snapshot(tmp_path, "chromium", "2024-10", [_change(1, 1)], record={})
    monkeypatch.setattr(cli, "http_transport", lambda **_: pytest.fail("no request"))
    cli.main(["build", "--org", "chromium", "--root", str(tmp_path)])
    assert "2024-10: outside the host's grant; not built" in capsys.readouterr().out
    assert not (tmp_path / "chromium" / "examples" / "2024-10.jsonl").exists()


POOL = cli.GrantScope(("platform/a", "platform/b"), "2024-10-01", "2025-03-27", None)


def _pool_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """chromium's host re-scoped as a two-project, uncapped pool ending mid-month."""
    host = "chromium-review.googlesource.com"
    monkeypatch.setitem(cli.REST_PERMITTED, host, cli.REST_PERMITTED[host]._replace(scope=POOL))


@pytest.mark.parametrize(
    ("month", "inside"),
    [
        ("2024-09", False),
        ("2024-10", True),
        ("2025-02", True),
        ("2025-03", True),
        ("2025-04", False),
    ],
)
def test_a_grant_ending_mid_month_covers_that_month(month: str, inside: bool) -> None:
    assert cli.grant_months(POOL, month) is inside
    chromium = cli.REST_PERMITTED["chromium-review.googlesource.com"].scope
    assert chromium is not None
    assert cli.grant_months(chromium, "2025-10") and not cli.grant_months(chromium, "2024-10")


def test_a_scoped_fetch_lists_each_project_alone_and_keeps_only_the_granted_dates(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    _pool_host(monkeypatch)
    asked: list[str] = []

    def listing(base, query, *, transport, options):
        asked.append(query)
        project = query.rsplit("project:", 1)[1].rstrip(")")
        created = ["2025-03-26 23:00:00.000000000", "2025-03-27 00:00:00.000000000"]
        rows = [
            {**_change(n, 1), "_number": n, "project": project, "created": c}
            for n, c in zip((len(asked) * 10, len(asked) * 10 + 1), created, strict=True)
        ]
        return rows, {"query": query, "pages": 1, "count": len(rows)}

    monkeypatch.setattr(cli, "fetch_changes", listing)
    monkeypatch.setattr(cli, "http_transport", lambda **_: object())
    flags = ["--project", "platform/a", "--project", "platform/b", "--month", "2025-03"]
    assert (
        cli.main(
            [
                "fetch",
                "--org",
                "chromium",
                "--root",
                str(tmp_path),
                *flags,
                "--cutoff",
                "2024-10-01",
            ]
        )
        == 0
    )
    assert [q.rsplit(" ", 1)[1] for q in asked] == ["(project:platform/a)", "(project:platform/b)"]
    assert all(cli.out_of_scope_listing(f"/changes/?q={q}", POOL) is None for q in asked)
    from sphragis.corpus.storage import iter_snapshot

    kept = list(iter_snapshot(tmp_path / "chromium" / "raw" / "2025-03.ndjson.gz"))
    assert sorted(c["_number"] for c in kept) == [10, 20]


def test_a_scoped_fetch_refuses_listings_that_repeat_a_change(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    _pool_host(monkeypatch)
    monkeypatch.setattr(
        cli, "fetch_changes", lambda *a, **k: ([_change(7, 1)], {"pages": 1, "count": 1})
    )
    monkeypatch.setattr(cli, "http_transport", lambda **_: object())
    flags = ["--project", "platform/a", "--project", "platform/b", "--month", "2024-10"]
    with pytest.raises(SystemExit, match="repeat change 7 away from a range boundary"):
        cli.main(["fetch", "--org", "chromium", "--root", str(tmp_path), *flags])


def test_an_uncapped_scope_builds_without_a_read_cap(tmp_path, monkeypatch) -> None:
    _pool_host(monkeypatch)
    args = cli.build_parser().parse_args(["build", "--org", "chromium", "--root", str(tmp_path)])
    cli.require_grant_cap(args)


def test_a_held_row_of_a_project_outside_the_grant_is_not_built(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    inside = {"branch": "main", "created": "2024-11-05 00:00:00.000000000"}
    rows = [{**_change(1, 1), **inside, "project": "v8/v8"}]
    rows.append({**_change(2, 1), **inside, "project": "All-Users"})
    write_snapshot(tmp_path, "chromium", "2024-11", rows, record={})
    monkeypatch.setattr(cli, "http_transport", lambda **_: pytest.fail("no request"))
    cli.main(["build", "--org", "chromium", "--root", str(tmp_path)])
    assert "2024-11: rows of projects outside the host's grant" in capsys.readouterr().out
    assert not (tmp_path / "chromium" / "examples" / "2024-11.jsonl").exists()


@pytest.mark.parametrize(
    ("month", "row", "why"),
    [
        (
            "2024-10",
            {"project": "platform/a", "created": "2024-09-20 08:00:00.000000000"},
            "1 rows",
        ),
        (
            "2025-03",
            {"project": "platform/b", "created": "2025-03-29 08:00:00.000000000"},
            "1 rows",
        ),
        (
            "2025-03",
            {"project": "platform/b", "created": "2025-03-27 00:00:00.000000000"},
            "1 rows",
        ),
        ("2024-11", {"project": "platform/a"}, "1 rows created outside"),
        ("2024-11", {"project": "platform/c", "created": "2024-11-05 00:00:00.000"}, "projects"),
        ("2025-04", {"project": "platform/a", "created": "2025-03-20 00:00:00.000"}, "outside"),
    ],
)
def test_a_held_row_created_outside_the_grant_is_not_built(
    tmp_path, monkeypatch, capsys, month, row, why
) -> None:
    """A snapshot held from before the grant can hold changes created outside its window, which
    the build would ask for by number."""
    _pool_host(monkeypatch)
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    change = {k: v for k, v in _change(1, 1).items() if k != "created"} | row
    write_snapshot(tmp_path, "chromium", month, [change], record={})
    monkeypatch.setattr(cli, "http_transport", lambda **_: pytest.fail("no request"))
    cli.main(["build", "--org", "chromium", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert f"{month}: " in out and why in out and "not built" in out
    assert not (tmp_path / "chromium" / "examples" / f"{month}.jsonl").exists()


@pytest.mark.parametrize(
    ("month", "inside"),
    [
        ("2024-09", False),
        ("2024-10", True),
        ("2025-04", True),
        ("2025-10", True),
        ("2025-11", False),
    ],
)
def test_a_scope_listed_past_its_window_reads_the_later_months(month: str, inside: bool) -> None:
    """A change created inside the grant and updated later is listed in a later month."""
    later = POOL._replace(listed_through="2025-10")
    assert cli.grant_months(later, month) is inside
    assert cli.grant_months(POOL, "2025-04") is False
    row = {"project": "platform/a", "created": "2025-02-10 00:00:00.000000000"}
    assert cli.outside_grant(later, "2025-06", [row]) == ""
    after = {**row, "created": "2025-04-02 00:00:00.000000000"}
    assert "created outside" in cli.outside_grant(later, "2025-06", [after])


def test_aosps_listings_run_through_the_month_before_the_sealed_window() -> None:
    from sphragis.corpus.windows import WINDOWS

    scope = cli.REST_PERMITTED["android-review.googlesource.com"].scope
    assert scope is not None and scope.listed_through == cli.last_open_month()
    assert f"{cli.last_open_month()}-31" < WINDOWS["test"][0]
    chromium = cli.REST_PERMITTED["chromium-review.googlesource.com"].scope
    assert chromium is not None and chromium.listed_through == cli.last_open_month()


def test_the_grants_window_is_its_created_dates_exactly() -> None:
    assert cli.outside_grant(None, "2024-10", [{"project": "x"}]) == ""
    assert cli.outside_grant(POOL, "2025-04", []) == ""
    inside = {"project": "platform/a", "created": "2025-03-26 23:59:59.999999999"}
    assert cli.outside_grant(POOL, "2025-03", [inside]) == ""
    assert cli.created_in_grant(POOL, "2024-10-01 00:00:00.000000000")
    assert not cli.created_in_grant(POOL, "2024-09-30 23:59:59.999999999")
    assert not cli.created_in_grant(POOL, None)


@pytest.mark.parametrize("month", ["2024-9", "2024-13", "24-10", "2024-10-01"])
def test_a_month_is_read_as_yyyy_mm_only(month: str) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["fetch", "--month", month])


@pytest.mark.parametrize("cutoff", ["2024-10-1", "2024-1-01", "2024-10", "2024-10-01 00:00"])
def test_a_cutoff_is_read_as_yyyy_mm_dd_only(cutoff: str) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["fetch", "--cutoff", cutoff])


def test_aosps_grant_lists_only_the_census_projects_and_none_before_it(tmp_path) -> None:
    permission = cli.REST_PERMITTED["android-review.googlesource.com"]
    assert permission.written_grant and permission.single_client and permission.crawl_delay >= 1.0
    scope = permission.scope
    assert scope is not None and scope.read_cap is None
    assert (scope.created_from, scope.created_before) == ("2024-10-01", "2025-03-27")
    assert scope.projects == cli.census_projects()
    assert cli.census_projects(tmp_path / "absent.json") == ()
    for unreadable in ("{", "{}", '{"cpp_majority": "platform/a"}', "[]"):
        (tmp_path / "bad.json").write_text(unreadable)
        assert cli.census_projects(tmp_path / "bad.json") == (), unreadable
    census = tmp_path / "census.json"
    census.write_text(json.dumps({"cpp_majority": ["platform/a", "platform/b"]}))
    assert cli.census_projects(census) == ("platform/a", "platform/b")
    empty = scope._replace(projects=())
    assert cli.out_of_scope_listing("/changes/?q=project%3Aplatform%2Fa", empty) is not None


def test_fuchsias_grant_covers_every_project_over_qts_training_months() -> None:
    from sphragis.corpus.windows import WINDOWS

    permission = cli.REST_PERMITTED["fuchsia-review.googlesource.com"]
    assert permission.written_grant and permission.single_client and permission.crawl_delay >= 1.0
    assert permission.error_pause >= 300.0 and permission.disallow == ()
    scope = permission.scope
    assert scope is not None and scope.read_cap is None
    assert (scope.created_from, scope.created_before) == WINDOWS["train"]
    assert scope.listed_through == cli.last_open_month()
    assert scope.every_project and scope.projects == () and scope.listing_days is None
    assert cli.host_of("fuchsia") == "fuchsia-review.googlesource.com"
    assert cli.covers_project(scope, "fuchsia") and cli.covers_project(scope, "third_party/libc++")
    for not_code in ("All-Projects", "All-Users", "", None):
        assert not cli.covers_project(scope, not_code)
    assert cli.covers_change(scope, {"project": "fuchsia", "branch": "main"})
    for branch in ("refs/meta/config", "refs/meta/dashboards/team", "", None):
        assert not cli.covers_change(scope, {"project": "fuchsia", "branch": branch})
    assert not cli.covers_change(scope, {"project": "fuchsia"})
    whole = _listing("status:merged after:2025-01-01 before:2025-02-01", "&n=500&S=0")
    assert cli.out_of_scope_request(whole, scope, set()) is None
    assert cli.out_of_scope_request(_listing(f"{MONTH} (project:All-Projects)"), scope, set())
    assert cli.out_of_scope_request("/changes/?q=project%3Afuchsia", scope, set())
    assert cli.out_of_scope_request("/projects/?type=CODE", scope, set())


def test_chromiums_grant_covers_every_project_in_two_day_ranges() -> None:
    """Chrome Infra's answer read as a rate, as for AOSP and Fuchsia (research log, 2026-10-19)."""
    from sphragis.corpus.windows import WINDOWS

    permission = cli.REST_PERMITTED["chromium-review.googlesource.com"]
    assert permission.written_grant and permission.single_client and permission.crawl_delay >= 1.0
    assert permission.error_pause >= 300.0 and permission.disallow == ()
    assert "rate" in permission.reason and "2,000" not in permission.reason
    assert "2026-10-19" in permission.reason
    scope = permission.scope
    assert scope == cli.GrantScope(
        (),
        WINDOWS["train"][0],
        WINDOWS["train"][1],
        None,
        cli.last_open_month(),
        every_project=True,
        listing_days=2,
    )
    for q in (
        "status:merged after:2024-11-01 before:2024-11-03",
        "status:merged after:2025-10-31 before:2025-11-01",
        "status:merged after:2025-01-05 before:2025-01-07 (project:chromium/src)",
    ):
        assert (
            cli.out_of_scope_request(_listing(q, "&n=100&S=0&o=ALL_REVISIONS"), scope, set())
            is None
        )
    for q in (
        "status:merged after:2024-10-30 before:2024-11-01",
        "status:merged after:2025-11-01 before:2025-11-03",
    ):
        assert cli.out_of_scope_request(_listing(q), scope, set()), q


@pytest.mark.parametrize(
    ("month", "days", "ranges"),
    [
        ("2025-01", None, [("2025-01-01", "2025-02-01")]),
        ("2024-12", None, [("2024-12-01", "2025-01-01")]),
        (
            "2025-02",
            7,
            [
                ("2025-02-01", "2025-02-08"),
                ("2025-02-08", "2025-02-15"),
                ("2025-02-15", "2025-02-22"),
                ("2025-02-22", "2025-03-01"),
            ],
        ),
        (
            "2024-02",
            10,
            [
                ("2024-02-01", "2024-02-11"),
                ("2024-02-11", "2024-02-21"),
                ("2024-02-21", "2024-03-01"),
            ],
        ),
        (
            "2024-12",
            10,
            [
                ("2024-12-01", "2024-12-11"),
                ("2024-12-11", "2024-12-21"),
                ("2024-12-21", "2024-12-31"),
                ("2024-12-31", "2025-01-01"),
            ],
        ),
        ("2025-04", 15, [("2025-04-01", "2025-04-16"), ("2025-04-16", "2025-05-01")]),
        ("2025-01", 40, [("2025-01-01", "2025-02-01")]),
        ("2025-01", 31, [("2025-01-01", "2025-02-01")]),
    ],
)
def test_day_ranges_list_a_month_in_consecutive_ranges_the_last_shorter(month, days, ranges):
    assert cli.day_ranges(month, days) == ranges


@pytest.mark.parametrize(
    ("month", "count", "last"),
    [
        ("2025-02", 14, ("2025-02-27", "2025-03-01")),
        ("2024-02", 15, ("2024-02-29", "2024-03-01")),
        ("2025-04", 15, ("2025-04-29", "2025-05-01")),
        ("2025-01", 16, ("2025-01-31", "2025-02-01")),
        ("2024-12", 16, ("2024-12-31", "2025-01-01")),
    ],
)
def test_two_day_ranges_tile_28_29_30_and_31_day_months(month, count, last) -> None:
    ranges = cli.day_ranges(month, 2)
    assert len(ranges) == count and ranges[-1] == last
    assert ranges[0][0] == f"{month}-01" and ranges[-1][1] == f"{cli.next_month(month)}-01"
    assert all(a[1] == b[0] for a, b in zip(ranges, ranges[1:], strict=False))


def test_one_day_ranges_list_each_day() -> None:
    ranges = cli.day_ranges("2024-02", 1)
    assert len(ranges) == 29 and ranges[0] == ("2024-02-01", "2024-02-02")
    assert ranges[-1] == ("2024-02-29", "2024-03-01")


@pytest.mark.parametrize("days", [0, -2])
def test_day_ranges_refuse_a_length_that_is_not_a_count_of_days(days: int) -> None:
    with pytest.raises(ValueError, match="days"):
        cli.day_ranges("2025-01", days)


@pytest.mark.parametrize(
    ("span", "halves"),
    [
        (
            ("2025-01-01", "2025-01-03"),
            (("2025-01-01", "2025-01-02"), ("2025-01-02", "2025-01-03")),
        ),
        (
            ("2025-01-01", "2025-01-04"),
            (("2025-01-01", "2025-01-02"), ("2025-01-02", "2025-01-04")),
        ),
        (
            ("2024-12-31", "2025-01-02"),
            (("2024-12-31", "2025-01-01"), ("2025-01-01", "2025-01-02")),
        ),
        (("2025-01-31", "2025-02-01"), None),
    ],
)
def test_a_range_halves_down_to_one_day(span, halves) -> None:
    assert cli.range_halves(*span) == halves


def _ranged_fetch(tmp_path, monkeypatch, *flags: str) -> tuple[list[str], dict]:
    """A Chromium month fetched under its grant, each listing answered with no change."""
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    asked: list[str] = []

    def listing(base, query, *, transport, options):
        asked.append(query)
        return [], {"query": query, "pages": 1, "count": 0}

    monkeypatch.setattr(cli, "fetch_changes", listing)
    monkeypatch.setattr(cli, "http_transport", lambda **_: object())
    fetch = ["fetch", "--org", "chromium", "--root", str(tmp_path), "--cutoff", "2024-11-01"]
    assert cli.main([*fetch, "--month", "2025-02", *flags]) == 0
    record = json.loads((tmp_path / "chromium" / "raw" / "2025-02.record.json").read_text())
    return asked, record


def test_a_scope_of_every_project_lists_its_month_in_its_ranges(tmp_path, monkeypatch) -> None:
    asked, record = _ranged_fetch(tmp_path, monkeypatch)
    ranges = cli.day_ranges("2025-02", 2)
    assert asked == [f"status:merged after:{a} before:{b}" for a, b in ranges]
    assert [r["query"] for r in record["listings"]] == asked
    scope = cli.grant_scope("chromium")
    assert scope is not None
    assert all(cli.out_of_scope_listing(_listing(q), scope) is None for q in asked)


def test_a_scope_of_every_project_lists_each_named_project_in_each_range(
    tmp_path, monkeypatch
) -> None:
    flags = ("--project", "chromium/src", "--project", "third_party/libc++")
    asked, record = _ranged_fetch(tmp_path, monkeypatch, *flags)
    ranges = cli.day_ranges("2025-02", 2)
    assert asked == [
        f"status:merged after:{a} before:{b} (project:{p})"
        for a, b in ranges
        for p in ("chromium/src", "third_party/libc++")
    ]
    assert len(record["listings"]) == 2 * len(ranges)
    scope = cli.grant_scope("chromium")
    assert scope is not None
    assert all(cli.out_of_scope_listing(_listing(q), scope) is None for q in asked)


def test_a_month_listed_whole_is_refused_not_halved_when_the_host_would_truncate_it(
    tmp_path, monkeypatch
) -> None:
    """Fuchsia's months are listed whole; its truncated month's listing is not yet registered."""
    from sphragis.corpus.gerrit import TruncatedListing

    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    asked: list[str] = []

    def truncated(base, query, *, transport, options):
        asked.append(query)
        raise TruncatedListing(f"{base} truncated {query!r}")

    monkeypatch.setattr(cli, "fetch_changes", truncated)
    monkeypatch.setattr(cli, "http_transport", lambda **_: object())
    fetch = ["fetch", "--org", "fuchsia", "--root", str(tmp_path), "--cutoff", "2024-11-01"]
    with pytest.raises(TruncatedListing):
        cli.main([*fetch, "--month", "2025-02"])
    assert asked == ["status:merged after:2025-02-01 before:2025-03-01"]
    assert not (tmp_path / "fuchsia" / "raw" / "2025-02.ndjson.gz").exists()


def test_a_ranged_fetch_halves_only_a_truncated_listing_not_another_failure(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    asked: list[str] = []

    def moved(base, query, *, transport, options):
        asked.append(query)
        raise RuntimeError(f"{base} moved {query!r} while it was paged")

    monkeypatch.setattr(cli, "fetch_changes", moved)
    monkeypatch.setattr(cli, "http_transport", lambda **_: object())
    fetch = ["fetch", "--org", "chromium", "--root", str(tmp_path), "--cutoff", "2024-11-01"]
    with pytest.raises(RuntimeError, match="moved"):
        cli.main([*fetch, "--month", "2025-02"])
    assert asked == ["status:merged after:2025-02-01 before:2025-02-03"]


def test_a_whole_month_listing_passes_only_a_scope_of_every_project() -> None:
    whole = _listing("status:merged after:2025-01-01 before:2025-02-01", "&n=500&S=0")
    probe = _listing(
        '(status:merged after:2025-01-01 before:2025-02-01) before:"2025-01-30 08:00:00 +0000"',
        "&n=3&S=1",
    )
    for target in (whole, probe):
        assert cli.out_of_scope_request(target, SCOPED, set())
        assert cli.out_of_scope_request(target, EVERY, set()) is None
    sealed = _listing("status:merged after:2025-12-01 before:2026-01-01", "&n=500&S=0")
    assert cli.out_of_scope_request(sealed, EVERY, set())


def test_a_whole_month_keeps_only_the_changes_its_grant_covers() -> None:
    created = "2025-01-02 00:00:00.000000000"
    rows = [
        {"_number": 1, "project": "fuchsia", "branch": "main", "created": created},
        {"_number": 2, "project": "All-Projects", "created": created},
        {"_number": 3, "project": "fuchsia", "branch": "refs/meta/config", "created": created},
    ]
    assert (
        cli.outside_grant(EVERY, "2025-01", rows[:2]) == "rows of projects outside the host's grant"
    )
    no_code = "rows on branches that hold no code (refs/meta/, or none named)"
    unnamed = {k: v for k, v in rows[0].items() if k != "branch"}
    for outside in (rows[2], unnamed, {**rows[0], "branch": "refs/meta/dashboards/team"}):
        assert cli.outside_grant(EVERY, "2025-01", [rows[0], outside]) == no_code
    assert cli.outside_grant(EVERY, "2025-01", rows[:1]) == ""
    # A scope that names its projects keeps its listings as they were read (research log,
    # 2026-10-19).
    config = {**rows[2], "project": "p"}
    assert cli.covers_change(SCOPED, config)
    admitted: cli.Admitted = {}
    every = cli.RestPermission("every-project placeholder", 0.0, scope=EVERY)
    original = dict(cli.REST_PERMITTED)
    cli.REST_PERMITTED["e"] = every
    try:
        cli.admit(admitted, "e", rows)
    finally:
        cli.REST_PERMITTED.clear()
        cli.REST_PERMITTED.update(original)
    assert admitted == {"e": {"1"}}


SCOPED = cli.GrantScope(("p", "a/c++"), "2024-11-01", "2025-09-01", None)
EVERY = cli.GrantScope((), "2024-11-01", "2025-09-01", None, "2025-10", every_project=True)
MONTH = "status:merged after:2025-01-01 before:2025-02-01"


def _listing(q: str, extra: str = "&n=500&S=0&o=ALL_REVISIONS") -> str:
    return f"/changes/?q={urllib.parse.quote(q)}{extra}"


@pytest.mark.parametrize(
    "target",
    [
        _listing(f"{MONTH} (project:p)"),
        _listing(f"{MONTH} project:p", "&n=500&S=0"),
        _listing(f"{MONTH} (project:a/c++)"),
        _listing(f'({MONTH} (project:p)) before:"2025-01-31 12:00:00 +0000"', "&n=3&S=1"),
        "/changes/7/comments",
        "/changes/7/revisions/abc123/files/src%2Fmain.cc/diff?base=1",
        _listing("status:merged after:2025-01-26 before:2025-02-01 project:p", "&n=500&S=0"),
        _listing(f"{MONTH} (project:p)", "&n=500&S=0&o=DETAILED_ACCOUNTS&o=CURRENT_REVISION"),
    ],
)
def test_a_scoped_host_answers_what_the_client_builds(target: str) -> None:
    assert cli.out_of_scope_request(target, SCOPED, {"7"}) is None


@pytest.mark.parametrize(
    "target",
    [
        _listing(f"{MONTH} (project:p or project:q)"),
        _listing(f"{MONTH} (project:p) or is:open"),
        _listing(f"{MONTH} (p:q)"),
        _listing(f"{MONTH} (project:^.*)"),
        _listing(f"{MONTH} (project:p) -is:wip"),
        _listing("status:open after:2025-01-01 before:2025-02-01 (project:p)"),
        _listing(f"{MONTH} (project:p)", "&n=500&q=status:open"),
        _listing(f"{MONTH} (project:p)", "&n=500&x=1"),
        "/changes/8/comments",
        "/changes/7/detail",
        "/accounts/self/detail",
        "/config/server/info",
        "/projects/p/commits/abc/files/README/content",
        "/projects/?type=ALL",
        _listing("status:merged after:2000-01-01 before:2099-01-01 (project:p)"),
        _listing("status:merged after:2025-12-01 before:2026-01-01 (project:p)"),
        _listing("status:merged after:2024-10-01 before:2024-11-01 (project:p)"),
        _listing("status:merged after:2025-02-01 before:2025-01-01 (project:p)"),
        _listing("status:merged after:2025-01-01 before:2025-03-01 (project:p)"),
        _listing("status:merged after:２０２５-01-01 before:2025-02-01 (project:p)"),
        _listing(f"{MONTH} (project:p)", "&n=500&S=0&o=MESSAGES"),
        _listing(f"{MONTH} (project:p)", "&n=500&S=-1"),
        _listing(f"{MONTH} (project:p)", "&n=&S=0"),
        "/changes/p~7/comments",
        "/changes/7/revisions/../files/a/diff?base=1",
        # Gerrit reads a brace phrase as quoted and needs no space before OR.
        _listing(f"{MONTH} project:{{p}}OR{{refs/meta/config}}", "&n=500&S=0"),
        _listing(f"{MONTH} (project:p{{x}}OR{{y}})", "&n=500&S=0"),
        _listing(f"{MONTH} (project:{{x}}OR{{y}})", "&n=500&S=0"),
        _listing(f'({MONTH} project:{{p}}OR{{y}}) before:"2025-01-31 12:00:00 +0000"', "&n=3&S=1"),
        # A Gerrit operator, in any case, or the host's own project, named as a project.
        _listing(f"{MONTH} (project:OR)", "&n=500&S=0"),
        _listing(f"{MONTH} (project:or)", "&n=500&S=0"),
        _listing(f"{MONTH} project:AND", "&n=500&S=0"),
        _listing(f"{MONTH} (project:NOT)", "&n=500&S=0"),
        _listing(f"{MONTH} (project:Not)", "&n=500&S=0"),
        _listing(f"{MONTH} (project:All-Projects)", "&n=500&S=0"),
        _listing(f"{MONTH} (project:All-Users)", "&n=500&S=0"),
        _listing(f'({MONTH} (project:OR)) before:"2025-01-31 12:00:00 +0000"', "&n=3&S=1"),
        # A space or a quote in a name.
        _listing(f"{MONTH} (project:a b)", "&n=500&S=0"),
        _listing(f'{MONTH} (project:"p")', "&n=500&S=0"),
        _listing(f"{MONTH} (project:'p')", "&n=500&S=0"),
        _listing(f"{MONTH} (project:p\\q)", "&n=500&S=0"),
        # Gerrit reads an impossible date leniently, past the month the string compares as.
        _listing("status:merged after:2025-01-01 before:2025-01-99 (project:p)", "&n=500&S=0"),
        _listing("status:merged after:2025-01-00 before:2025-02-01 (project:p)", "&n=500&S=0"),
        _listing("status:merged after:2025-13-01 before:2025-14-01 (project:p)", "&n=500&S=0"),
        _listing(f'({MONTH} (project:p)) before:"2025-01-31 25:00:00 +0000"', "&n=3&S=1"),
        _listing(f'({MONTH} (project:p)) before:"2025-01-32 12:00:00 +0000"', "&n=3&S=1"),
    ],
)
@pytest.mark.parametrize("scope", ["named", "every"])
def test_a_scoped_host_refuses_every_other_request(target: str, scope: str) -> None:
    granted = SCOPED if scope == "named" else EVERY
    assert cli.out_of_scope_request(target, granted, {"7"})
    whole = target.replace(" (project:p)", "").replace(" project:p", "")
    if scope == "every" and "project:" not in urllib.parse.unquote(whole):
        assert cli.out_of_scope_request(whole, EVERY, {"7"}), whole


def test_a_scope_that_names_its_projects_refuses_any_other() -> None:
    assert cli.out_of_scope_request(_listing(f"{MONTH} (project:q)"), SCOPED, {"7"})
    assert cli.out_of_scope_request(_listing(f"{MONTH} (project:fuchsia)"), SCOPED, {"7"})


@pytest.mark.parametrize(
    "project",
    [
        "{x}OR{y}",
        "p{x}OR{y}",
        "^.*",
        "OR",
        "or",
        "AND",
        "and",
        "NOT",
        "nOt",
        "All-Projects",
        "All-Users",
        "-p",
        "a/../b",
        "p/",
    ],
)
def test_a_scope_of_every_project_refuses_a_listing_naming_what_is_not_a_code_project(
    project: str,
) -> None:
    """A name that Gerrit's query grammar reads as syntax, or the host's own project."""
    probe = f'({MONTH} (project:{project})) before:"2025-01-31 12:00:00 +0000"'
    for listing in (f"{MONTH} (project:{project})", f"{MONTH} project:{project}"):
        assert cli.out_of_scope_request(_listing(listing, "&n=500&S=0"), EVERY, set()), listing
    assert cli.out_of_scope_request(_listing(probe, "&n=3&S=1"), EVERY, set())
    assert not cli.listable_project(EVERY, project)


@pytest.mark.parametrize("project", ["fuchsia", "chromium/src", "third_party/libc++", "v8/v8"])
def test_a_scope_of_every_project_admits_a_listing_naming_a_project_it_covers(
    project: str,
) -> None:
    probe = f'({MONTH} (project:{project})) before:"2025-01-31 12:00:00 +0000"'
    for listing in (f"{MONTH} (project:{project})", f"{MONTH} project:{project}"):
        assert cli.out_of_scope_request(_listing(listing, "&n=500&S=0"), EVERY, set()) is None
    assert cli.out_of_scope_request(_listing(probe, "&n=3&S=1"), EVERY, set()) is None
    assert cli.listable_project(EVERY, project)
    # Outside the grant's months a name does not help.
    sealed = f"status:merged after:2025-12-01 before:2026-01-01 (project:{project})"
    assert cli.out_of_scope_request(_listing(sealed), EVERY, set())


def test_no_scope_asks_a_host_for_its_project_list() -> None:
    for scope in (SCOPED, EVERY):
        assert cli.out_of_scope_request("/projects/?type=CODE", scope, set())


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("fuchsia", True),
        ("third_party/libc++", True),
        ("a.b-c/d_e", True),
        ("^.*", False),
        ("-x", False),
        ("a b", False),
        ("a(b)", False),
        ('a"b', False),
        ("a/../b", False),
        ("a//b", False),
        ("a/", False),
        ("", False),
    ],
)
def test_a_rest_project_name_reads_as_itself(name: str, ok: bool) -> None:
    assert cli.rest_project_name(name) is ok


def test_an_artifact_with_one_unsafe_name_scopes_nothing(tmp_path) -> None:
    listed = tmp_path / "census.json"
    listed.write_text(json.dumps({"cpp_majority": ["platform/a", "^.*"]}))
    assert cli.census_projects(listed) == ()
    (tmp_path / "dir.json").mkdir()
    assert cli.census_projects(tmp_path / "dir.json") == ()
    (tmp_path / "bin.json").write_bytes(b"\xff\xfe")
    assert cli.census_projects(tmp_path / "bin.json") == ()


def test_aosps_git_and_review_hosts_are_one_client(tmp_path) -> None:
    from sphragis.corpus import hosts

    git = hosts.claim_host("android.googlesource.com", tmp_path)
    assert hosts.claim_host("android-review.googlesource.com", tmp_path) is git
    assert (tmp_path / "android-review.googlesource.com.lock").exists()
    assert not (tmp_path / "android.googlesource.com.lock").exists()
    hosts.release_host("android.googlesource.com", tmp_path)
    assert (tmp_path, "android-review.googlesource.com") not in hosts._CLAIMS


def test_a_test_claims_a_host_in_a_lock_directory_of_its_own() -> None:
    """A fetch the machine has in flight holds the user's lock; no test may contend for it."""
    from sphragis.corpus import hosts

    assert not hosts.LOCK_DIR.is_relative_to(Path.home() / ".cache" / "sphragis")
    hosts.claim_host("fuchsia-review.googlesource.com")
    assert (hosts.LOCK_DIR / "fuchsia-review.googlesource.com.lock").is_file()


def test_a_change_on_a_range_boundary_is_kept_once_and_counted() -> None:
    """Gerrit's after: and before: are both inclusive, so a change last updated at a range's
    shared midnight is listed by both ranges."""
    edge = {"_number": 7, "updated": "2025-01-03 00:00:00.000000000"}
    inside = {"_number": 8, "updated": "2025-01-02 13:00:00.000000000"}
    kept, repeats = cli.drop_boundary_repeats([inside, edge, edge], {"2025-01-03"})
    assert kept == [inside, edge] and repeats == 1
    assert cli.drop_boundary_repeats([inside, edge], {"2025-01-03"}) == ([inside, edge], 0)


@pytest.mark.parametrize(
    "repeated",
    [
        {"_number": 8, "updated": "2025-01-02 13:00:00.000000000"},
        {"_number": 7, "updated": "2025-01-05 00:00:00.000000000"},
    ],
)
def test_any_other_repeat_fails_the_month(repeated: dict) -> None:
    with pytest.raises(ValueError, match="repeat"):
        cli.drop_boundary_repeats([repeated, dict(repeated)], {"2025-01-03"})


def test_a_boundary_repeat_must_be_the_same_change_twice() -> None:
    edge = {"_number": 7, "updated": "2025-01-03 00:00:00.000000000"}
    moved = {**edge, "updated": "2025-01-03 00:00:01.000000000"}
    with pytest.raises(ValueError, match="repeat"):
        cli.drop_boundary_repeats([edge, moved], {"2025-01-03"})
