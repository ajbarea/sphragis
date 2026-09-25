"""The port of Gerrit's diff, against JGit's recorded output and a captured Gerrit payload."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sphragis.corpus import gerrit_diff
from sphragis.corpus.examples import hunks_from_diff

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


def _jgit_cases() -> list[dict]:
    return json.loads((FIXTURES / "jgit_edits.json").read_text())["cases"]


def test_the_jgit_fixture_exercises_the_myers_fallback() -> None:
    # Without these the fallback half of the port would pass untested: none of 233 real AOSP
    # file pairs reached it.
    assert sum(case["myers_fallback"] for case in _jgit_cases()) >= 20


@pytest.mark.parametrize("index", range(len(_jgit_cases())))
def test_edits_match_what_jgit_computed(index: int) -> None:
    case = _jgit_cases()[index]
    a = gerrit_diff.RawText(case["a"].encode())
    b = gerrit_diff.RawText(case["b"].encode())
    found = [[e.begin_a, e.end_a, e.begin_b, e.end_b] for e in gerrit_diff.edits(a, b)]
    assert found == case["edits"]


def _sides(diff: dict) -> tuple[bytes, bytes]:
    """Both files back out of a Gerrit `content` payload, which carries every line."""
    before: list[str] = []
    after: list[str] = []
    for block in diff["content"]:
        before += block.get("ab", []) + block.get("a", [])
        after += block.get("ab", []) + block.get("b", [])
    return "\n".join(before).encode(), "\n".join(after).encode()


def test_the_captured_gerrit_diff_is_reproduced_block_for_block() -> None:
    captured = json.loads((FIXTURES / "gerrit_diff.json").read_text())
    before, after = _sides(captured)
    assert gerrit_diff.diff(before, after)["content"] == captured["content"]


def test_a_whitespace_only_change_is_a_common_block_and_still_a_hunk() -> None:
    # Gerrit ignores whitespace by default and emits such lines as `common: true`; the REST
    # corpus built hunks from them, so the port must emit them too.
    content = gerrit_diff.diff(b"a\n  b\nc\n", b"a\n    b\nc\n")["content"]
    assert content == [
        {"ab": ["a"]},
        {"a": ["  b"], "b": ["    b"], "common": True},
        {"ab": ["c", ""]},
    ]
    assert [h.before for h in hunks_from_diff({"content": content})] == [("  b",)]


def test_a_file_ending_in_a_newline_has_a_final_empty_line() -> None:
    assert gerrit_diff.diff(b"x\n", b"x\n")["content"] == [{"ab": ["x", ""]}]
    assert gerrit_diff.diff(b"x", b"x")["content"] == [{"ab": ["x"]}]


def test_a_dropped_final_newline_is_its_own_edit() -> None:
    content = gerrit_diff.diff(b"a\nb\n", b"a\nb")["content"]
    assert content == [{"ab": ["a", "b"]}, {"a": [""]}]


def test_an_added_file_is_all_insertion() -> None:
    assert gerrit_diff.diff(None, b"new\n")["content"] == [{"b": ["new", ""]}]


@pytest.mark.parametrize(
    ("data", "binary"),
    [
        (b"text\n", False),
        (b"crlf\r\nok\r\n", False),
        (b"nul\x00byte", True),
        (b"lone\rcr", True),
        (b"ends in cr\r", True),
        (b"x" * 9000 + b"\x00", True),
        (b"y\n" * 5000, False),
    ],
)
def test_binary_is_jgits_verdict(data: bytes, binary: bool) -> None:
    assert gerrit_diff.is_binary(data) is binary


def test_a_binary_side_yields_no_content() -> None:
    assert gerrit_diff.diff(b"a\x00", b"b\x00") == {"binary": True, "content": []}


# Upstream changed line 2 between the parents; the author wrote line 4 and fixed it at n+1.
PARENT_A = b"a\nb\nc\n"
PARENT_B = b"a\nB\nc\n"
BEFORE = b"a\nb\nc\nx=1\n"
AFTER = b"a\nB\nc\nx = 1\n"


def test_an_edit_the_parents_made_is_marked_due_to_rebase_and_the_authors_is_not() -> None:
    content = gerrit_diff.diff(BEFORE, AFTER, parents=(PARENT_A, PARENT_B))["content"]
    assert content == [
        {"ab": ["a"]},
        {"a": ["b"], "b": ["B"], "due_to_rebase": True},
        {"ab": ["c"]},
        {"a": ["x=1"], "b": ["x = 1"]},
        {"ab": [""]},
    ]
    assert [h.before for h in hunks_from_diff({"content": content})] == [("b",), ("x=1",)]


def test_without_parents_nothing_is_marked() -> None:
    content = gerrit_diff.diff(BEFORE, AFTER)["content"]
    assert not any(block.get("due_to_rebase") for block in content)


def test_an_upstream_edit_the_author_also_touched_is_not_attributed_to_the_rebase() -> None:
    # The author edited line 2 at patch set n, so the parents' edit there collides with the
    # change's own and Gerrit omits it rather than guess.
    before = b"a\nb2\nc\n"
    upstream, lost = gerrit_diff.rebase_edits(PARENT_A, PARENT_B, before, b"a\nB\nc\n")
    assert upstream == set() and lost == 1


def test_an_upstream_edit_is_placed_in_patch_set_coordinates() -> None:
    # The author inserted two lines above the upstream edit, so it moves down by two on side A.
    before = b"new1\nnew2\na\nb\nc\n"
    after = b"new1\nnew2\na\nB\nc\n"
    upstream, lost = gerrit_diff.rebase_edits(PARENT_A, PARENT_B, before, after)
    assert upstream == {(3, 4, 3, 4)} and lost == 0


@pytest.mark.parametrize(
    ("a_parents", "b_parents", "a", "b", "expected"),
    [
        (["p"], ["q"], "A", "B", False),
        (["p"], ["p"], "A", "B", True),
        (["p"], ["A"], "A", "B", True),
        (["p", "m"], ["q"], "A", "B", True),
        ([], ["q"], "A", "B", True),
    ],
)
def test_related_is_gerrits_test_for_skipping_rebase_edits(
    a_parents: list[str], b_parents: list[str], a: str, b: str, expected: bool
) -> None:
    assert gerrit_diff.related(a_parents, b_parents, a, b) is expected
