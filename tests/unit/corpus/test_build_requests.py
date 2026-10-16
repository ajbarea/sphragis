"""A `--fewer-requests` build skips changes its snapshot records no comment on and asks each diff
once; without the flag the build is as it was."""

from __future__ import annotations

import json
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


@pytest.mark.parametrize("flags", [(), ("--read-cap", "171")])
def test_a_scoped_host_is_read_only_under_its_grants_cap(tmp_path, monkeypatch, flags) -> None:
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    write_snapshot(tmp_path, "chromium", "2024-11", [_change(1, 1)], record={})
    monkeypatch.setattr(cli, "http_transport", lambda **_: pytest.fail("no request"))
    with pytest.raises(SystemExit, match="reads at most 170 changes a month"):
        cli.main(["build", "--org", "chromium", "--root", str(tmp_path), *flags])


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (("--month", "2024-11", "--cutoff", "2024-11-01"), "--project v8/v8 only"),
        (("--project", "chromium/src", "--month", "2024-11", "--cutoff", "2024-11-01"), "only"),
        (("--project", "v8/v8", "--month", "2024-10", "--cutoff", "2024-11-01"), "not 2024-10"),
        (("--project", "v8/v8", "--month", "2025-09", "--cutoff", "2024-11-01"), "not 2025-09"),
        (("--project", "v8/v8", "--month", "2024-11"), "--cutoff 2024-11-01"),
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
        ('((status:merged (project:v8/v8))) before:"2024-11-30 +0000"', False),
        ("status:merged project:chromium/src", True),
        ("message:Assisted-by after:2024-10-01", True),
        ("project:v8/v8 OR is:open", True),
        ("project:v8/v8 -is:wip", True),
    ],
)
def test_a_scoped_host_answers_only_listings_of_its_granted_projects(query, refused) -> None:
    from urllib.parse import quote

    scope = cli.REST_PERMITTED["chromium-review.googlesource.com"].scope
    assert scope is not None
    why = cli.out_of_scope_listing(f"/changes/?q={quote(query)}&n=100", scope)
    assert (why is not None) is refused
    assert cli.out_of_scope_listing("/changes/123/comments", scope) is None


def test_a_month_outside_the_grant_is_not_built(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    write_snapshot(tmp_path, "chromium", "2025-09", [_change(1, 1)], record={})
    monkeypatch.setattr(cli, "http_transport", lambda **_: pytest.fail("no request"))
    cli.main(["build", "--org", "chromium", "--root", str(tmp_path), "--read-cap", "170"])
    assert "2025-09: outside the host's grant; not built" in capsys.readouterr().out
    assert not (tmp_path / "chromium" / "examples" / "2025-09.jsonl").exists()
