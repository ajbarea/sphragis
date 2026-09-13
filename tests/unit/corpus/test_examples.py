"""Refinement pairs: changed hunks, comment anchoring, and the drop rules."""

from __future__ import annotations

from typing import Any

from sphragis.corpus.examples import build_examples, changed_hunks

CHANGE: dict[str, Any] = {
    "change_id": "I1",
    "project": "openstack/nova",
    "created": "2024-10-02 11:00:00.000000000",
}
BEFORE = ["def f(x):", "    return x+1", "", "def g():", "    pass"]
AFTER = ["def f(x):", "    return x + 1", "", "def g():", "    pass"]


def test_changed_hunks_finds_only_the_changed_region() -> None:
    hunks = changed_hunks(BEFORE, AFTER)
    assert len(hunks) == 1
    assert hunks[0].before == ("    return x+1",)
    assert hunks[0].after == ("    return x + 1",)
    assert hunks[0].before_start == 2


def test_changed_hunks_is_empty_for_identical_content() -> None:
    assert changed_hunks(BEFORE, BEFORE) == []


def test_build_examples_attaches_a_comment_anchored_inside_the_hunk() -> None:
    comments = [{"line": 2, "message": "spaces around the operator"}]
    examples, drops = build_examples(
        org="openstack",
        change=CHANGE,
        path="nova/f.py",
        before=BEFORE,
        after=AFTER,
        comments=comments,
    )
    assert len(examples) == 1 and drops["no_anchored_comment"] == 0
    example = examples[0]
    assert example["before"] == "    return x+1"
    assert example["after"] == "    return x + 1"
    assert example["comments"] == ["spaces around the operator"]
    assert example["org"] == "openstack"
    assert example["project"] == "openstack/nova"
    assert example["path"] == "nova/f.py"
    assert example["created"] == "2024-10-02 11:00:00.000000000"
    assert example["id"] == "openstack:I1:nova/f.py:2"


def test_build_examples_drops_a_hunk_whose_comment_falls_outside_it() -> None:
    examples, drops = build_examples(
        org="openstack",
        change=CHANGE,
        path="nova/f.py",
        before=BEFORE,
        after=AFTER,
        comments=[{"line": 5, "message": "unrelated"}],
    )
    assert examples == [] and drops["no_anchored_comment"] == 1


def test_build_examples_drops_file_level_comments_with_no_line() -> None:
    examples, drops = build_examples(
        org="openstack",
        change=CHANGE,
        path="nova/f.py",
        before=BEFORE,
        after=AFTER,
        comments=[{"message": "looks fine"}],
    )
    assert examples == [] and drops["no_anchored_comment"] == 1
