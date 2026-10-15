"""A `--fewer-requests` build skips changes its snapshot records no comment on and asks each diff
once; without the flag the build is as it was."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sphragis.corpus import cli
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


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *flags: str) -> tuple[list, dict, list]:
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    write_snapshot(
        tmp_path,
        "openstack",
        "2024-10",
        [_change(1, 0), _change(2, 2), _change(3, None)],
        record={},
    )
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
