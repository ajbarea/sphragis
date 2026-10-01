"""The GitHub route: pull requests shaped as Gerrit changes, so the Gerrit build runs unchanged."""

from __future__ import annotations

import json
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
    unified_patch,
    with_commented_commits,
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
    for hunk in hunks_from_diff(diff_from_patch(PATCH), context=3):
        assert not any(line == "" for line in hunk.context_before + hunk.context_after)


def test_a_patch_ending_without_newline_marker_is_parsed() -> None:
    patch = "@@ -1,2 +1,2 @@\n-old\n+new\n same\n\\ No newline at end of file"
    (hunk,) = hunks_from_diff(diff_from_patch(patch), context=3)
    assert hunk.before == ("old",) and hunk.after == ("new",)


def test_interleaved_removals_and_additions_lose_no_line() -> None:
    patch = "@@ -1,5 +1,5 @@\n keep\n-a\n+b\n-c\n+d\n tail"
    (hunk,) = hunks_from_diff(diff_from_patch(patch), context=3)
    assert hunk.before == ("a", "c") and hunk.after == ("b", "d")
    assert hunk.before_start == 2


@pytest.mark.parametrize("separator", [" ", "\f", "\x85", "\x1c"])
def test_a_line_separator_inside_a_source_line_does_not_split_it(separator: str) -> None:
    """Review finding: `splitlines` split these and shifted the anchor by a line."""
    patch = f"@@ -1,3 +1,3 @@\n a{separator} x\n-old\n+new\n tail"
    (hunk,) = hunks_from_diff(diff_from_patch(patch), context=3)
    assert hunk.before_start == 2 and hunk.context_before == (f"a{separator} x",)


def test_unified_patch_is_gits_diff_of_the_two_versions() -> None:
    before = "a\nb\nc\nd = compute(a, b)\ne\n"
    after = "a\nb\nc\nd = compute(a, b, c)\ne\n"
    (hunk,) = hunks_from_diff(diff_from_patch(unified_patch(before, after)), context=3)
    assert (hunk.before_start, hunk.before, hunk.after) == (
        4,
        ("d = compute(a, b)",),
        ("d = compute(a, b, c)",),
    )
    assert unified_patch(before, before) == ""


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


def test_mentions_in_prose_are_pseudonymised_and_decorators_in_code_are_not() -> None:
    text = "cc @alice-dev, see @org/team; the `@property` decorator\n```python\n@cache\n```"
    out = scrub_mentions(text, "salt")
    assert "alice-dev" not in out and "org/team" not in out
    assert "`@property`" in out and "@cache" in out
    assert scrub_mentions(text, "salt") == out, "stable under the corpus salt"


@pytest.mark.parametrize(
    "text",
    [
        "see `@octocat` here",
        "```suggestion\n# owner: @octocat\n```",
        "forked at https://github.com/octocat/repo/commit/abc",
        "[@x](https://github.com/octocat)",
        "thanks octocat!",
    ],
    ids=["code span", "suggestion block", "profile link", "link target", "bare login"],
)
def test_a_participants_login_is_replaced_wherever_it_appears(text: str) -> None:
    """Review finding: these forms reached disk; a known participant's login never does."""
    out = scrub_mentions(text, "salt", logins=["OctoCat"], keep_owners=["apache"])
    assert "octocat" not in out.lower()


def test_the_organizations_own_links_are_kept() -> None:
    out = scrub_mentions("see https://github.com/apache/kafka/pull/1", "s", keep_owners=["apache"])
    assert "github.com/apache/kafka" in out


def _pr(**overrides: Any) -> dict[str, Any]:
    pr = {
        "number": 42,
        "created_at": "2025-01-15T10:20:30Z",
        "base_ref": "main",
        "user": {"id": 1, "login": "author", "type": "User"},
    }
    return pr | overrides


HEADS = [
    {"sha": "c1", "committed_at": "2025-01-15T10:00:00Z"},
    {"sha": "c2", "committed_at": "2025-01-16T10:00:00Z"},
    {"sha": "c3", "committed_at": "2025-01-17T10:00:00Z"},
]
BEFORE = "a = 1\nb = 2\nc = 3\nd = compute(a, b)\ne = 5\n"
AFTER = "a = 1\nb = 2\nc = 3\nd = compute(a, b, c)\ne = 5\n"


def _comment(**overrides: Any) -> dict[str, Any]:
    comment = {
        "id": 100,
        "user": {"id": 2, "login": "reviewer", "type": "User"},
        "body": "Pass c through as well.",
        "path": "src/app.py",
        "original_commit_id": "c1",
        "original_line": 4,
        "side": "RIGHT",
        "created_at": "2025-01-15T12:00:00Z",
    }
    return comment | overrides


class Files:
    """File versions by commit, fork points by commit, and upstream changes between forks."""

    def __init__(
        self,
        versions: dict[str, str | None],
        forks: dict[str, str] | None = None,
        upstream: set[str] | None = None,
    ) -> None:
        self.versions, self.forks, self.upstream = versions, forks or {}, upstream or set()
        self.blobs: list[str] = []

    def file_oids(self, repo: str, path: str, shas: list[str]) -> dict[str, str | None]:
        """Content-addressed, as git's blob ids are: equal text, equal id."""
        return {sha: self.versions.get(sha) and f"oid:{self.versions[sha]}" for sha in shas}

    def blob_text(self, repo: str, oid: str) -> str | None:
        self.blobs.append(oid)
        return oid.removeprefix("oid:")

    def fork_point(self, repo: str, base_ref: str, sha: str) -> str:
        return self.forks.get(sha, "base0")

    def changed_paths(self, repo: str, older: str, newer: str) -> set[str]:
        return self.upstream


def _row(versions: dict[str, str | None], comments: list[dict[str, Any]] | None = None, **kw: Any):
    return row_from_pr(
        "org/repo",
        _pr(),
        HEADS,
        comments or [_comment()],
        project="org/repo",
        salt="s",
        api=Files(versions, **kw),
    )


def test_a_pr_becomes_a_change_with_patch_sets_in_commit_time_order() -> None:
    change, comments, dropped = change_from_pr(
        "org/repo", _pr(), HEADS, [_comment()], project="org/repo"
    )
    assert change["_number"] == 42 and change["change_id"] == "org/repo#42"
    assert change["created"] == "2025-01-15 10:20:30.000000000"
    assert change["owner"] == {"_account_id": 1}
    assert len(change["revisions"]) == 3
    (only,) = comments["src/app.py"]
    assert (only["patch_set"], only["line"]) == (1, 4)
    assert dropped == {"rewritten_history": 0}


def test_a_comment_on_the_base_side_has_no_line_on_its_patch_set() -> None:
    _, comments, _ = change_from_pr(
        "org/repo", _pr(), HEADS, [_comment(side="LEFT")], project="org/repo"
    )
    assert comments["src/app.py"][0]["line"] is None


def test_a_comment_on_a_commit_the_timeline_lacks_is_counted_apart() -> None:
    _, comments, dropped = change_from_pr(
        "org/repo", _pr(), HEADS, [_comment(original_commit_id="gone")], project="org/repo"
    )
    assert comments == {} and dropped == {"rewritten_history": 1}


def test_the_gerrit_build_turns_a_github_pr_into_an_example_offline() -> None:
    row = _row({"c1": BEFORE, "c2": AFTER, "c3": AFTER})
    comments, diff = github_fetchers(row)
    examples, drops = build_from_change("github-org", row, comments, diff)
    (example,) = examples
    assert example["before"] == "d = compute(a, b)" and example["after"] == "d = compute(a, b, c)"
    assert example["comments"] == ["Pass c through as well."]
    assert drops["diff_error"] == 0


def test_the_successor_is_the_first_commit_after_the_comment_that_changes_the_file() -> None:
    """An increment that leaves the file alone is skipped; the diff is k to that later change."""
    row = _row({"c1": BEFORE, "c2": BEFORE, "c3": AFTER})
    (hunk,) = hunks_from_diff(row[GITHUB_KEY]["diffs"]["1:src/app.py"], context=3)
    assert hunk.after == ("d = compute(a, b, c)",)


def test_a_commit_made_before_the_comment_is_not_its_response() -> None:
    late = _comment(created_at="2025-01-16T12:00:00Z")
    row = _row({"c1": BEFORE, "c2": "edited before the review\n", "c3": AFTER}, [late])
    (hunk,) = hunks_from_diff(row[GITHUB_KEY]["diffs"]["1:src/app.py"], context=3)
    assert hunk.after == ("d = compute(a, b, c)",), "c2 predates the comment"


def test_a_file_no_later_commit_changes_is_an_empty_diff() -> None:
    row = _row({"c1": BEFORE, "c2": BEFORE, "c3": BEFORE})
    assert row[GITHUB_KEY]["diffs"]["1:src/app.py"] == {"content": []}


def test_the_diff_is_between_the_two_file_versions_whatever_their_ancestry() -> None:
    """Review finding: compare's merge base made a force-pushed successor's diff start at main."""
    row = _row(
        {"c1": BEFORE, "c2": AFTER, "c3": AFTER},
        forks={"c1": "base0", "c2": "base0"},
    )
    (hunk,) = hunks_from_diff(row[GITHUB_KEY]["diffs"]["1:src/app.py"], context=3)
    assert hunk.before == ("d = compute(a, b)",), "the file at the commented commit, not main"


def test_upstream_edits_to_the_file_between_fork_points_drop_the_comment() -> None:
    """Review finding: the rebase guard. A merge or rebase that changed the file upstream."""
    row = _row(
        {"c1": BEFORE, "c2": AFTER, "c3": AFTER},
        forks={"c1": "base0", "c2": "base1"},
        upstream={"src/app.py"},
    )
    assert row[GITHUB_KEY]["upstream_change"] == 1 and row[GITHUB_KEY]["comments"] == {}


def test_a_rebase_that_left_the_file_alone_upstream_keeps_the_comment() -> None:
    row = _row(
        {"c1": BEFORE, "c2": AFTER, "c3": AFTER},
        forks={"c1": "base0", "c2": "base1"},
        upstream={"other/file.py"},
    )
    assert row[GITHUB_KEY]["upstream_change"] == 0 and "1:src/app.py" in row[GITHUB_KEY]["diffs"]


def test_a_renamed_or_deleted_file_is_gone_not_unchanged() -> None:
    """Review finding: compare listed the new name, so a rename read as no change."""
    row = _row({"c1": BEFORE, "c2": None, "c3": None})
    assert row[GITHUB_KEY]["file_gone"] == 1 and row[GITHUB_KEY]["comments"] == {}


def test_a_binary_or_truncated_file_is_counted_unavailable() -> None:
    class Opaque(Files):
        def blob_text(self, repo: str, oid: str) -> str | None:
            return None

    row = row_from_pr(
        "org/repo",
        _pr(),
        HEADS,
        [_comment()],
        project="org/repo",
        salt="s",
        api=Opaque({"c1": BEFORE, "c2": AFTER, "c3": AFTER}),
    )
    assert row[GITHUB_KEY]["text_unavailable"] == 1


def test_a_row_reaches_disk_with_no_login_or_raw_id() -> None:
    comment = _comment(body="@reviewer thinks `author` should see github.com/author/fork")
    row = _row({"c1": BEFORE, "c2": AFTER, "c3": AFTER}, [comment])
    text = json.dumps(row)
    assert '"reviewer"' not in text and "author/fork" not in text and "@reviewer" not in text
    assert row["owner"]["_account_id"] != 1


class FakeAPI(Files):
    """A GitHub API with an LLVM PR, an agent PR and a withdrawn PR on 2025-01-02."""

    def __init__(self) -> None:
        super().__init__({"c1": BEFORE, "c2": AFTER, "c3": AFTER})

    def merged_prs(self, owner: str, day: str, *, qualifier: str) -> list[dict[str, Any]]:
        if day != "2025-01-02":
            return []
        person = {"id": 1, "login": "author", "type": "User"}
        agent = {"id": 5, "login": "copilot-swe-agent", "type": "Bot"}
        base = {
            "repo": "llvm/llvm-project",
            "created_at": "2025-01-01T00:00:00Z",
            "base_ref": "main",
        }
        return [
            base | {"number": 42, "user": person},
            base | {"number": 43, "user": agent},
            base | {"number": 7, "user": person},
        ]

    def pr_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        if number == 7:
            raise Gone("404")
        return [_comment(path="clang/lib/Sema.cpp")]

    def pr_heads(self, repo: str, number: int) -> list[dict[str, Any]]:
        return HEADS

    def commit_times(self, repo: str, shas: list[str]) -> dict[str, str]:
        return {}

    def pr_files(self, repo: str, number: int) -> list[dict[str, Any]]:
        return [
            {"filename": "clang/lib/Sema.cpp", "changes": 40},
            {"filename": "llvm/lib/IR.cpp", "changes": 3},
        ]


def test_a_month_skips_agent_prs_counts_withdrawn_ones_and_assigns_llvm_projects() -> None:
    rows, record = collect_month("llvm", "2025-01", FakeAPI(), salt="s")
    (row,) = rows
    assert row["project"] == "llvm/llvm-project:clang"
    assert row[GITHUB_KEY]["repo"] == "llvm/llvm-project"
    assert record["listed"] == 3 and record["agent_authored"] == 1 and record["withdrawn"] == 1
    assert record["route"] == "github" and record["github_rules"] == GITHUB_RULES


def test_only_registered_organizations_are_collected() -> None:
    with pytest.raises(SystemExit, match="not a registered GitHub organization"):
        collect_month("someone-else", "2025-01", FakeAPI(), salt="s")
    assert GITHUB_ORGS["openjdk"].qualifier.startswith("is:closed label:integrated")


def test_a_commit_only_a_comment_names_is_added_in_commit_time_order() -> None:
    """A non-head commit of an earlier push: the timeline omits it, the comment names it."""

    class Held(FakeAPI):
        def commit_times(self, repo: str, shas: list[str]) -> dict[str, str]:
            return {"x1": "2025-01-15T11:00:00Z"} if "x1" in shas else {}

    comments = [_comment(original_commit_id="x1"), _comment(original_commit_id="lost")]
    heads = with_commented_commits("org/repo", HEADS, comments, Held())
    assert [h["sha"] for h in heads] == ["c1", "x1", "c2", "c3"]
    _, _, dropped = change_from_pr("org/repo", _pr(), heads, comments, project="org/repo")
    assert dropped == {"rewritten_history": 1}, "only the commit GitHub no longer holds"
