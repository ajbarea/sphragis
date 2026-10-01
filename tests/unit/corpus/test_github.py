"""The GitHub route: pull requests shaped as Gerrit changes, so the Gerrit build runs unchanged."""

from __future__ import annotations

from typing import Any

import pytest

from sphragis.corpus.build import build_from_change
from sphragis.corpus.examples import hunks_from_diff
from sphragis.corpus.github import (
    AGENT_LOGINS,
    GITHUB_KEY,
    GITHUB_ORGS,
    account,
    change_from_pr,
    collect_month,
    diff_from_patch,
    github_fetchers,
    row_from_pr,
    scrub_mentions,
)
from sphragis.corpus.github_api import Gone
from sphragis.corpus.rules import GITHUB_RULES

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
    row = row_from_pr("org/repo", _pr(), COMMITS, [_comment()], project="org/repo", salt="s")
    comments, diff = github_fetchers(row, lambda repo, base, head: {"src/app.py": PATCH})
    examples, drops = build_from_change("github-org", row, comments, diff)
    (example,) = examples
    assert example["before"] == "d = compute(a, b)" and example["after"] == "d = compute(a, b, c)"
    assert example["comments"] == ["Pass c through as well."]
    assert drops["diff_error"] == 0


def test_a_patch_github_omits_raises_so_the_build_counts_a_diff_error() -> None:
    row = row_from_pr("org/repo", _pr(), COMMITS, [_comment()], project="org/repo", salt="s")
    _, diff = github_fetchers(row, lambda repo, base, head: {"src/app.py": None})
    with pytest.raises(LookupError):
        diff(42, 2, "src/app.py", 1)


def _compare_files(calls: list[tuple[str, str, str]]):
    def compare(repo: str, base: str, head: str) -> dict[str, str | None]:
        calls.append((repo, base, head))
        return {"src/app.py": PATCH, "big.bin": None}

    return compare


def test_a_row_carries_scrubbed_comments_and_the_build_reads_it() -> None:
    comment = _comment(body="Pass c through as well, @alice-dev.")
    row = row_from_pr("org/repo", _pr(), COMMITS, [comment], project="org/repo", salt="s")
    assert row["owner"]["_account_id"] != 1, "the owner is pseudonymised before disk"
    carried = row[GITHUB_KEY]
    (stored,) = carried["comments"]["src/app.py"]
    assert stored["author"]["_account_id"] != 2 and "alice-dev" not in stored["message"]
    assert carried["shas"] == ["c1", "c2", "c3"] and carried["repo"] == "org/repo"
    calls: list[tuple[str, str, str]] = []
    comments, diff = github_fetchers(row, _compare_files(calls))
    examples, drops = build_from_change("github-org", row, comments, diff)
    assert len(examples) == 1 and drops["author_comment"] == 0
    assert calls == [("org/repo", "c1", "c2")]


def test_one_compare_request_serves_every_file_of_a_commit_pair() -> None:
    row = row_from_pr("org/repo", _pr(), COMMITS, [_comment()], project="org/repo", salt="s")
    calls: list[tuple[str, str, str]] = []
    _, diff = github_fetchers(row, _compare_files(calls))
    diff(42, 2, "src/app.py", 1)
    diff(42, 2, "src/app.py", 1)
    with pytest.raises(LookupError):
        diff(42, 2, "big.bin", 1)
    assert len(calls) == 1


class FakeAPI:
    """A GitHub API with two PRs on 2025-01-02 and none on other days."""

    def __init__(self) -> None:
        self.gone = {7}

    def merged_prs(self, owner: str, day: str, *, qualifier: str) -> list[dict[str, Any]]:
        if day != "2025-01-02":
            return []
        person = {"id": 1, "login": "author", "type": "User"}
        agent = {"id": 5, "login": "copilot-swe-agent", "type": "Bot"}
        return [
            {
                "repo": "llvm/llvm-project",
                "number": 42,
                "created_at": "2025-01-01T00:00:00Z",
                "user": person,
            },
            {
                "repo": "llvm/llvm-project",
                "number": 43,
                "created_at": "2025-01-01T00:00:00Z",
                "user": agent,
            },
            {
                "repo": "llvm/llvm-project",
                "number": 7,
                "created_at": "2025-01-01T00:00:00Z",
                "user": person,
            },
        ]

    def pr_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        if number in self.gone:
            raise Gone("404")
        return [_comment(path="clang/lib/Sema.cpp")]

    def pr_heads(self, repo: str, number: int) -> list[dict[str, Any]]:
        return COMMITS

    def pr_files(self, repo: str, number: int) -> list[dict[str, Any]]:
        return [
            {"filename": "clang/lib/Sema.cpp", "changes": 40},
            {"filename": "llvm/lib/IR.cpp", "changes": 3},
        ]


def test_a_month_skips_agent_prs_counts_withdrawn_ones_and_assigns_llvm_projects() -> None:
    rows, record = collect_month("llvm", "2025-01", FakeAPI(), salt="s")
    (row,) = rows
    assert row["project"] == "llvm/llvm-project:clang", "the top directory with most changed lines"
    assert row[GITHUB_KEY]["repo"] == "llvm/llvm-project"
    assert record["listed"] == 3 and record["agent_authored"] == 1 and record["withdrawn"] == 1
    assert record["route"] == "github" and record["github_rules"] == GITHUB_RULES


def test_only_registered_organizations_are_collected() -> None:
    with pytest.raises(SystemExit, match="not a registered GitHub organization"):
        collect_month("someone-else", "2025-01", FakeAPI(), salt="s")
    assert GITHUB_ORGS["openjdk"].qualifier.startswith("is:closed label:integrated")


def test_the_successor_is_the_first_later_commit_that_changes_the_file() -> None:
    """GitHub commits are incremental: a fix to the commented file may come commits later."""
    row = row_from_pr("org/repo", _pr(), COMMITS, [_comment()], project="org/repo", salt="s")
    patches: dict[tuple[str, str], dict[str, str | None]] = {
        ("c1", "c2"): {"other.py": "@@ -1 +1 @@\n-x\n+y"},
        ("c1", "c3"): {"other.py": "@@ -1 +1 @@\n-x\n+y", "src/app.py": PATCH},
    }
    calls: list[tuple[str, str]] = []

    def compare(repo: str, base: str, head: str) -> dict[str, str | None]:
        calls.append((base, head))
        return patches[(base, head)]

    _, diff = github_fetchers(row, compare)
    (hunk, *_) = hunks_from_diff(diff(42, 2, "src/app.py", 1), context=3)
    assert hunk.after == ("d = compute(a, b, c)",) and calls == [("c1", "c2"), ("c1", "c3")]


def test_a_file_no_later_commit_changes_is_an_empty_diff_not_an_error() -> None:
    """Gerrit's diff of an untouched file has no hunk; the build counts no_anchored_hunk."""
    row = row_from_pr("org/repo", _pr(), COMMITS, [_comment()], project="org/repo", salt="s")
    _, diff = github_fetchers(row, lambda repo, base, head: {"other.py": "@@ -1 +1 @@\n-x\n+y"})
    assert diff(42, 2, "src/app.py", 1) == {"content": []}
