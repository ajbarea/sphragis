"""The NoteDb route, read over `file://` from a server repository built here in NoteDb's shape."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from sphragis.corpus import cli, notedb
from sphragis.corpus.build import build_from_change
from sphragis.corpus.pacing import Pacer
from sphragis.corpus.scrub import pseudonym

SERVER_ID = "0f0f0f0f-1111-2222-3333-444455556666"
OWNER, REVIEWER, SUBMITTER = 1000, 2000, 3000
CHANGE_ID = "I" + "a" * 40
PROJECT = "proj"


class Server:
    """A bare repository holding what a Gerrit host serves: branches, patch sets and NoteDb."""

    def __init__(self, root: Path) -> None:
        self.path = root / f"{PROJECT}.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.path)], check=True)
        for key, value in (
            ("uploadpack.allowFilter", "true"),
            ("uploadpack.allowAnySHA1InWant", "true"),
        ):
            self.git("config", key, value)

    @property
    def url(self) -> str:
        return f"file://{self.path.parent}"

    def git(self, *args: str, stdin: bytes | None = None, env: dict[str, str] | None = None) -> str:
        done = subprocess.run(
            ["git", "--git-dir", str(self.path), *args],
            input=stdin,
            capture_output=True,
            check=True,
            env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, **(env or {})},
        )
        return done.stdout.decode().strip()

    def tree(self, files: dict[str, bytes]) -> str:
        """A tree of blobs, nested directories included."""
        entries: dict[str, dict[str, bytes] | bytes] = {}
        for path, data in files.items():
            head, _, rest = path.partition("/")
            if rest:
                sub = entries.setdefault(head, {})
                assert isinstance(sub, dict)
                sub[rest] = data
            else:
                entries[head] = data
        lines = []
        for name, value in sorted(entries.items()):
            if isinstance(value, dict):
                lines.append(f"040000 tree {self.tree(value)}\t{name}")
            else:
                oid = self.git("hash-object", "-w", "--stdin", stdin=value)
                lines.append(f"100644 blob {oid}\t{name}")
        return self.git("mktree", stdin=("\n".join(lines) + "\n").encode() if lines else b"")

    def commit(
        self,
        tree: str,
        message: str,
        when: str,
        parents: tuple[str, ...] = (),
        author: tuple[str, str] = ("Dev", "dev@example.invalid"),
        committer: tuple[str, str] | None = None,
    ) -> str:
        committer = committer or author
        env = {
            "GIT_AUTHOR_NAME": author[0],
            "GIT_AUTHOR_EMAIL": author[1],
            "GIT_AUTHOR_DATE": when,
            "GIT_COMMITTER_NAME": committer[0],
            "GIT_COMMITTER_EMAIL": committer[1],
            "GIT_COMMITTER_DATE": when,
        }
        args = [a for p in parents for a in ("-p", p)]
        return self.git("commit-tree", tree, *args, "-m", message, env=env)

    def ref(self, name: str, oid: str) -> None:
        self.git("update-ref", name, oid)


def _account(account: int) -> tuple[str, str]:
    return (f"Gerrit User {account}", f"{account}@{SERVER_ID}")


SERVER_IDENT = ("Gerrit Code Review", "gerrit@example.invalid")


def _meta(server: Server, number: int, steps: list[tuple[int, str, str, dict[str, bytes]]]) -> str:
    """A meta chain: one commit per (account, when, message, notes) step."""
    parents: tuple[str, ...] = ()
    tip = ""
    for account, when, message, notes in steps:
        tip = server.commit(
            server.tree(notes),
            message,
            when,
            parents,
            author=_account(account),
            committer=SERVER_IDENT,
        )
        parents = (tip,)
    server.ref(notedb.change_ref(number, "meta"), tip)
    return tip


def _message(subject: str, body: str, *footers: str) -> str:
    """A NoteDb commit message: subject, change message, then the footer paragraph."""
    return f"{subject}\n\n{body}\n\n" + "\n".join(footers)


def _comment(
    uuid: str, ps: int, line: int, account: int, when: str, message: str, rev: str
) -> dict:
    return {
        "key": {"uuid": uuid, "filename": "src/a.py", "patchSetId": ps},
        "lineNbr": line,
        "author": {"id": account},
        "writtenOn": when,
        "side": 1,
        "message": message,
        "revId": rev,
        "serverId": SERVER_ID,
        "unresolved": True,
    }


@pytest.fixture
def server(tmp_path: Path) -> Server:
    """Change 1234 merged on 2024-11-05 (AOSP style: its patch set 2 is the branch tip), and
    change 1299 merged 2024-12-02, outside November."""
    s = Server(tmp_path / "server")
    base = s.commit(s.tree({"src/a.py": b"x = 1\ny = 2\n"}), "base", "2024-10-20T00:00:00Z")
    ps1 = s.commit(
        s.tree({"src/a.py": b"x=1\ny = 2\nz = 3\n"}),
        "Fix\n\nChange-Id: I1",
        "2024-11-02T10:00:00Z",
        (base,),
    )
    ps2 = s.commit(
        s.tree({"src/a.py": b"x = 1\ny = 2\nz = 3\n"}),
        "Fix\n\nChange-Id: I1",
        "2024-11-04T10:00:00Z",
        (base,),
    )
    late = s.commit(
        s.tree({"src/a.py": b"x = 1\ny = 2\nz = 4\n"}), "Late", "2024-11-20T00:00:00Z", (ps2,)
    )
    s.ref("refs/changes/34/1234/1", ps1)
    s.ref("refs/changes/34/1234/2", ps2)
    s.ref("refs/changes/99/1299/1", late)
    s.ref("refs/heads/main", late)
    note = json.dumps(
        {
            "comments": [
                _comment("r1", 1, 1, REVIEWER, "2024-11-03T09:00:00Z", "Use spaces around =", ps1),
                _comment("o1", 1, 1, OWNER, "2024-11-03T12:00:00Z", "Done", ps1),
                _comment("f1", 1, 0, REVIEWER, "2024-11-03T09:05:00Z", "file level", ps1),
            ]
        }
    ).encode()
    footer = f"Change-id: {CHANGE_ID}\nSubject: Fix spacing\nBranch: refs/heads/main"
    _meta(
        s,
        1234,
        [
            (
                OWNER,
                "2024-11-02T10:00:00Z",
                _message(
                    "Create change",
                    "Uploaded patch set 1.",
                    "Patch-set: 1",
                    footer,
                    "Status: new",
                    "Topic: ",
                    f"Commit: {ps1}",
                ),
                {},
            ),
            (
                REVIEWER,
                "2024-11-03T09:00:00Z",
                "Update patch set 1\n\nPatch Set 1:\n\n(1 comment)\n\nPatch-set: 1",
                {ps1: note},
            ),
            (
                OWNER,
                "2024-11-04T10:00:00Z",
                _message(
                    "Update patch set 2",
                    "Uploaded patch set 2.",
                    "Patch-set: 2",
                    "Subject: Fix spacing",
                    f"Commit: {ps2}",
                ),
                {ps1: note},
            ),
            (
                SUBMITTER,
                "2024-11-05T08:00:00Z",
                _message(
                    "Update patch set 2",
                    "Change has been successfully merged",
                    "Patch-set: 2",
                    "Status: merged",
                    "Submission-id: 1234-1",
                    "Hashtags: b,a",
                ),
                {ps1: note},
            ),
        ],
    )
    _meta(
        s,
        1299,
        [
            (
                OWNER,
                "2024-11-20T00:00:00Z",
                _message(
                    "Create change",
                    "Uploaded patch set 1.",
                    "Patch-set: 1",
                    f"Change-id: I{'b' * 40}",
                    "Subject: Late",
                    "Branch: refs/heads/main",
                    "Status: new",
                    f"Commit: {late}",
                ),
                {},
            ),
            (
                SUBMITTER,
                "2024-12-02T00:00:00Z",
                "Update patch set 1\n\nPatch-set: 1\nStatus: merged\nSubmission-id: 1299-1",
                {},
            ),
        ],
    )
    return s


def _repo(server: Server, tmp_path: Path, pacer: Pacer | None = None) -> notedb.Repo:
    return notedb.Repo.open(tmp_path / "client.git", f"{server.url}/{PROJECT}", pacer or Pacer(0))


def test_a_change_ref_uses_the_last_two_digits() -> None:
    assert notedb.change_ref(2923712, "meta") == "refs/changes/12/2923712/meta"
    assert notedb.change_ref(5, 1) == "refs/changes/05/5/1"


def test_read_change_follows_the_notedb_parser(server: Server, tmp_path: Path) -> None:
    repo = _repo(server, tmp_path)
    assert repo.fetch_refs("meta", [notedb.change_ref(1234, "meta")]) == []
    record = notedb.read_change(repo, 1234)
    assert (record.owner, record.submitter, record.status) == (OWNER, SUBMITTER, "MERGED")
    assert notedb.gerrit_timestamp(record.created or 0) == "2024-11-02 10:00:00.000000000"
    assert notedb.gerrit_timestamp(record.submitted or 0) == "2024-11-05 08:00:00.000000000"
    assert sorted(record.patch_sets) == [1, 2]
    assert record.patch_sets[2]["uploader"] == OWNER
    assert record.hashtags == ["a", "b"]
    assert len(record.comments) == 3


def test_a_deleted_patch_set_is_not_a_revision(tmp_path: Path) -> None:
    s = Server(tmp_path / "server")
    tree = s.tree({"f": b"1\n"})
    one = s.commit(tree, "one", "2024-11-01T00:00:00Z")
    two = s.commit(tree, "two", "2024-11-02T00:00:00Z")
    _meta(
        s,
        7,
        [
            (
                OWNER,
                "2024-11-01T00:00:00Z",
                f"Create\n\nPatch-set: 1\nChange-id: I7\nCommit: {one}",
                {},
            ),
            (OWNER, "2024-11-02T00:00:00Z", f"Upload\n\nPatch-set: 2\nCommit: {two}", {}),
            (OWNER, "2024-11-03T00:00:00Z", "Delete\n\nPatch-set: 2 (DELETED)", {}),
        ],
    )
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))
    repo.fetch_refs("meta", [notedb.change_ref(7, "meta")])
    assert sorted(notedb.read_change(repo, 7).patch_sets) == [1]


def test_rest_comments_take_the_rest_shape_and_order() -> None:
    comments = [
        {
            **_comment("b", 1, 5, 1, "2024-11-03T09:00:00.5Z", "second", "c"),
            "range": {"startLine": 5, "startChar": 1, "endLine": 5, "endChar": 4},
        },
        {**_comment("a", 1, 0, 1, "2024-11-03T09:00:00Z", "file level", "c")},
        {**_comment("c", 1, 5, 1, "2024-11-03T08:00:00Z", "on the parent", "c"), "side": 0},
    ]
    by_path = notedb.rest_comments(comments)
    ordered = by_path["src/a.py"]
    assert [c["id"] for c in ordered] == ["c", "a", "b"], "parent side first, then by line"
    assert "line" not in ordered[1], "a file-level comment has no line, as in REST"
    assert ordered[0]["side"] == "PARENT"
    assert ordered[2]["range"] == {
        "start_line": 5,
        "start_character": 1,
        "end_line": 5,
        "end_character": 4,
    }
    assert ordered[2]["updated"] == "2024-11-03 09:00:00.500000000"
    assert ordered[2]["author"] == {"_account_id": 1}


def test_reviewed_on_matches_only_this_host_and_project() -> None:
    message = (
        "Fix\n\nChange-Id: I1\nReviewed-on: https://other-review.example/c/proj/+/9\n"
        "Reviewed-on: https://review.example/c/proj/+/1234\n"
    )
    assert notedb.reviewed_on(message, "review.example", "proj") == 1234
    assert notedb.reviewed_on(message, "review.example", "elsewhere") is None
    assert notedb.reviewed_on("no trailer", "review.example", "proj") is None


def test_the_trailer_finds_a_cherry_picked_change(tmp_path: Path) -> None:
    s = Server(tmp_path / "server")
    landed = s.commit(
        s.tree({"f": b"1\n"}),
        "Fix\n\nChange-Id: I1\nReviewed-on: https://review.example/c/proj/+/42",
        "2024-11-10T00:00:00Z",
    )
    s.ref("refs/heads/main", landed)
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))
    notedb.fetch_history(repo, ["refs/heads/main"], "2024-10-01")
    found, counts = notedb.merged_commits(
        repo,
        ["refs/heads/main"],
        "2024-11-01",
        "2024-12-01",
        review_host="review.example",
        project="proj",
    )
    assert [(m.number, m.via, m.commit) for m in found] == [(42, "trailer", landed)]
    assert counts == {"commits": 1, "by_trailer": 1}


def test_fetch_month_yields_scrubbed_rows_that_build_the_same_examples(
    server: Server, tmp_path: Path
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    rows, record = notedb.fetch_month(
        "aosp", [PROJECT], "2024-11", "salt", pacer=Pacer(0), base_url=server.url, workdir=scratch
    )
    assert list(scratch.iterdir()) == [], "the scratch repository holding raw identities is gone"
    assert [row["_number"] for row in rows] == [1234], "1299 merged in December"
    assert record["projects"][PROJECT]["collect"]["submitted_after"] == 1
    row = rows[0]
    assert row["owner"] == {"_account_id": pseudonym(OWNER, "salt")}
    assert row["submitter"] == {"_account_id": pseudonym(SUBMITTER, "salt")}
    assert row["created"] == "2024-11-02 10:00:00.000000000"
    assert row["id"] == f"{PROJECT}~1234" and row["status"] == "MERGED"
    assert len(row["revisions"]) == 2
    assert row["files"] == {"src/a.py": {"lines_inserted": 1, "lines_deleted": 0}}
    assert row["merged_commit"] == server.git("rev-parse", "refs/changes/34/1234/2")
    authors = {c["author"]["_account_id"] for c in row[notedb.NOTEDB_KEY]["comments"]["src/a.py"]}
    assert authors == {pseudonym(REVIEWER, "salt"), pseudonym(OWNER, "salt")}
    assert "Gerrit User" not in json.dumps(row) and SERVER_ID not in json.dumps(row)

    examples, drops = build_from_change("aosp", row, *notedb.embedded_fetchers(row))
    assert [(e["before"], e["after"], e["comments"]) for e in examples] == [
        ("x=1", "x = 1", ["Use spaces around ="])
    ]
    assert examples[0]["id"] == f"aosp:{CHANGE_ID}:src/a.py:1:1"
    assert drops["author_comment"] == 1 and drops["no_line_anchor"] == 1


def test_a_change_touching_the_sealed_window_is_dropped_before_any_notes_fetch(
    tmp_path: Path,
) -> None:
    """Its last NoteDb update (2025-11-03) reaches the sealed test window (starts 2025-11-01),
    though the change itself is a candidate for 2025-10: dropped before the meta chain or
    notes cost anything, not merely before it reaches a row."""
    s = Server(tmp_path / "server")
    commit = s.commit(s.tree({"f": b"1\n"}), "one", "2025-10-05T00:00:00Z")
    note = json.dumps(
        {"comments": [_comment("c1", 1, 1, REVIEWER, "2025-11-03T00:00:00Z", "hi", commit)]}
    ).encode()
    _meta(
        s,
        88,
        [
            (
                OWNER,
                "2025-10-05T00:00:00Z",
                f"Create\n\nPatch-set: 1\nChange-id: I88\nCommit: {commit}",
                {},
            ),
            (
                REVIEWER,
                "2025-11-03T00:00:00Z",
                "Update patch set 1\n\nPatch-set: 1",
                {commit: note},
            ),
        ],
    )
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))
    rows, counts = notedb.collect(repo, [88], project=PROJECT)
    assert rows == []
    assert counts["touches_test_window"] == 1
    purposes = {entry["purpose"] for entry in repo.log}
    assert "notes" not in purposes and "meta" not in purposes, (
        f"only the depth-1 tip probe should run, got {purposes}"
    )


def test_a_late_comment_on_an_earlier_change_never_reaches_a_persisted_row(
    tmp_path: Path,
) -> None:
    """The change merged in September; a comment landed in November, inside the sealed
    window. `fetch_month` for September must not persist that comment, or any row for it."""
    s = Server(tmp_path / "server")
    commit = s.commit(s.tree({"f": b"1\n"}), "one", "2025-09-05T00:00:00Z")
    s.ref("refs/heads/main", commit)
    s.ref("refs/changes/89/89/1", commit)
    note = json.dumps(
        {"comments": [_comment("c1", 1, 1, REVIEWER, "2025-11-03T00:00:00Z", "late", commit)]}
    ).encode()
    _meta(
        s,
        89,
        [
            (
                OWNER,
                "2025-09-05T00:00:00Z",
                "Create\n\nPatch-set: 1\nChange-id: I89\nBranch: refs/heads/main\n"
                f"Commit: {commit}",
                {},
            ),
            (
                SUBMITTER,
                "2025-09-05T01:00:00Z",
                "Update patch set 1\n\nPatch-set: 1\nStatus: merged\nSubmission-id: 89-1",
                {},
            ),
            (
                REVIEWER,
                "2025-11-03T00:00:00Z",
                "Update patch set 1\n\nPatch-set: 1",
                {commit: note},
            ),
        ],
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    rows, record = notedb.fetch_month(
        "aosp", [PROJECT], "2025-09", "salt", pacer=Pacer(0), base_url=s.url, workdir=scratch
    )
    assert rows == [], "no row -- and so no comment -- reaches the snapshot"
    assert record["projects"][PROJECT]["collect"]["touches_test_window"] == 1


def test_a_vanished_meta_ref_is_counted_not_fatal(server: Server, tmp_path: Path) -> None:
    repo = _repo(server, tmp_path)
    rows, counts = notedb.collect(repo, [1234, 5555], project=PROJECT)
    assert [r["_number"] for r in rows] == [1234]
    assert counts["meta_missing"] == 1


def test_a_missing_object_fails_rather_than_fetching_lazily(server: Server, tmp_path: Path) -> None:
    repo = _repo(server, tmp_path)
    notedb.fetch_history(repo, ["refs/heads/main"], "2024-10-01")
    blob = server.git("rev-parse", "refs/heads/main:src/a.py")
    assert repo.missing([blob]) == [blob], "history is fetched without blobs"
    with pytest.raises(
        notedb.GitError, match="lazy fetching disabled|could not fetch|not in the repository"
    ):
        repo.read_objects([blob])
    assert repo.operations == 1, "the failed read made no network operation"


def test_git_operations_against_one_host_are_paced(server: Server, tmp_path: Path) -> None:
    now, slept = [0.0], []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    repo = _repo(server, tmp_path, Pacer(1.5, clock=lambda: now[0], sleep=sleep))
    notedb.fetch_history(repo, ["refs/heads/main"], "2024-10-01")
    repo.fetch_refs("meta", [notedb.change_ref(1234, "meta")])
    assert slept == pytest.approx([1.5])
    assert [entry["purpose"] for entry in repo.log] == ["history", "meta"]


def test_month_bounds_roll_over_the_year() -> None:
    assert notedb.month_bounds("2024-12") == ("2024-12-01", "2025-01-01")


def test_candidates_reach_back_further_for_a_host_that_merges_uploads_unchanged() -> None:
    assert notedb.candidates_since("aosp", "2024-11-01") == "2024-08-03"
    assert notedb.candidates_since("chromium", "2024-11-01") == "2024-10-18"


def test_a_rebased_step_marks_the_upstream_hunk_and_builds_as_rest_does(tmp_path: Path) -> None:
    """Patch set 2 sits on a newer base that changed line 2; the author fixed line 4.

    The reviewer commented on both lines. The diff marks the upstream hunk `due_to_rebase`, as
    Gerrit's REST payload does, and the build ignores the flag on both routes, so a NoteDb
    organization is built under the same rules as a REST one.
    """
    s = Server(tmp_path / "server")
    old_base = s.commit(s.tree({"f.py": b"a\nb\nc\n"}), "base", "2024-10-01T00:00:00Z")
    new_base = s.commit(
        s.tree({"f.py": b"a\nB\nc\n"}), "upstream", "2024-10-05T00:00:00Z", (old_base,)
    )
    ps1 = s.commit(s.tree({"f.py": b"a\nb\nc\nx=1\n"}), "Fix", "2024-10-02T00:00:00Z", (old_base,))
    ps2 = s.commit(
        s.tree({"f.py": b"a\nB\nc\nx = 1\n"}), "Fix", "2024-10-06T00:00:00Z", (new_base,)
    )
    s.ref("refs/changes/77/77/1", ps1)
    s.ref("refs/changes/77/77/2", ps2)
    s.ref("refs/heads/main", new_base)

    def on(uuid: str, line: int, message: str) -> dict:
        return {
            **_comment(uuid, 1, line, REVIEWER, "2024-10-03T00:00:00Z", message, ps1),
            "key": {"uuid": uuid, "filename": "f.py", "patchSetId": 1},
        }

    note = json.dumps({"comments": [on("u", 2, "why b?"), on("v", 4, "spaces")]}).encode()
    _meta(
        s,
        77,
        [
            (
                OWNER,
                "2024-10-02T00:00:00Z",
                _message(
                    "Create change",
                    "Uploaded.",
                    "Patch-set: 1",
                    "Change-id: I77",
                    "Branch: refs/heads/main",
                    f"Commit: {ps1}",
                ),
                {},
            ),
            (
                REVIEWER,
                "2024-10-03T00:00:00Z",
                _message("Update patch set 1", "(2 comments)", "Patch-set: 1"),
                {ps1: note},
            ),
            (
                OWNER,
                "2024-10-06T00:00:00Z",
                _message("Update patch set 2", "Uploaded.", "Patch-set: 2", f"Commit: {ps2}"),
                {ps1: note},
            ),
        ],
    )
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))
    rows, counts = notedb.collect(repo, [77], project=PROJECT)
    row = rows[0]
    assert counts["rebase_steps"] == 1
    assert {r["_number"]: r["parents"] for r in row["revisions"].values()} == {
        1: [old_base],
        2: [new_base],
    }
    blocks = row[notedb.NOTEDB_KEY]["diffs"]["1:f.py"]["content"]
    assert [b.get("due_to_rebase", False) for b in blocks if "ab" not in b] == [True, False]

    built, _ = build_from_change("aosp", row, *notedb.embedded_fetchers(row))
    assert sorted(e["comments"] for e in built) == [["spaces"], ["why b?"]]


def test_a_depth_fetch_does_not_cut_the_branch_history_short(tmp_path: Path) -> None:
    """In a shallow repository, fetching a patch set at depth 2 marks its parent shallow even
    when that parent and its own parents are held. Here the parent is the tip of a merged side
    branch, so the mark hid the side branch's first commit from every walk of main; the
    repair removes marks whose parents are all present."""
    s = Server(tmp_path / "server")

    def commit(name: str, when: str, *parents: str) -> str:
        return s.commit(s.tree({"f": name.encode()}), name, f"2024-09-{when}Z", parents)

    base = commit("base", "01T00:00:00")
    side_1 = commit("side 1", "02T00:00:00", base)
    side_2 = commit("side 2", "03T00:00:00", side_1)
    main_1 = commit("main 1", "02T12:00:00", base)
    merge = commit("merge", "04T00:00:00", main_1, side_2)
    tip = commit("tip", "05T00:00:00", merge)
    s.ref("refs/heads/main", tip)
    patch_set = commit("patch set", "06T00:00:00", side_2)
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))
    notedb.fetch_history(repo, ["refs/heads/main"], "2024-09-01T12:00:00")
    walk = repo.git("rev-list", "refs/heads/main").decode().split()
    assert side_1 in walk and base not in walk, "a shallow history, as the route always has"
    repo.fetch_objects("patch_sets", [patch_set], "--depth=2", "--filter=tree:0")
    assert side_1 in repo.git("rev-list", "refs/heads/main").decode().split()


# ---------------------------------------------------------------------------
# Since/until bound on the merge-commit log, and branch scope (items 5, 6)
# ---------------------------------------------------------------------------


def test_merged_commits_git_log_is_bounded_by_since_and_until(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `git log` call itself carries `--since`/`--until`, not only the Python-side check."""
    s = Server(tmp_path / "server")
    landed = s.commit(
        s.tree({"f": b"1\n"}),
        "Fix\n\nChange-Id: I1\nReviewed-on: https://review.example/c/proj/+/42",
        "2024-11-10T00:00:00Z",
    )
    s.ref("refs/heads/main", landed)
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))
    notedb.fetch_history(repo, ["refs/heads/main"], "2024-10-01")
    calls: list[list[str]] = []
    real_git = repo.git

    def spy(*args: str, stdin: bytes | None = None) -> bytes:
        calls.append(list(args))
        return real_git(*args, stdin=stdin)

    monkeypatch.setattr(repo, "git", spy)
    notedb.merged_commits(
        repo,
        ["refs/heads/main"],
        "2024-11-01",
        "2024-12-01",
        review_host="review.example",
        project="proj",
    )
    log_call = next(c for c in calls if c[0] == "log")
    assert any(a.startswith("--since=2024-11-01") for a in log_call)
    assert any(a.startswith("--until=2024-12-01") for a in log_call)


def test_branch_refs_defaults_to_every_branch_and_dedupes_a_cherry_pick(tmp_path: Path) -> None:
    """`release` carries a change `main` never sees; change 20 lands separately on each, once."""
    s = Server(tmp_path / "server")
    base = s.commit(s.tree({"f": b"0\n"}), "base", "2024-10-01T00:00:00Z")
    on_main = s.commit(
        s.tree({"f": b"2\n"}),
        "Cherry\n\nChange-Id: I2\nReviewed-on: https://review.example/c/proj/+/20",
        "2024-11-06T00:00:00Z",
        (base,),
    )
    release_only = s.commit(
        s.tree({"f": b"1\n"}),
        "Fix\n\nChange-Id: I1\nReviewed-on: https://review.example/c/proj/+/10",
        "2024-11-05T00:00:00Z",
        (base,),
    )
    on_release = s.commit(
        s.tree({"f": b"2\n"}),
        "Cherry\n\nChange-Id: I2\nReviewed-on: https://review.example/c/proj/+/20",
        "2024-11-07T00:00:00Z",
        (release_only,),
    )
    s.ref("refs/heads/main", on_main)
    s.ref("refs/heads/release", on_release)
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))

    refs = notedb.branch_refs(repo, "aosp", ())
    assert refs == ["refs/heads/main", "refs/heads/release"]
    notedb.fetch_history(repo, refs, "2024-10-01")
    found, counts = notedb.merged_commits(
        repo, refs, "2024-11-01", "2024-12-01", review_host="review.example", project="proj"
    )
    assert sorted(m.number for m in found if m.number is not None) == [10, 20], (
        "release-only change is found too"
    )
    assert counts["duplicate_change"] == 1, "the cherry-picked change is counted, not doubled"

    main_only = notedb.branch_refs(repo, "aosp", ["main"])
    assert main_only == ["refs/heads/main"]
    found_main, _ = notedb.merged_commits(
        repo, main_only, "2024-11-01", "2024-12-01", review_host="review.example", project="proj"
    )
    assert sorted(m.number for m in found_main if m.number is not None) == [20], (
        "release-only change is out of scope"
    )


def test_chromium_also_reads_branch_heads(tmp_path: Path) -> None:
    s = Server(tmp_path / "server")
    s.ref("refs/heads/main", s.commit(s.tree({"f": b"1\n"}), "m", "2024-11-01T00:00:00Z"))
    s.ref("refs/branch-heads/4.4", s.commit(s.tree({"f": b"2\n"}), "b", "2024-11-01T00:00:00Z"))
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))
    assert notedb.branch_refs(repo, "chromium", ()) == [
        "refs/branch-heads/4.4",
        "refs/heads/main",
    ]
    assert notedb.branch_refs(repo, "aosp", ()) == ["refs/heads/main"], (
        "an org with no extra namespace reads refs/heads/* only"
    )


# ---------------------------------------------------------------------------
# Integration: a change's commit that is itself a branch tip (item 11A)
# ---------------------------------------------------------------------------


def _fetch_month_with_release_tip(
    tmp_path: Path, branches: list[str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """`release` points directly at a change's patch set 2, `main` points elsewhere.

    `fetch_history` shallow-fetches `release`'s tip (--filter=tree:0), so the scratch repo
    already holds that patch-set commit -- without its tree -- before `collect` ever runs.
    Reproduces the reviewer's trigger: `collect`'s later tree fetch, by resolved tree id, of a
    commit already held this way failed with `fatal: bad revision ... did not send all
    necessary objects`, git's own shallow/partial-clone negotiation getting confused about an
    object it already partly holds.
    """
    s = Server(tmp_path / "server")
    root = s.commit(s.tree({"f": b"line1\nline2\n"}), "root", "2024-09-01T00:00:00Z")
    main_tip = s.commit(
        s.tree({"f": b"line1\nMAIN\n"}), "unrelated", "2024-09-02T00:00:00Z", (root,)
    )
    ps1 = s.commit(
        s.tree({"f": b"line1\nline2\n", "g": b"x\n"}), "Fix", "2024-09-05T00:00:00Z", (root,)
    )
    ps2 = s.commit(
        s.tree({"f": b"line1\nline2\n", "g": b"y\n"}), "Fix", "2024-09-07T00:00:00Z", (root,)
    )
    s.ref("refs/heads/main", main_tip)
    s.ref("refs/heads/release", ps2)
    s.ref("refs/changes/98/98/1", ps1)
    s.ref("refs/changes/98/98/2", ps2)
    _meta(
        s,
        98,
        [
            (
                OWNER,
                "2024-09-05T00:00:00Z",
                _message(
                    "Create change",
                    "Uploaded.",
                    "Patch-set: 1",
                    "Change-id: I98",
                    "Branch: refs/heads/release",
                    f"Commit: {ps1}",
                ),
                {},
            ),
            (
                OWNER,
                "2024-09-07T00:00:00Z",
                _message("Update patch set 2", "Uploaded.", "Patch-set: 2", f"Commit: {ps2}"),
                {},
            ),
            (
                SUBMITTER,
                "2024-09-08T00:00:00Z",
                _message(
                    "Update patch set 2",
                    "Change has been successfully merged",
                    "Patch-set: 2",
                    "Status: merged",
                    "Submission-id: 98-1",
                ),
                {},
            ),
        ],
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return notedb.fetch_month(
        "aosp", [PROJECT], "2024-09", "salt", pacer=Pacer(0), base_url=s.url, workdir=scratch,
        branches=branches,
    )  # fmt: skip


def test_all_branches_does_not_crash_when_a_changes_commit_is_a_branch_tip(
    tmp_path: Path,
) -> None:
    rows, record = _fetch_month_with_release_tip(tmp_path, [])
    assert [row["_number"] for row in rows] == [98]
    assert record["projects"][PROJECT]["branches_read"] == [
        "refs/heads/main",
        "refs/heads/release",
    ]


def test_restricting_to_the_tip_branch_does_not_crash_either(tmp_path: Path) -> None:
    rows, record = _fetch_month_with_release_tip(tmp_path, ["release"])
    assert [row["_number"] for row in rows] == [98]
    assert record["projects"][PROJECT]["branches_read"] == ["refs/heads/release"]


# ---------------------------------------------------------------------------
# Host allowlist, pacing floor, redirects (item 2)
# ---------------------------------------------------------------------------


def test_git_permitted_states_a_reason_and_a_positive_floor_for_every_host() -> None:
    for permission in notedb.GIT_PERMITTED.values():
        assert permission.reason
        assert permission.min_interval > 0


def test_android_is_permitted_and_chromium_is_not_yet() -> None:
    assert notedb.GIT_PERMITTED["android.googlesource.com"].permitted is True
    assert notedb.GIT_PERMITTED["chromium.googlesource.com"].permitted is False


def _forbid_network_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """After this, a `git fetch` or `ls-remote` fails the test; local bookkeeping still runs."""
    real_run = notedb.subprocess.run

    def guarded(cmd: list[str], *a: Any, **kw: Any) -> subprocess.CompletedProcess[bytes]:
        if any(verb in cmd for verb in ("fetch", "ls-remote")):
            raise AssertionError(f"a network git command ran despite refused permission: {cmd!r}")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(notedb.subprocess, "run", guarded)


def test_git_refuses_an_unpermitted_host_before_touching_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never a real subprocess: proven by a spy, not by reading a DNS failure's text."""
    repo = notedb.Repo.open(tmp_path / "c.git", "https://chromium.googlesource.com/x", Pacer(0))
    _forbid_network_subprocess(monkeypatch)
    with pytest.raises(notedb.GitError, match="chromium.googlesource.com"):
        repo.fetch("history", ["+refs/heads/main:refs/heads/main"])


def test_git_refuses_a_host_with_no_recorded_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = notedb.Repo.open(tmp_path / "c.git", "https://gerrit.example.org/x", Pacer(0))
    _forbid_network_subprocess(monkeypatch)
    with pytest.raises(notedb.GitError, match="not recorded"):
        repo.fetch("history", ["+refs/heads/main:refs/heads/main"])


def test_an_scp_style_url_does_not_bypass_the_host_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`scheme == ""` also matches an scp-style remote (`git@host:path`), which is a network
    URL, not a local path: the file-URL exemption must not let it through unchecked."""
    repo = notedb.Repo.open(tmp_path / "c.git", "git@chromium-review.googlesource.com:x", Pacer(0))
    _forbid_network_subprocess(monkeypatch)
    with pytest.raises(notedb.GitError, match="refusing to contact"):
        repo.fetch("history", ["+refs/heads/main:refs/heads/main"])


@pytest.mark.parametrize(
    "bad",
    [
        "a/../b",
        "a+b",
        "a?b",
        "a#b",
        "/etc/passwd",
        "..",
        "a/b/../../etc",
        "a%2e%2e/b",
        "platform%2fabc",
        "a%b",
    ],
)
def test_valid_project_name_refuses_path_and_url_escapes(bad: str) -> None:
    assert notedb.valid_project_name(bad) is False


def test_valid_project_name_accepts_a_plain_project_path() -> None:
    assert notedb.valid_project_name("platform/hardware/interfaces") is True


def test_repo_open_refuses_a_scratch_repository_pointed_elsewhere(tmp_path: Path) -> None:
    path = tmp_path / "c.git"
    notedb.Repo.open(path, "file:///a", Pacer(0))
    with pytest.raises(notedb.GitError, match="pointed elsewhere"):
        notedb.Repo.open(path, "file:///b", Pacer(0))


def test_a_network_git_call_disables_http_redirects(
    server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    real_run = notedb.subprocess.run

    def spy(cmd: list[str], *a: Any, **kw: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(list(cmd))
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(notedb.subprocess, "run", spy)
    repo = _repo(server, tmp_path)
    notedb.fetch_history(repo, ["refs/heads/main"], "2024-10-01")
    fetch_calls = [c for c in calls if "fetch" in c]
    assert fetch_calls, "the history fetch should have run"
    assert all("http.followRedirects=false" in " ".join(c) for c in fetch_calls)


def test_isolated_env_carries_the_suites_git_allow_protocol_through(
    server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`GIT_ALLOW_PROTOCOL=file`, the suite's second offline guard (tests/conftest.py), is
    itself a `GIT_*` variable, so `_isolated_env`'s blanket strip must not drop it: a route git
    subprocess run under the suite's own environment has to see it too."""
    assert os.environ.get("GIT_ALLOW_PROTOCOL") == "file", "conftest sets this for the session"
    repo = _repo(server, tmp_path)
    calls: list[dict[str, Any]] = []
    real_run = notedb.subprocess.run

    def spy(cmd: list[str], *a: Any, **kw: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(kw)
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(notedb.subprocess, "run", spy)
    repo.git("rev-parse", "--is-bare-repository")
    assert calls, "the local command should have run"
    assert calls[-1]["env"].get("GIT_ALLOW_PROTOCOL") == "file"


def test_the_git_pacer_floors_a_zero_or_negative_request_interval() -> None:
    """A mutated CLI passing `--request-interval 0` (or less) must not remove Android's floor."""
    now, slept = [0.0], []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    for interval in (0.0, -5.0):
        pacer = cli._git_pacer(interval)
        pacer._clock = lambda: now[0]  # noqa: SLF001 - test seam, no clock param on _git_pacer
        pacer._sleep = sleep  # noqa: SLF001
        slept.clear()
        pacer.wait("android.googlesource.com")
        pacer.wait("android.googlesource.com")
        assert slept == pytest.approx([1.0]), interval


# ---------------------------------------------------------------------------
# Lazy fetch probe (item 3)
# ---------------------------------------------------------------------------


def test_lazy_fetch_probe_passes_under_this_process_git() -> None:
    """This machine's git must actually fail the probe, or the whole route is unsafe to run."""
    notedb._require_lazy_fetch_disabled()  # must not raise


def test_lazy_fetch_probe_raises_when_the_safety_variable_is_missing() -> None:
    """Simulates an old git: without `GIT_NO_LAZY_FETCH`, the promisor remote serves the blob."""
    env = {k: v for k, v in notedb._isolated_env().items() if k != "GIT_NO_LAZY_FETCH"}
    with pytest.raises(notedb.GitError, match="lazily") as excinfo:
        notedb._require_lazy_fetch_disabled(env)
    message = str(excinfo.value)
    assert "2.36" not in message, "GIT_NO_LAZY_FETCH landed in git 2.44, not 2.36"
    assert "2.44" in message
    assert "behaviour" in message, "the probe tests behaviour, not a version number"


# ---------------------------------------------------------------------------
# SIGTERM cleanup (item 4)
# ---------------------------------------------------------------------------


def test_sigterm_during_a_git_fetch_leaves_no_scratch_repository(
    server: Server, tmp_path: Path
) -> None:
    """`fetch_month` alone, with no CLI-level wrap: the handling has to live in `fetch_month`
    itself (item 4b), so every caller gets it, not only `_stage_fetch_git`."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    script = f"""
import sys
sys.path.insert(0, {str(Path(notedb.__file__).resolve().parents[2])!r})
from pathlib import Path
from sphragis.corpus.notedb import fetch_month
from sphragis.corpus.pacing import Pacer
fetch_month(
    "aosp", [{PROJECT!r}], "2024-11", "salt",
    pacer=Pacer(5.0),
    base_url={server.url!r},
    workdir=Path({str(workdir)!r}),
)
"""
    proc = subprocess.Popen([sys.executable, "-c", script])
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not any(workdir.iterdir()):
            time.sleep(0.05)
        assert any(workdir.iterdir()), "the scratch repository never appeared to be killed mid-run"
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) != 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert list(workdir.iterdir()) == [], "the scratch repository must be gone after SIGTERM"


def test_cli_stage_fetch_git_still_gets_sigterm_protection(server: Server, tmp_path: Path) -> None:
    """The CLI path (`_stage_fetch_git`) no longer wraps `fetch_month` itself, so this proves
    the protection it used to add explicitly still reaches it through `fetch_month` alone --
    driven through `cli.main` end to end, `TMPDIR` pinned so the scratch directory is one this
    test can watch."""
    root = tmp_path / "root"
    workdir = tmp_path / "cliwork"
    workdir.mkdir()
    script = f"""
import sys
sys.path.insert(0, {str(Path(notedb.__file__).resolve().parents[2])!r})
import os
os.environ["SPHRAGIS_CORPUS_SALT"] = "salt"
from sphragis.corpus import cli
cli.GIT_HOSTS["aosp"] = {server.url!r}  # same dict object notedb.fetch_month reads
cli.main(["fetch", "--org", "aosp", "--project", {PROJECT!r}, "--month", "2024-11",
          "--root", {str(root)!r}, "--via", "git"])
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", script], env={**os.environ, "TMPDIR": str(workdir)}
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not any(workdir.iterdir()):
            time.sleep(0.05)
        assert any(workdir.iterdir()), "the scratch repository never appeared to be killed mid-run"
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) != 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert list(workdir.iterdir()) == [], "the scratch repository must be gone after SIGTERM"


def test_raise_on_sigterm_installs_no_handler_outside_the_main_thread() -> None:
    """`signal.signal` raises ValueError off the main thread; the context manager must not try."""
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            with notedb._raise_on_sigterm():
                pass
        except BaseException as error:  # noqa: BLE001 - captured across a thread boundary
            errors.append(error)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert errors == []


def test_uninterruptible_ignores_a_second_signal_during_cleanup() -> None:
    """A signal delivered while ignored is dropped, not deferred: it must not fire later either,
    and the previous handler must be back in place and live once the block exits."""
    fired: list[int] = []

    def handler(signum: int, frame: Any) -> None:
        fired.append(signum)

    previous = signal.signal(signal.SIGTERM, handler)
    try:
        with notedb._uninterruptible(signal.SIGTERM):
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.05)
        assert fired == [], "a signal sent during the ignore window must not fire the handler"
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.05)
        assert fired == [signal.SIGTERM], "the previous handler must be restored and live"
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_uninterruptible_cleanup_survives_a_second_signal_mid_rmtree(
    server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slowed `TemporaryDirectory.cleanup`, self-signalled mid-way, still finishes: the
    scratch repository is gone and `fetch_month` still returns normally."""
    import tempfile as tempfile_module

    real_cleanup = tempfile_module.TemporaryDirectory.cleanup

    def slow_cleanup(self: Any) -> None:
        os.kill(os.getpid(), signal.SIGTERM)
        os.kill(os.getpid(), signal.SIGINT)
        time.sleep(0.05)
        real_cleanup(self)

    # Warm the (`@cache`d) lazy-fetch probe first: it opens and closes its own, unrelated
    # `TemporaryDirectory` inside `Repo.open`, which the patch below must not catch instead.
    notedb._require_lazy_fetch_disabled()
    monkeypatch.setattr(tempfile_module.TemporaryDirectory, "cleanup", slow_cleanup)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    rows, _record = notedb.fetch_month(
        "aosp", [PROJECT], "2024-11", "salt", pacer=Pacer(0), base_url=server.url, workdir=scratch
    )
    assert [row["_number"] for row in rows] == [1234]
    assert list(scratch.iterdir()) == [], "cleanup must have finished despite the mid-way signals"


# ---------------------------------------------------------------------------
# Successor kind, computed from git objects (item 7)
# ---------------------------------------------------------------------------


def _kind_verified(
    tmp_path: Path, server: Server, a: str, b: str, *, filtered: bool = False
) -> tuple[str, bool]:
    """`_successor_kind(a, b)` after fetching just these two commits (with their history)."""
    repo = notedb.Repo.open(tmp_path / "kind.git", f"{server.url}/{PROJECT}", Pacer(0))
    server.ref("refs/tmp/a", a)
    server.ref("refs/tmp/b", b)
    options = ("--filter=tree:0",) if filtered else ()
    repo.fetch("test", ["+refs/tmp/a:refs/tmp/a", "+refs/tmp/b:refs/tmp/b"], *options)
    infos = notedb._commit_infos(repo, [a, b])
    return notedb._successor_kind(repo, a, b, infos[a], infos[b])


def _kind(tmp_path: Path, server: Server, a: str, b: str, *, filtered: bool = False) -> str:
    return _kind_verified(tmp_path, server, a, b, filtered=filtered)[0]


def test_successor_kind_no_change_same_tree_parents_and_message(tmp_path: Path) -> None:
    s = Server(tmp_path / "server")
    root = s.commit(s.tree({"f": b"0\n"}), "root", "2024-10-01T00:00:00Z")
    tree = s.tree({"f": b"1\n"})
    a = s.commit(tree, "same\n\nChange-Id: I1", "2024-11-01T00:00:00Z", (root,))
    b = s.commit(tree, "same\n\nChange-Id: I1", "2024-11-02T00:00:00Z", (root,))
    assert a != b
    assert _kind(tmp_path, s, a, b) == notedb.NO_CHANGE_KIND


def test_successor_kind_no_code_change_same_tree_and_parents_different_message(
    tmp_path: Path,
) -> None:
    s = Server(tmp_path / "server")
    root = s.commit(s.tree({"f": b"0\n"}), "root", "2024-10-01T00:00:00Z")
    tree = s.tree({"f": b"1\n"})
    a = s.commit(tree, "one\n\nChange-Id: I1", "2024-11-01T00:00:00Z", (root,))
    b = s.commit(tree, "two\n\nChange-Id: I1", "2024-11-02T00:00:00Z", (root,))
    assert _kind(tmp_path, s, a, b) == notedb.NO_CODE_CHANGE_KIND


def test_successor_kind_same_parent_different_tree_is_rework(tmp_path: Path) -> None:
    s = Server(tmp_path / "server")
    root = s.commit(s.tree({"f": b"0\n"}), "root", "2024-10-01T00:00:00Z")
    a = s.commit(s.tree({"f": b"1\n"}), "m\n\nChange-Id: I1", "2024-11-01T00:00:00Z", (root,))
    b = s.commit(s.tree({"f": b"2\n"}), "m\n\nChange-Id: I1", "2024-11-02T00:00:00Z", (root,))
    assert _kind(tmp_path, s, a, b) == notedb.REWORK


def test_successor_kind_trivial_rebase(tmp_path: Path) -> None:
    """Upstream changes `f`; the author's own edit only adds `g`. Replaying it lands cleanly."""
    s = Server(tmp_path / "server")
    root = s.commit(s.tree({"f": b"line1\nline2\n"}), "root", "2024-10-01T00:00:00Z")
    upstream = s.commit(
        s.tree({"f": b"line1\nCHANGED\n"}), "upstream", "2024-10-05T00:00:00Z", (root,)
    )
    a = s.commit(
        s.tree({"f": b"line1\nline2\n", "g": b"new\n"}),
        "add g\n\nChange-Id: I1",
        "2024-10-02T00:00:00Z",
        (root,),
    )
    b = s.commit(
        s.tree({"f": b"line1\nCHANGED\n", "g": b"new\n"}),
        "add g\n\nChange-Id: I1",
        "2024-10-06T00:00:00Z",
        (upstream,),
    )
    assert _kind(tmp_path, s, a, b) == notedb.TRIVIAL_REBASE_KIND


def test_successor_kind_trivial_rebase_with_message_update(tmp_path: Path) -> None:
    s = Server(tmp_path / "server")
    root = s.commit(s.tree({"f": b"line1\nline2\n"}), "root", "2024-10-01T00:00:00Z")
    upstream = s.commit(
        s.tree({"f": b"line1\nCHANGED\n"}), "upstream", "2024-10-05T00:00:00Z", (root,)
    )
    a = s.commit(
        s.tree({"f": b"line1\nline2\n", "g": b"new\n"}),
        "add g\n\nChange-Id: I1",
        "2024-10-02T00:00:00Z",
        (root,),
    )
    b = s.commit(
        s.tree({"f": b"line1\nCHANGED\n", "g": b"new\n"}),
        "add g, reword\n\nChange-Id: I1",
        "2024-10-06T00:00:00Z",
        (upstream,),
    )
    assert _kind(tmp_path, s, a, b) == notedb.TRIVIAL_REBASE_MESSAGE_KIND


def test_successor_kind_rework_across_a_rebase(tmp_path: Path) -> None:
    """The author's own further edit (`g` differs from a plain replay) makes it a rework."""
    s = Server(tmp_path / "server")
    root = s.commit(s.tree({"f": b"base\n"}), "root", "2024-10-01T00:00:00Z")
    upstream = s.commit(s.tree({"f": b"upstream\n"}), "upstream", "2024-10-05T00:00:00Z", (root,))
    a = s.commit(
        s.tree({"f": b"base\n", "g": b"new\n"}),
        "m\n\nChange-Id: I1",
        "2024-10-02T00:00:00Z",
        (root,),
    )
    b = s.commit(
        s.tree({"f": b"upstream\n", "g": b"different\n"}),
        "m\n\nChange-Id: I1",
        "2024-10-06T00:00:00Z",
        (upstream,),
    )
    assert _kind(tmp_path, s, a, b) == notedb.REWORK


def test_successor_kind_merge_commits_with_different_trees_are_rework(tmp_path: Path) -> None:
    s = Server(tmp_path / "server")
    p1 = s.commit(s.tree({"f": b"1\n"}), "p1", "2024-10-01T00:00:00Z")
    p2 = s.commit(s.tree({"g": b"2\n"}), "p2", "2024-10-01T00:00:00Z")
    a = s.commit(
        s.tree({"f": b"1\n", "g": b"2\n"}),
        "merge\n\nChange-Id: I1",
        "2024-10-02T00:00:00Z",
        (p1, p2),
    )
    b = s.commit(
        s.tree({"f": b"1\n", "g": b"3\n"}),
        "merge\n\nChange-Id: I1",
        "2024-10-03T00:00:00Z",
        (p1, p2),
    )
    assert _kind(tmp_path, s, a, b) == notedb.REWORK


def test_successor_kind_merge_commits_with_the_same_tree_are_not_rework(tmp_path: Path) -> None:
    """Documented simplification: a same-tree merge pair is told apart from REWORK by tree
    equality alone -- Gerrit's real MERGE_FIRST_PARENT_UPDATE replay is not attempted."""
    s = Server(tmp_path / "server")
    p1 = s.commit(s.tree({"f": b"1\n"}), "p1", "2024-10-01T00:00:00Z")
    p2 = s.commit(s.tree({"g": b"2\n"}), "p2", "2024-10-01T00:00:00Z")
    tree = s.tree({"f": b"1\n", "g": b"2\n"})
    a = s.commit(tree, "merge\n\nChange-Id: I1", "2024-10-02T00:00:00Z", (p1, p2))
    b = s.commit(tree, "merge\n\nChange-Id: I1", "2024-10-03T00:00:00Z", (p1, p2))
    assert _kind(tmp_path, s, a, b) == notedb.NO_CHANGE_KIND


def test_successor_kind_falls_back_to_rework_when_the_replay_cannot_be_verified(
    tmp_path: Path,
) -> None:
    """Trees not fetched (`--filter=tree:0`): `git merge-tree` cannot run, so REWORK is the
    conservative answer, the same call made for an actual conflict."""
    s = Server(tmp_path / "server")
    root = s.commit(s.tree({"f": b"base\n"}), "root", "2024-10-01T00:00:00Z")
    upstream = s.commit(s.tree({"f": b"upstream\n"}), "upstream", "2024-10-05T00:00:00Z", (root,))
    a = s.commit(
        s.tree({"f": b"base\n", "g": b"new\n"}),
        "m\n\nChange-Id: I1",
        "2024-10-02T00:00:00Z",
        (root,),
    )
    b = s.commit(
        s.tree({"f": b"upstream\n", "g": b"new\n"}),
        "m\n\nChange-Id: I1",
        "2024-10-06T00:00:00Z",
        (upstream,),
    )
    assert _kind(tmp_path, s, a, b, filtered=True) == notedb.REWORK


def test_collect_writes_kind_and_kind_source_onto_each_revision(tmp_path: Path) -> None:
    """End to end: `collect` attaches `kind`/`kind_source` the same way it attaches everything
    else, for a change with a genuine trivial rebase between two of its patch sets."""
    s = Server(tmp_path / "server")
    root = s.commit(s.tree({"f": b"line1\nline2\n"}), "root", "2024-10-01T00:00:00Z")
    upstream = s.commit(
        s.tree({"f": b"line1\nCHANGED\n"}), "upstream", "2024-10-05T00:00:00Z", (root,)
    )
    ps1 = s.commit(
        s.tree({"f": b"line1\nline2\n", "g": b"new\n"}), "Fix", "2024-10-02T00:00:00Z", (root,)
    )
    ps2 = s.commit(
        s.tree({"f": b"line1\nCHANGED\n", "g": b"new\n"}),
        "Fix",
        "2024-10-06T00:00:00Z",
        (upstream,),
    )
    s.ref("refs/changes/90/90/1", ps1)
    s.ref("refs/changes/90/90/2", ps2)
    _meta(
        s,
        90,
        [
            (
                OWNER,
                "2024-10-02T00:00:00Z",
                f"Create\n\nPatch-set: 1\nChange-id: I90\nCommit: {ps1}",
                {},
            ),
            (
                OWNER,
                "2024-10-06T00:00:00Z",
                f"Update patch set 2\n\nPatch-set: 2\nCommit: {ps2}",
                {},
            ),
        ],
    )
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))
    rows, counts = notedb.collect(repo, [90], project=PROJECT)
    revisions = rows[0]["revisions"]
    assert revisions[ps1]["kind"] == "REWORK" and revisions[ps1]["kind_source"] == "git"
    assert revisions[ps2]["kind"] == notedb.TRIVIAL_REBASE_KIND
    assert revisions[ps2]["kind_source"] == "git"
    assert counts["kind_computed"] == 2


def test_collect_fetches_a_missing_kind_parent_instead_of_crashing_the_whole_batch(
    tmp_path: Path,
) -> None:
    """item 9's reviewer trigger: patch set 1's commit is already held (as a shallow fetch --
    e.g. from an earlier, narrower operation -- would leave it) but its parent is not, and
    patch set 2's parent is a separate commit never otherwise fetched either. `kind_roots` used
    to hand both parents straight to `git log --no-walk --stdin` with no `missing()`-and-fetch
    guard (`upstream`, just above, gets one; `kind_roots` did not), so `collect` raised `fatal:
    bad object` for the whole project-month, not just this one revision."""
    s = Server(tmp_path / "server")
    base = s.commit(s.tree({"f": b"base\n"}), "base", "2024-09-01T00:00:00Z")
    other_base = s.commit(s.tree({"f": b"other\n"}), "other base", "2024-09-01T00:00:00Z")
    ps1 = s.commit(s.tree({"f": b"base\nX\n"}), "Fix", "2024-09-05T00:00:00Z", (base,))
    ps2 = s.commit(s.tree({"f": b"other\nY\n"}), "Fix", "2024-09-07T00:00:00Z", (other_base,))
    s.ref("refs/changes/96/96/1", ps1)
    s.ref("refs/changes/96/96/2", ps2)
    _meta(
        s,
        96,
        [
            (
                OWNER,
                "2024-09-05T00:00:00Z",
                f"Create\n\nPatch-set: 1\nChange-id: I96\nCommit: {ps1}",
                {},
            ),
            (
                OWNER,
                "2024-09-07T00:00:00Z",
                f"Update patch set 2\n\nPatch-set: 2\nCommit: {ps2}",
                {},
            ),
        ],
    )
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))
    # ps1 already held (shallowly, at depth 1: only the commit, not its parent), as an earlier,
    # narrower fetch would leave it -- exactly the state `every`'s own depth=2 fetch does not
    # reliably widen, since a shallow object already present is not refetched.
    repo.fetch_objects("presim", [ps1], "--depth=1", "--filter=tree:0")
    assert repo.missing([base]) == [base], "ps1's parent must start out genuinely unfetched"
    assert repo.missing([other_base]) == [other_base]

    rows, counts = notedb.collect(repo, [96], project=PROJECT)  # must not raise GitError

    revisions = rows[0]["revisions"]
    assert revisions[ps2]["kind"] in (notedb.REWORK,), "unrelated bases: a genuine rework"
    assert counts["kind_computed"] == 2
    assert counts.get("kind_unverified", 0) == 0, "both parents were fetchable, just not yet held"


def test_collect_counts_kind_unverified_when_a_parent_stays_unfetchable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parent that is still missing after the fetch attempt (genuinely gone, not merely
    unfetched yet) must not crash `collect` or silently pass as a confirmed REWORK: it is
    dropped from `kind_roots` and counted separately, `kind_unverified`, distinct from a REWORK
    git actually confirmed."""
    s = Server(tmp_path / "server")
    base = s.commit(s.tree({"f": b"base\n"}), "base", "2024-09-01T00:00:00Z")
    other_base = s.commit(s.tree({"f": b"other\n"}), "other base", "2024-09-01T00:00:00Z")
    ps1 = s.commit(s.tree({"f": b"base\nX\n"}), "Fix", "2024-09-05T00:00:00Z", (base,))
    ps2 = s.commit(s.tree({"f": b"other\nY\n"}), "Fix", "2024-09-07T00:00:00Z", (other_base,))
    s.ref("refs/changes/97/97/1", ps1)
    s.ref("refs/changes/97/97/2", ps2)
    _meta(
        s,
        97,
        [
            (
                OWNER,
                "2024-09-05T00:00:00Z",
                f"Create\n\nPatch-set: 1\nChange-id: I97\nCommit: {ps1}",
                {},
            ),
            (
                OWNER,
                "2024-09-07T00:00:00Z",
                f"Update patch set 2\n\nPatch-set: 2\nCommit: {ps2}",
                {},
            ),
        ],
    )
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))
    real_missing = repo.missing

    def lying_missing(oids: Any) -> list[str]:
        # `base` never becomes available, however many times a fetch is attempted for it.
        return sorted({*real_missing(oids), base} & set(oids))

    monkeypatch.setattr(repo, "missing", lying_missing)

    rows, counts = notedb.collect(repo, [97], project=PROJECT)  # must not raise GitError

    revisions = rows[0]["revisions"]
    assert revisions[ps2]["kind"] == notedb.REWORK, "still the conservative fallback"
    assert counts["kind_unverified"] == 1
    assert counts["kind_computed"] == 2


def test_collect_confirms_trivial_rebase_when_both_sides_touch_the_same_file(
    tmp_path: Path,
) -> None:
    """item 8's reviewer case: `f` has 20 lines, upstream edits line 2, the change edits line
    18, and patch set 2 is patch set 1 rebased cleanly onto that upstream commit -- through
    `collect` end to end, not only `_successor_kind` with every object already fetched (the
    existing unit tests above never touch the same file on both sides, so `git merge-tree`
    never needed blob content and the ordering bug went unnoticed there). A comment sits on
    line 2, the line only the rebase touched.
    """
    lines = [f"l{i}" for i in range(1, 21)]

    def rendered(edited: dict[int, str]) -> bytes:
        text = list(lines)
        for line_no, value in edited.items():
            text[line_no - 1] = value
        return ("\n".join(text) + "\n").encode()

    s = Server(tmp_path / "server")
    root = s.commit(s.tree({"f": rendered({})}), "root", "2024-10-01T00:00:00Z")
    upstream = s.commit(
        s.tree({"f": rendered({2: "UPSTREAM"})}), "upstream", "2024-10-05T00:00:00Z", (root,)
    )
    ps1 = s.commit(s.tree({"f": rendered({18: "OWN"})}), "Fix", "2024-10-02T00:00:00Z", (root,))
    ps2 = s.commit(
        s.tree({"f": rendered({2: "UPSTREAM", 18: "OWN"})}),
        "Fix",
        "2024-10-06T00:00:00Z",
        (upstream,),
    )
    s.ref("refs/changes/95/95/1", ps1)
    s.ref("refs/changes/95/95/2", ps2)
    comment = {
        **_comment("u", 1, 2, REVIEWER, "2024-10-03T00:00:00Z", "upstream line", ps1),
        "key": {"uuid": "u", "filename": "f", "patchSetId": 1},
    }
    note = json.dumps({"comments": [comment]}).encode()
    _meta(
        s,
        95,
        [
            (
                OWNER,
                "2024-10-02T00:00:00Z",
                _message(
                    "Create change",
                    "Uploaded.",
                    "Patch-set: 1",
                    "Change-id: I95",
                    "Branch: refs/heads/main",
                    f"Commit: {ps1}",
                ),
                {},
            ),
            (
                REVIEWER,
                "2024-10-03T00:00:00Z",
                _message("Update patch set 1", "(1 comment)", "Patch-set: 1"),
                {ps1: note},
            ),
            (
                OWNER,
                "2024-10-06T00:00:00Z",
                _message("Update patch set 2", "Uploaded.", "Patch-set: 2", f"Commit: {ps2}"),
                {ps1: note},
            ),
        ],
    )
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))
    rows, counts = notedb.collect(repo, [95], project=PROJECT)
    revisions = rows[0]["revisions"]
    assert revisions[ps2]["kind"] == notedb.TRIVIAL_REBASE_KIND, (
        "both sides touched f (different lines): must still resolve via a real content merge, "
        "not fall back to REWORK for lack of blob content"
    )
    assert counts["kind_computed"] == 2
    assert counts.get("kind_unverified", 0) == 0, "every object needed was fetched in time"
    block = rows[0][notedb.NOTEDB_KEY]["diffs"]["1:f"]["content"]
    assert any(b.get("due_to_rebase") for b in block if "ab" not in b)


# ---------------------------------------------------------------------------
# service_user gap and the bot-template rule on NoteDb rows (item 8)
# ---------------------------------------------------------------------------


def test_author_tags_is_a_recorded_gap() -> None:
    assert "author.tags" in notedb.GAPS


def test_is_service_user_never_matches_a_notedb_comment_author() -> None:
    from sphragis.corpus.build import is_service_user

    comment = {"key": {"uuid": "u", "filename": "f", "patchSetId": 1}, "author": {"id": 1}}
    _, info = notedb.rest_comment(comment)
    assert is_service_user(info.get("author")) is False


def test_refine_drops_a_bot_comment_on_a_notedb_row_the_same_as_rest(tmp_path: Path) -> None:
    from sphragis.corpus.refine import index_changes, refine

    s = Server(tmp_path / "server")
    ps1 = s.commit(s.tree({"src/a.py": b"1\n"}), "one", "2024-11-01T00:00:00Z")
    ps2 = s.commit(s.tree({"src/a.py": b"2\n"}), "two", "2024-11-02T00:00:00Z", (ps1,))
    s.ref("refs/changes/91/91/1", ps1)
    s.ref("refs/changes/91/91/2", ps2)
    note = json.dumps(
        {
            "comments": [
                _comment(
                    "b1",
                    1,
                    1,
                    REVIEWER,
                    "2024-11-01T09:00:00Z",
                    "Modifying security sensitive file.",
                    ps1,
                )
            ]
        }
    ).encode()
    _meta(
        s,
        91,
        [
            (
                OWNER,
                "2024-11-01T00:00:00Z",
                f"Create\n\nPatch-set: 1\nChange-id: I91\nCommit: {ps1}",
                {},
            ),
            (
                REVIEWER,
                "2024-11-01T09:00:00Z",
                "Update patch set 1\n\nPatch-set: 1",
                {ps1: note},
            ),
            (
                OWNER,
                "2024-11-02T00:00:00Z",
                f"Update patch set 2\n\nPatch-set: 2\nCommit: {ps2}",
                {ps1: note},
            ),
        ],
    )
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))
    rows, _ = notedb.collect(repo, [91], project=PROJECT)
    row = rows[0]
    examples, _ = build_from_change("aosp", row, *notedb.embedded_fetchers(row))
    assert len(examples) == 1, "the comment anchors to a real hunk and reaches build"
    kept, counts = refine(examples, index_changes([]))
    assert kept == [], "the bot-template rule drops it, exactly as it would a REST row's"
    assert counts["automated_only"] == 1


# ---------------------------------------------------------------------------
# Note parsing robustness (item 11)
# ---------------------------------------------------------------------------


def test_the_legacy_written_on_format_parses(tmp_path: Path) -> None:
    assert notedb._note_timestamp("Dec 18, 2024 10:50:22 PM") == "2024-12-18 22:50:22.000000000"
    assert notedb._note_timestamp("Jan 1, 2025 12:00:00 AM") == "2025-01-01 00:00:00.000000000"


def test_an_unparseable_written_on_is_none_not_a_crash() -> None:
    assert notedb._note_timestamp("not a timestamp at all") is None
    assert notedb._note_timestamp("") is None
    assert notedb._note_timestamp(None) is None


def test_a_corrupt_note_blob_is_counted_not_silently_skipped(tmp_path: Path) -> None:
    s = Server(tmp_path / "server")
    ps1 = s.commit(s.tree({"src/a.py": b"1\n"}), "one", "2024-11-01T00:00:00Z")
    ps2 = s.commit(s.tree({"src/a.py": b"2\n"}), "two", "2024-11-02T00:00:00Z", (ps1,))
    s.ref("refs/changes/92/92/1", ps1)
    s.ref("refs/changes/92/92/2", ps2)
    good = json.dumps(
        {"comments": [_comment("g1", 1, 1, REVIEWER, "2024-11-01T09:00:00Z", "real", ps1)]}
    ).encode()
    _meta(
        s,
        92,
        [
            (
                OWNER,
                "2024-11-01T00:00:00Z",
                f"Create\n\nPatch-set: 1\nChange-id: I92\nCommit: {ps1}",
                {ps1: b"{not valid json"},
            ),
            (
                REVIEWER,
                "2024-11-01T09:00:00Z",
                "Update patch set 1\n\nPatch-set: 1",
                {ps1: b"{not valid json", ps2: good},
            ),
            (
                OWNER,
                "2024-11-02T00:00:00Z",
                f"Update patch set 2\n\nPatch-set: 2\nCommit: {ps2}",
                {ps1: b"{not valid json", ps2: good},
            ),
        ],
    )
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))
    repo.fetch_refs("meta", [notedb.change_ref(92, "meta")])
    record = notedb.read_change(repo, 92)
    assert record.note_parse_errors == 1, "the corrupt blob is counted"
    assert len(record.comments) == 1, "the valid note's comment still reads"
    rows, counts = notedb.collect(repo, [92], project=PROJECT)
    assert counts["note_parse_errors"] == 1


def test_a_comment_with_an_unparseable_timestamp_is_dropped_not_fatal(tmp_path: Path) -> None:
    s = Server(tmp_path / "server")
    ps1 = s.commit(s.tree({"src/a.py": b"1\n"}), "one", "2024-11-01T00:00:00Z")
    ps2 = s.commit(s.tree({"src/a.py": b"2\n"}), "two", "2024-11-02T00:00:00Z", (ps1,))
    s.ref("refs/changes/93/93/1", ps1)
    s.ref("refs/changes/93/93/2", ps2)
    bad = {
        **_comment("b1", 1, 1, REVIEWER, "garbage-not-a-date", "bad", ps1),
    }
    note = json.dumps({"comments": [bad]}).encode()
    _meta(
        s,
        93,
        [
            (
                OWNER,
                "2024-11-01T00:00:00Z",
                f"Create\n\nPatch-set: 1\nChange-id: I93\nCommit: {ps1}",
                {},
            ),
            (
                REVIEWER,
                "2024-11-01T09:00:00Z",
                "Update patch set 1\n\nPatch-set: 1",
                {ps1: note},
            ),
            (
                OWNER,
                "2024-11-02T00:00:00Z",
                f"Update patch set 2\n\nPatch-set: 2\nCommit: {ps2}",
                {ps1: note},
            ),
        ],
    )
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))
    repo.fetch_refs("meta", [notedb.change_ref(93, "meta")])
    record = notedb.read_change(repo, 93)
    assert record.comments == [], "the unparseable-timestamp comment is dropped"
    assert record.comment_timestamp_errors == 1
    rows, counts = notedb.collect(repo, [93], project=PROJECT)
    assert counts["comment_timestamp_errors"] == 1
    assert rows[0]["_number"] == 93, "the change itself is not aborted"


# ---------------------------------------------------------------------------
# Guard-mutant kill tests (item 6)
# ---------------------------------------------------------------------------


def test_a_filtered_fetch_does_not_leak_its_filter_onto_a_later_one(
    server: Server, tmp_path: Path
) -> None:
    """M8: a `--filter=tree:0` fetch (`fetch_history`'s own) must not linger as the remote's
    default filter, or the next fetch -- naming none of its own -- inherits it. Here that next
    fetch is the meta+notes fetch a real `collect` always makes; a leaked `tree:0` would strip
    the notes tree, and a leaked `blob:none` would leave it present but content-less."""
    repo = _repo(server, tmp_path)
    notedb.fetch_history(repo, ["refs/heads/main"], "2024-10-01")
    assert repo.fetch_refs("meta", [notedb.change_ref(1234, "meta")]) == []
    tip = repo.git("rev-parse", notedb.change_ref(1234, "meta")).decode().strip()
    blobs = notedb._note_blobs(repo, tip)
    assert blobs, "the notes tree must be present, not stripped by a leaked tree:0"
    contents = repo.read_objects(blobs)
    assert any(b"comments" in raw for raw in contents.values()), (
        "note content must be present, not stripped by a leaked blob:none"
    )


def test_a_bad_project_name_is_refused_before_fetch_month_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M13: `_stage_fetch_git`'s own project-name validation, not just `valid_project_name`
    itself: a mutant that removed the CLI's `bad = [...]; raise SystemExit(...)` guard would
    still pass every `valid_project_name` unit test while letting an unsafe --project through
    to a real fetch."""
    from sphragis.corpus import cli

    called = []
    monkeypatch.setattr(cli, "fetch_month", lambda *a, **kw: called.append(1) or ([], {}))
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    with pytest.raises(SystemExit, match="not a plain project path"):
        cli.main(
            [
                "fetch",
                "--via",
                "git",
                "--org",
                "aosp",
                "--month",
                "2025-10",
                "--root",
                str(tmp_path),
                "--project",
                "a/../b",
            ]
        )
    assert called == [], "fetch_month must never run for a refused project name"


def test_the_git_pacer_is_actually_what_the_cli_fetch_stage_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M7: a mutant that has `_stage_fetch_git` build a bare `Pacer(0)` (or otherwise bypass
    `_git_pacer`) would still pass `test_the_git_pacer_floors_...` (which only unit-tests
    `_git_pacer` in isolation) while removing every host floor from a real fetch."""
    from sphragis.corpus import cli

    captured: dict[str, Any] = {}

    def fake_fetch_month(org, projects, month, salt, *, pacer, branches):
        captured["pacer"] = pacer
        return [], {"http_requests": 0, "git_operations": 0}

    monkeypatch.setattr(cli, "fetch_month", fake_fetch_month)
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    cli.main(
        [
            "fetch",
            "--via",
            "git",
            "--org",
            "aosp",
            "--month",
            "2025-10",
            "--root",
            str(tmp_path),
            "--project",
            "p",
            "--request-interval",
            "0",
        ]
    )
    pacer = captured["pacer"]
    assert isinstance(pacer, Pacer)
    assert pacer.interval("android.googlesource.com") >= 1.0, (
        "the CLI's own Pacer must still carry android's GIT_PERMITTED floor"
    )


def test_repo_open_actually_calls_the_lazy_fetch_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M16: a mutant that dropped `_require_lazy_fetch_disabled()` from `Repo.open` would still
    pass `test_lazy_fetch_probe_passes_under_this_process_git` (which calls the probe function
    directly, not through `Repo.open`), while every route call would run on an unchecked git."""
    calls = []
    monkeypatch.setattr(notedb, "_require_lazy_fetch_disabled", lambda *a, **kw: calls.append(1))
    notedb.Repo.open(tmp_path / "c.git", "file:///wherever", Pacer(0))
    assert calls == [1]


def test_network_charges_the_pacer_for_extra_http_requests(
    server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M3: `_network` must actually call `pacer.charge` with the requests a fetch made beyond
    the first -- `test_pacing.py` only proves `Pacer.charge` itself works, not that `_network`
    calls it; a mutant that dropped that call would still pass every local-fetch test, since a
    `file://` transport never produces a real curl trace to charge for on its own."""
    now, slept = [0.0], []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    pacer = Pacer(1.0, clock=lambda: now[0], sleep=sleep)
    repo = _repo(server, tmp_path, pacer)
    monkeypatch.setattr(
        notedb,
        "_CURL_REQUEST",
        type("R", (), {"findall": staticmethod(lambda b: [b"1", b"2", b"3"])})(),
    )
    notedb.fetch_history(repo, ["refs/heads/main"], "2024-10-01")
    pacer.wait("local")
    assert slept == pytest.approx([3.0]), (
        "a fetch counted as 3 HTTP requests must charge 2 extra, not just the base interval"
    )


# ---------------------------------------------------------------------------
# The sealed test window's tip probe: forced, and pinned by commit id (item 1)
# ---------------------------------------------------------------------------


def test_a_rerun_over_a_kept_repo_drops_a_change_updated_since_the_first_run(
    tmp_path: Path,
) -> None:
    """NB1: an unforced probe on a second `collect` over the same scratch repository read back
    the ref it already held from the first run (`fetch_refs` skips a ref already held unless
    `force`), never learning the change had a window-dated comment added upstream since."""
    s = Server(tmp_path / "server")
    ps1 = s.commit(s.tree({"f": b"1\n"}), "one", "2025-10-01T00:00:00Z")
    steps = [
        (
            OWNER,
            "2025-10-01T00:00:00Z",
            f"Create\n\nPatch-set: 1\nChange-id: I99\nCommit: {ps1}",
            {},
        ),
    ]
    _meta(s, 99, steps)
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))

    rows, counts = notedb.collect(repo, [99], project=PROJECT)
    assert [r["_number"] for r in rows] == [99]
    assert counts["touches_test_window"] == 0

    # Upstream: a comment lands on the change, dated inside the sealed window.
    late_note = json.dumps(
        {"comments": [_comment("late", 1, 1, REVIEWER, "2025-11-03T00:00:00Z", "late", ps1)]}
    ).encode()
    steps.append(
        (
            REVIEWER,
            "2025-11-03T00:00:00Z",
            "Update patch set 1\n\nPatch-set: 1",
            {ps1: late_note},
        )
    )
    _meta(s, 99, steps)

    rows2, counts2 = notedb.collect(repo, [99], project=PROJECT)
    assert rows2 == [], "the rerun must re-probe upstream and drop the now-sealed change"
    assert counts2["touches_test_window"] == 1


def test_a_ref_advanced_between_probe_and_full_fetch_persists_nothing_window_dated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NB2: the race. The probe reads a safe (pre-window) tip; before the full meta chain is
    fetched, the ref advances upstream to a window-dated commit. Fetching the full chain BY
    THE PROBED COMMIT ID (not the ref name) must land on the same, still-safe commit the probe
    checked -- a ref-named fetch would silently follow the ref to the new, sealed tip instead."""
    s = Server(tmp_path / "server")
    ps1 = s.commit(s.tree({"f": b"1\n"}), "one", "2025-10-01T00:00:00Z")
    steps = [
        (
            OWNER,
            "2025-10-01T00:00:00Z",
            f"Create\n\nPatch-set: 1\nChange-id: I100\nCommit: {ps1}",
            {},
        ),
    ]
    _meta(s, 100, steps)
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))

    real_meta_tips = notedb._meta_tips

    def advance_then_probe(repo_, numbers):
        result = real_meta_tips(repo_, numbers)
        late_note = json.dumps(
            {"comments": [_comment("late", 1, 1, REVIEWER, "2025-11-03T00:00:00Z", "late", ps1)]}
        ).encode()
        steps.append(
            (
                REVIEWER,
                "2025-11-03T00:00:00Z",
                "Update patch set 1\n\nPatch-set: 1",
                {ps1: late_note},
            )
        )
        _meta(s, 100, steps)  # the server's ref moves after the probe, before the full fetch
        return result

    monkeypatch.setattr(notedb, "_meta_tips", advance_then_probe)
    rows, counts = notedb.collect(repo, [100], project=PROJECT)
    assert [r["_number"] for r in rows] == [100], "the probed (pre-window) commit is what lands"
    assert counts["touches_test_window"] == 0
    for row in rows:
        assert "late" not in json.dumps(row), "nothing window-dated reaches a persisted row"


def test_a_read_record_disagreeing_with_the_probed_id_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The belt-and-braces check after `read_change`: even if some other path let a record
    through whose own `meta` differs from what was probed (or whose `updated` time reaches the
    window despite that), it is dropped and counted -- not merely trusted because the earlier
    probe-based gate happened to pass."""
    s = Server(tmp_path / "server")
    ps1 = s.commit(s.tree({"f": b"1\n"}), "one", "2025-10-01T00:00:00Z")
    _meta(
        s,
        101,
        [
            (
                OWNER,
                "2025-10-01T00:00:00Z",
                f"Create\n\nPatch-set: 1\nChange-id: I101\nCommit: {ps1}",
                {},
            ),
        ],
    )
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))
    real_read_change = notedb.read_change

    def wrong_meta(repo_, number):
        record = real_read_change(repo_, number)
        record.meta = "0" * 40  # disagrees with what was just probed and fetched
        return record

    monkeypatch.setattr(notedb, "read_change", wrong_meta)
    rows, counts = notedb.collect(repo, [101], project=PROJECT)
    assert rows == []
    assert counts["touches_test_window"] == 1


def test_a_read_record_updated_at_the_window_is_dropped_even_if_the_meta_id_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = Server(tmp_path / "server")
    ps1 = s.commit(s.tree({"f": b"1\n"}), "one", "2025-10-01T00:00:00Z")
    _meta(
        s,
        102,
        [
            (
                OWNER,
                "2025-10-01T00:00:00Z",
                f"Create\n\nPatch-set: 1\nChange-id: I102\nCommit: {ps1}",
                {},
            ),
        ],
    )
    repo = notedb.Repo.open(tmp_path / "c.git", f"{s.url}/{PROJECT}", Pacer(0))
    real_read_change = notedb.read_change

    def late_updated(repo_, number):
        record = real_read_change(repo_, number)
        record.updated = int(
            __import__("datetime").datetime.fromisoformat("2025-11-05T00:00:00+00:00").timestamp()
        )
        return record

    monkeypatch.setattr(notedb, "read_change", late_updated)
    rows, counts = notedb.collect(repo, [102], project=PROJECT)
    assert rows == []
    assert counts["touches_test_window"] == 1
