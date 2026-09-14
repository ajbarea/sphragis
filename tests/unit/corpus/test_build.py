"""Turning a raw snapshot into refinement examples."""

from __future__ import annotations

from typing import Any

from sphragis.corpus.build import build_from_change

CHANGE: dict[str, Any] = {
    "change_id": "I1",
    "_number": 42,
    "project": "openstack/nova",
    "created": "2024-10-02 11:00:00.000000000",
    "revisions": {"aaa": {"_number": 1}, "bbb": {"_number": 2}},
}
COMMENTS = {
    "/COMMIT_MSG": [{"patch_set": 1, "line": 3, "message": "typo"}],
    "nova/f.py": [{"patch_set": 1, "line": 2, "message": "spaces around the operator"}],
}
DIFF = {
    "content": [
        {"ab": ["def f(x):"]},
        {"a": ["    return x+1"], "b": ["    return x + 1"]},
        {"ab": ["", "def g():"]},
    ]
}


def _fetchers(diff: dict[str, Any] | None = None):
    calls: list[tuple] = []

    def comments(number: int) -> dict[str, list[dict[str, Any]]]:
        return COMMENTS

    def diff_for(number: int, rev: int, path: str, base: int) -> dict[str, Any]:
        calls.append((number, rev, path, base))
        return diff if diff is not None else DIFF

    return comments, diff_for, calls


def test_a_commented_changed_hunk_becomes_one_example() -> None:
    comments, diff_for, _ = _fetchers()
    examples, drops = build_from_change("openstack", CHANGE, comments, diff_for)
    assert len(examples) == 1
    e = examples[0]
    assert e["before"] == "    return x+1"
    assert e["after"] == "    return x + 1"
    assert e["comments"] == ["spaces around the operator"]
    assert e["org"] == "openstack" and e["change_id"] == "I1"
    assert e["path"] == "nova/f.py"
    assert drops["metadata_file"] == 1


def test_the_commit_message_never_produces_an_example() -> None:
    comments, diff_for, calls = _fetchers()
    build_from_change("openstack", CHANGE, comments, diff_for)
    assert all(path != "/COMMIT_MSG" for _, _, path, _ in calls)


def test_the_diff_is_requested_between_consecutive_patch_sets() -> None:
    comments, diff_for, calls = _fetchers()
    build_from_change("openstack", CHANGE, comments, diff_for)
    assert calls == [(42, 2, "nova/f.py", 1)]


def test_a_comment_outside_every_changed_hunk_is_dropped() -> None:
    comments, diff_for, _ = _fetchers(diff={"content": [{"ab": ["a", "b", "c"]}]})
    examples, drops = build_from_change("openstack", CHANGE, comments, diff_for)
    assert examples == [] and drops["no_anchored_hunk"] == 1


def test_a_comment_on_the_last_patch_set_is_dropped_before_any_fetch() -> None:
    def comments(number: int) -> dict[str, list[dict[str, Any]]]:
        return {"nova/f.py": [{"patch_set": 2, "line": 2, "message": "x"}]}

    _, diff_for, calls = _fetchers()
    examples, drops = build_from_change("openstack", CHANGE, comments, diff_for)
    assert examples == [] and drops["no_successor"] == 1
    assert calls == [], "no diff should be fetched for a dropped comment"


def test_a_comment_with_no_line_anchor_is_dropped() -> None:
    def comments(number: int) -> dict[str, list[dict[str, Any]]]:
        return {"nova/f.py": [{"patch_set": 1, "message": "file-level note"}]}

    _, diff_for, calls = _fetchers()
    examples, drops = build_from_change("openstack", CHANGE, comments, diff_for)
    assert examples == [] and drops["no_line_anchor"] == 1
    assert calls == []


def test_a_diff_failure_is_counted_not_raised() -> None:
    def failing(number: int, rev: int, path: str, base: int) -> dict[str, Any]:
        raise RuntimeError("gerrit returned 404")

    comments, _, _ = _fetchers()
    examples, drops = build_from_change("openstack", CHANGE, comments, failing)
    assert examples == [] and drops["diff_error"] == 1
