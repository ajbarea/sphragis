"""The GitHub route: pull requests shaped as Gerrit changes, so the Gerrit build runs unchanged."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
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
from sphragis.durable import DurableLog

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
        "raw file https://raw.githubusercontent.com/octocat/r/main/f",
        "docs at octocat.github.io/site",
    ],
    ids=["code span", "suggestion block", "profile link", "link target", "raw link", "pages"],
)
def test_a_participants_account_reference_is_replaced_wherever_it_appears(text: str) -> None:
    out = scrub_mentions(text, "salt", logins=["OctoCat"], keep_owners=["apache"])
    assert "octocat" not in out.lower()


def test_a_login_that_is_an_ordinary_word_does_not_corrupt_the_comment() -> None:
    """Review finding: a participant named `fix` turned "Please fix this" into a pseudonym."""
    assert scrub_mentions("Please fix this, @fix", "s", logins=["fix"]).startswith(
        "Please fix this"
    )
    assert "@fix" not in scrub_mentions("Please fix this, @fix", "s", logins=["fix"])


def test_the_organizations_own_links_are_kept() -> None:
    out = scrub_mentions("see https://github.com/apache/kafka/pull/1", "s", keep_owners=["apache"])
    assert "github.com/apache/kafka" in out


def _pr(**overrides: Any) -> dict[str, Any]:
    pr = {
        "number": 42,
        "created_at": "2025-01-15T10:20:30Z",
        "base_tip": "tip",
        "user": {"id": 1, "login": "author", "type": "User"},
    }
    return pr | overrides


HEADS = [
    {"sha": "c1", "committed_at": "2025-01-15T10:00:00Z", "parents": ["base0"]},
    {"sha": "c2", "committed_at": "2025-01-16T10:00:00Z", "parents": ["c1"]},
    {"sha": "c3", "committed_at": "2025-01-17T10:00:00Z", "parents": ["c2"]},
]
BEFORE = "a = 1\nb = 2\nc = 3\nd = compute(a, b)\ne = 5\n"
AFTER = "a = 1\nb = 2\nc = 3\nd = compute(a, b, c)\ne = 5\n"
UPSTREAM = "a = 1\nb = 2\nc = 3\nd = compute(a, b)\ne = 6\n"


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
    """File versions by commit (content-addressed, as git blob ids are), and fork points.

    `forks` maps (base tip, commit) to the commit's merge base with that tip, so a caller that
    asks against the wrong tip gets no answer and the test fails.
    """

    def __init__(
        self,
        versions: Mapping[str, str | None],
        forks: dict[tuple[str, str], str] | None = None,
    ) -> None:
        self.versions: dict[str, str | None] = dict(versions)
        self.forks = (
            forks
            if forks is not None
            else {("tip", sha): "base0" for sha in ("c1", "c2", "c3", "x1")}
        )
        self.versions.setdefault("base0", BEFORE)

    def file_oids(self, repo: str, path: str, shas: list[str]) -> dict[str, str | None]:
        return {sha: self.versions.get(sha) and f"oid:{self.versions[sha]}" for sha in shas}

    def blob_text(self, repo: str, oid: str) -> str | None:
        return oid.removeprefix("oid:")

    def fork_point(self, repo: str, base_tip: str, sha: str) -> str:
        return self.forks[(base_tip, sha)]


def _row(
    versions: Mapping[str, str | None],
    comments: list[dict[str, Any]] | None = None,
    heads: list[dict[str, Any]] | None = None,
    api: Files | None = None,
    **kw: Any,
):
    return row_from_pr(
        "org/repo",
        _pr(base_tip="tip"),
        heads or HEADS,
        comments or [_comment()],
        project="org/repo",
        salt="s",
        api=api or Files(versions, **kw),
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


def test_a_file_changed_between_the_commit_and_the_comment_is_an_outdated_view() -> None:
    """Review finding: those edits predate the comment, so they are no response to it."""
    late = _comment(created_at="2025-01-16T12:00:00Z")
    row = _row({"c1": BEFORE, "c2": "a = 100\n" + BEFORE, "c3": "a = 100\n" + AFTER}, [late])
    assert row[GITHUB_KEY]["outdated_view"] == 1 and row[GITHUB_KEY]["diffs"] == {}


def test_a_file_no_later_commit_changes_is_an_empty_diff() -> None:
    row = _row({"c1": BEFORE, "c2": BEFORE, "c3": BEFORE})
    assert row[GITHUB_KEY]["diffs"]["1:src/app.py"] == {"content": []}


def test_fork_points_are_taken_against_the_branch_before_the_pr_merged() -> None:
    """Review finding: after a merge commit every PR commit is reachable from the branch, so a
    merge base against its current head is the commit itself and every comment read upstream."""
    on_main_now = {("main", "c1"): "c1", ("main", "c2"): "c2"}
    before_merge = {("tip", "c1"): "base0", ("tip", "c2"): "base0"}
    row = _row({"c1": BEFORE, "c2": AFTER, "c3": AFTER}, forks=on_main_now | before_merge)
    assert row[GITHUB_KEY]["upstream_change"] == 0 and "1:src/app.py" in row[GITHUB_KEY]["diffs"]


@pytest.mark.parametrize(
    ("older", "newer"), [("base0", "base1"), ("base1", "base0")], ids=["forward", "backward"]
)
def test_the_base_branchs_own_edit_to_the_file_drops_the_comment(older: str, newer: str) -> None:
    """Review findings: the rebase guard, in both directions, with no 300-file list to miss."""
    versions = {"c1": BEFORE, "c2": AFTER, "c3": AFTER, "base0": BEFORE, "base1": UPSTREAM}
    forks = {("tip", "c1"): older, ("tip", "c2"): newer, ("tip", "c3"): newer}
    row = _row(versions, forks=forks)
    assert row[GITHUB_KEY]["upstream_change"] == 1 and row[GITHUB_KEY]["comments"] == {}


def test_a_rebase_that_left_the_file_alone_upstream_keeps_the_comment() -> None:
    versions = {"c1": BEFORE, "c2": AFTER, "c3": AFTER, "base0": BEFORE, "base1": BEFORE}
    forks = {("tip", "c1"): "base0", ("tip", "c2"): "base1", ("tip", "c3"): "base1"}
    row = _row(versions, forks=forks)
    assert row[GITHUB_KEY]["upstream_change"] == 0 and "1:src/app.py" in row[GITHUB_KEY]["diffs"]


def test_a_merge_commit_before_the_successor_drops_the_comment() -> None:
    merged = [HEADS[0], HEADS[1] | {"parents": ["c1", "main9"]}, HEADS[2]]
    row = _row({"c1": BEFORE, "c2": AFTER, "c3": AFTER}, heads=merged)
    assert row[GITHUB_KEY]["upstream_change"] == 1


def test_a_renamed_or_deleted_file_is_gone_not_unchanged() -> None:
    row = _row({"c1": BEFORE, "c2": None, "c3": None})
    assert row[GITHUB_KEY]["file_gone"] == 1 and row[GITHUB_KEY]["comments"] == {}


def test_a_binary_or_truncated_file_is_counted_unavailable() -> None:
    class Opaque(Files):
        def blob_text(self, repo: str, oid: str) -> str | None:
            return None

    row = _row({}, api=Opaque({"c1": BEFORE, "c2": AFTER, "c3": AFTER}))
    assert row[GITHUB_KEY]["text_unavailable"] == 1


def test_an_object_github_no_longer_holds_costs_its_comments_not_the_pr() -> None:
    """Review finding: one missing blob marked the whole PR withdrawn."""

    class Collected(Files):
        def blob_text(self, repo: str, oid: str) -> str | None:
            raise Gone("garbage collected")

    row = _row({}, api=Collected({"c1": BEFORE, "c2": AFTER, "c3": AFTER}))
    assert row[GITHUB_KEY]["object_gone"] == 1 and row["change_id"] == "org/repo#42"


def test_a_row_reaches_disk_with_no_login_or_raw_id() -> None:
    comment = _comment(body="@reviewer thinks `@author` should see github.com/author/fork")
    row = _row({"c1": BEFORE, "c2": AFTER, "c3": AFTER}, [comment])
    text = json.dumps(row)
    assert "@reviewer" not in text and "@author" not in text and "author/fork" not in text
    assert row["owner"]["_account_id"] != 1


class FakeAPI(Files):
    """A GitHub API with an LLVM PR, an agent PR, a withdrawn PR and a failing PR on 2025-01-02."""

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
            "base_tip": "tip",
        }
        return [
            base | {"number": 42, "user": person},
            base | {"number": 43, "user": agent},
            base | {"number": 7, "user": person},
            base | {"number": 9, "user": person},
        ]

    def pr_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        if number == 7:
            raise Gone("404")
        if number == 9:
            raise RuntimeError("GitHub kept refusing after 8 attempts")
        return [_comment(path="clang/lib/Sema.cpp")]

    def pr_heads(self, repo: str, number: int) -> list[dict[str, Any]]:
        return HEADS

    def commit_times(self, repo: str, shas: list[str]) -> list[dict[str, Any]]:
        return []

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
    assert record["listed"] == 4 and record["agent_authored"] == 1 and record["withdrawn"] == 1
    assert record["failed"] == 1 and "llvm/llvm-project#9" in record["failures"][0]
    assert record["route"] == "github" and record["github_rules"] == GITHUB_RULES


def test_only_registered_organizations_are_collected() -> None:
    with pytest.raises(SystemExit, match="not a registered GitHub organization"):
        collect_month("someone-else", "2025-01", FakeAPI(), salt="s")
    assert GITHUB_ORGS["openjdk"].qualifier.startswith("is:closed label:integrated")


def test_a_commit_only_a_comment_names_is_added_in_commit_time_order() -> None:
    """A non-head commit of an earlier push: the timeline omits it, the comment names it."""

    class Held(FakeAPI):
        def commit_times(self, repo: str, shas: list[str]) -> list[dict[str, Any]]:
            x1 = {"sha": "x1", "committed_at": "2025-01-15T11:00:00Z", "parents": ["c1"]}
            return [x1] if "x1" in shas else []

    comments = [_comment(original_commit_id="x1"), _comment(original_commit_id="lost")]
    heads = with_commented_commits("org/repo", HEADS, comments, Held())
    assert [h["sha"] for h in heads] == ["c1", "x1", "c2", "c3"]
    _, _, dropped = change_from_pr("org/repo", _pr(), heads, comments, project="org/repo")
    assert dropped == {"rewritten_history": 1}, "only the commit GitHub no longer holds"


def test_a_pr_with_no_reviewer_inline_comment_costs_one_request() -> None:
    """Most PRs carry no inline review; they are counted, not walked."""

    class Quiet(FakeAPI):
        def __init__(self) -> None:
            super().__init__()
            self.walked: list[int] = []

        def pr_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
            if number == 42:
                return [_comment(user={"id": 1, "login": "author", "type": "User"})]
            return super().pr_comments(repo, number)

        def pr_heads(self, repo: str, number: int) -> list[dict[str, Any]]:
            self.walked.append(number)
            return HEADS

    api = Quiet()
    rows, record = collect_month("llvm", "2025-01", api, salt="s")
    assert rows == [] and record["no_inline_review"] == 1 and api.walked == []


def test_a_month_resumes_from_its_checkpoint(tmp_path: Any) -> None:
    """A long month that fails partway keeps every finished PR."""

    class Counting(FakeAPI):
        def __init__(self) -> None:
            super().__init__()
            self.fetched: list[int] = []

        def pr_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
            self.fetched.append(number)
            return super().pr_comments(repo, number)

    checkpoint = tmp_path / "2025-01.partial.jsonl"
    first = Counting()
    rows, record = collect_month("llvm", "2025-01", first, salt="s", checkpoint=checkpoint)
    again = Counting()
    rows_again, record_again = collect_month(
        "llvm", "2025-01", again, salt="s", checkpoint=checkpoint
    )
    assert rows_again == rows and record_again == record
    assert again.fetched == [9], "every PR came back from the checkpoint but the failed one"


def test_a_month_resumes_from_a_checkpoint_torn_by_a_hard_stop(tmp_path: Any) -> None:
    """Zeros where the checkpoint grew before its data landed: the month resumes, unchanged."""

    class Counting(FakeAPI):
        def __init__(self) -> None:
            super().__init__()
            self.fetched: list[int] = []

        def pr_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
            self.fetched.append(number)
            return super().pr_comments(repo, number)

    checkpoint = DurableLog(tmp_path, "2025-01.partial.jsonl")
    rows, record = collect_month("llvm", "2025-01", FakeAPI(), salt="s", checkpoint=checkpoint)
    whole = checkpoint.read_bytes()
    last = whole.rstrip(b"\n").rfind(b"\n") + 1
    torn = int(json.loads(whole[last:])["key"].rsplit("#", 1)[1])
    checkpoint.write_bytes(whole[:last] + whole[last : last + 10] + b"\0" * 1539)
    again = Counting()
    rows_again, record_again = collect_month(
        "llvm", "2025-01", again, salt="s", checkpoint=checkpoint
    )
    assert rows_again == rows and record_again == record
    assert set(again.fetched) == {torn, 9}, "only the torn PR, and the failed one, asked again"
    assert checkpoint.read_bytes().count(b"\0") == 0


def test_a_resumed_month_asks_again_for_a_pr_that_failed(tmp_path: Any) -> None:
    """A failure checkpointed on one run is retried on the next, and its new outcome kept."""

    class Recovered(FakeAPI):
        def pr_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
            if number == 9:
                return [_comment(path="clang/lib/Sema.cpp")]
            return super().pr_comments(repo, number)

    checkpoint = tmp_path / "2025-01.partial.jsonl"
    _, failed = collect_month("llvm", "2025-01", FakeAPI(), salt="s", checkpoint=checkpoint)
    rows, record = collect_month("llvm", "2025-01", Recovered(), salt="s", checkpoint=checkpoint)
    assert failed["failed"] == 1 and record["failed"] == 0
    assert "llvm/llvm-project#9" in {row["change_id"] for row in rows}
    _, replayed = collect_month("llvm", "2025-01", FakeAPI(), salt="s", checkpoint=checkpoint)
    assert replayed == record, "the recovered outcome, written after the failure, is the one read"


def test_the_route_module_imports_no_storage_plumbing() -> None:
    # GITHUB_RULES digests github.py's text, so any edit to it marks every GitHub month fetched
    # so far as fetched under other rules. Durability lives in the checkpoint object instead.
    import ast

    from sphragis.corpus import github

    tree = ast.parse(Path(github.__file__).read_text())
    imported = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    plumbing = {"sphragis.durable", "sphragis.corpus.storage"}
    assert not {name for name in imported if name and (name in plumbing or name == "sphragis")}, (
        "import rule code only; storage belongs to the checkpoint the caller passes"
    )
