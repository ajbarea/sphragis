"""The GitHub route: pull requests shaped as Gerrit changes, so the Gerrit build runs unchanged."""

from __future__ import annotations

from typing import Any

import pytest

from sphragis.corpus.build import build_from_change
from sphragis.corpus.examples import hunks_from_diff
from sphragis.corpus.github import (
    AGENT_LOGINS,
    account,
    change_from_pr,
    diff_fetcher,
    diff_from_patch,
    scrub_mentions,
)

PATCH = """@@ -2,7 +2,7 @@ def main():
 a = 1
 b = 2
 c = 3
-d = compute(a, b)
+d = compute(a, b, c)
 e = 5
 f = 6
 g = 7
@@ -20,6 +20,7 @@ def tail():
 x = 1
 y = 2
 z = 3
+w = 4
 v = 5
 u = 6
 t = 7"""


def test_a_patch_becomes_gerrit_blocks_with_line_numbers_and_real_context() -> None:
    hunks = hunks_from_diff(diff_from_patch(PATCH), context=3)
    assert [(h.before_start, h.before, h.after) for h in hunks] == [
        (5, ("d = compute(a, b)",), ("d = compute(a, b, c)",)),
        (23, (), ("w = 4",)),
    ]
    first, second = hunks
    assert first.context_before == ("a = 1", "b = 2", "c = 3")
    assert first.context_after == ("e = 5", "f = 6", "g = 7")
    assert second.context_before == ("x = 1", "y = 2", "z = 3")


def test_padding_between_hunks_is_never_read_as_context() -> None:
    diff = diff_from_patch(PATCH)
    for hunk in hunks_from_diff(diff, context=3):
        assert not any(line == "" for line in hunk.context_before + hunk.context_after)


def test_a_patch_ending_without_newline_marker_is_parsed() -> None:
    patch = "@@ -1,2 +1,2 @@\n-old\n+new\n same\n\\ No newline at end of file"
    (hunk,) = hunks_from_diff(diff_from_patch(patch), context=3)
    assert hunk.before == ("old",) and hunk.after == ("new",)


def test_bots_and_ai_agents_are_service_users_and_people_are_not() -> None:
    assert account({"id": 7, "login": "dependabot[bot]", "type": "Bot"}) == {
        "_account_id": 7,
        "tags": ["SERVICE_USER"],
    }
    agent = sorted(AGENT_LOGINS)[0]
    assert account({"id": 8, "login": agent, "type": "User"}) == {
        "_account_id": 8,
        "tags": ["SERVICE_USER"],
    }
    assert account({"id": 9, "login": "a-reviewer", "type": "User"}) == {"_account_id": 9}
    assert account(None) is None


def test_mentions_are_pseudonymised_and_code_and_emails_left_to_their_own_rules() -> None:
    text = "cc @alice-dev, see @org/team; the `@property` decorator\n```python\n@cache\n```"
    out = scrub_mentions(text, "salt")
    assert "alice-dev" not in out and "org/team" not in out
    assert "`@property`" in out and "@cache" in out, "code is not a person"
    assert scrub_mentions(text, "salt") == out, "stable under the corpus salt"


def _pr(**overrides: Any) -> dict[str, Any]:
    pr = {
        "number": 42,
        "created_at": "2025-01-15T10:20:30Z",
        "user": {"id": 1, "login": "author", "type": "User"},
    }
    return pr | overrides


COMMITS = [{"sha": "c1"}, {"sha": "c2"}, {"sha": "c3"}]


def _comment(**overrides: Any) -> dict[str, Any]:
    comment = {
        "id": 100,
        "user": {"id": 2, "login": "reviewer", "type": "User"},
        "body": "Pass c through as well.",
        "path": "src/app.py",
        "original_commit_id": "c1",
        "original_line": 5,
        "side": "RIGHT",
    }
    return comment | overrides


def test_a_pr_becomes_a_change_with_patch_sets_numbered_by_commit() -> None:
    change, comments, dropped = change_from_pr(
        "org/repo", _pr(), COMMITS, [_comment()], project="org/repo"
    )
    assert change["_number"] == 42 and change["change_id"] == "org/repo#42"
    assert change["created"] == "2025-01-15 10:20:30.000000000"
    assert change["owner"] == {"_account_id": 1}
    assert len(change["revisions"]) == 3
    (only,) = comments["src/app.py"]
    assert (only["patch_set"], only["line"], only["message"]) == (1, 5, "Pass c through as well.")
    assert dropped == {"rewritten_history": 0}


def test_a_comment_on_the_base_side_has_no_line_on_its_patch_set() -> None:
    _, comments, _ = change_from_pr(
        "org/repo", _pr(), COMMITS, [_comment(side="LEFT")], project="org/repo"
    )
    assert comments["src/app.py"][0]["line"] is None


def test_a_comment_on_a_commit_force_pushed_away_is_counted_apart() -> None:
    _, comments, dropped = change_from_pr(
        "org/repo", _pr(), COMMITS, [_comment(original_commit_id="gone")], project="org/repo"
    )
    assert comments == {} and dropped == {"rewritten_history": 1}


def test_the_gerrit_build_turns_a_github_pr_into_an_example() -> None:
    change, comments, _ = change_from_pr(
        "org/repo", _pr(), COMMITS, [_comment()], project="org/repo"
    )
    patches = {("c1", "c2", "src/app.py"): PATCH}

    def compare(base: str, head: str, path: str) -> str:
        return patches[(base, head, path)]

    examples, drops = build_from_change(
        "github-org", change, lambda number: comments, diff_fetcher(COMMITS, compare)
    )
    (example,) = examples
    assert example["before"] == "d = compute(a, b)" and example["after"] == "d = compute(a, b, c)"
    assert example["comments"] == ["Pass c through as well."]
    assert drops["diff_error"] == 0


def test_a_missing_patch_raises_so_the_build_counts_a_diff_error() -> None:
    fetch = diff_fetcher(COMMITS, lambda base, head, path: None)
    with pytest.raises(LookupError):
        fetch(42, 2, "src/app.py", 1)


def test_interleaved_removals_and_additions_lose_no_line() -> None:
    """One change region, as Gerrit reports it: every removed and every added line kept."""
    patch = "@@ -1,5 +1,5 @@\n keep\n-a\n+b\n-c\n+d\n tail"
    (hunk,) = hunks_from_diff(diff_from_patch(patch), context=3)
    assert hunk.before == ("a", "c") and hunk.after == ("b", "d")
    assert hunk.before_start == 2
