"""A REST build asks the host only what it has not already answered, and builds the same."""

from __future__ import annotations

from typing import Any

import pytest

from sphragis.corpus.build import build_from_change
from sphragis.corpus.cli import rest_fetchers_for

CHANGE: dict[str, Any] = {
    "change_id": "I1",
    "_number": 42,
    "project": "openstack/nova",
    "created": "2024-10-02 11:00:00.000000000",
    "revisions": {"aaa": {"_number": 1}, "bbb": {"_number": 2}},
    "total_comment_count": 3,
}
COMMENTS = {
    "nova/f.py": [
        {"patch_set": 1, "line": 2, "message": "spaces around the operator"},
        {"patch_set": 1, "line": 2, "message": "and a test"},
        {"patch_set": 1, "line": 3, "message": "same file, same patch set"},
    ],
}
DIFF = {
    "content": [
        {"ab": ["def f(x):"]},
        {"a": ["    return x+1"], "b": ["    return x + 1"]},
        {"ab": ["", "def g():"]},
    ]
}


def _host():
    asked: list[tuple] = []

    def comments(number: int):
        asked.append(("comments", number))
        return COMMENTS

    def diffs(number: int, patch_set: int, path: str, base: int):
        asked.append(("diff", number, patch_set, path, base))
        return DIFF

    return comments, diffs, asked


def test_a_change_gerrit_counts_no_comments_on_is_not_asked_for_them() -> None:
    comments, diffs, asked = _host()
    fetch_comments, fetch_diff = rest_fetchers_for(
        CHANGE | {"total_comment_count": 0}, comments, diffs
    )
    examples, drops = build_from_change("openstack", CHANGE, fetch_comments, fetch_diff)
    assert (examples, asked) == ([], [])
    assert sum(drops.values()) == 0


def test_a_change_without_the_count_is_asked() -> None:
    comments, diffs, asked = _host()
    change = {k: v for k, v in CHANGE.items() if k != "total_comment_count"}
    fetch_comments, _ = rest_fetchers_for(change, comments, diffs)
    assert fetch_comments(42) == COMMENTS
    assert asked == [("comments", 42)]


def test_each_diff_is_asked_once_and_the_build_is_unchanged() -> None:
    comments, diffs, asked = _host()
    direct = build_from_change("openstack", CHANGE, comments, diffs)
    asked_directly = [a for a in asked if a[0] == "diff"]
    asked.clear()
    wrapped = build_from_change("openstack", CHANGE, *rest_fetchers_for(CHANGE, comments, diffs))
    assert wrapped == direct
    assert len(asked_directly) == 3
    assert [a for a in asked if a[0] == "diff"] == [("diff", 42, 2, "nova/f.py", 1)]


def test_a_failed_diff_is_not_remembered() -> None:
    comments, _, _ = _host()
    calls = []

    def flaky(number, patch_set, path, base):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("reset")
        return DIFF

    _, fetch_diff = rest_fetchers_for(CHANGE, comments, flaky)
    with pytest.raises(OSError):
        fetch_diff(42, 2, "nova/f.py", 1)
    assert fetch_diff(42, 2, "nova/f.py", 1) == DIFF
    assert fetch_diff(42, 2, "nova/f.py", 1) == DIFF
    assert len(calls) == 2
