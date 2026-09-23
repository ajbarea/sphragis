"""The NoteDb route, read over `file://` from a server repository built here in NoteDb's shape."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from sphragis.corpus import notedb
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
    notedb.fetch_history(repo, "main", "2024-10-01")
    found, counts = notedb.merged_commits(
        repo, "main", "2024-11-01", "2024-12-01", review_host="review.example", project="proj"
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


def test_a_vanished_meta_ref_is_counted_not_fatal(server: Server, tmp_path: Path) -> None:
    repo = _repo(server, tmp_path)
    rows, counts = notedb.collect(repo, [1234, 5555], project=PROJECT)
    assert [r["_number"] for r in rows] == [1234]
    assert counts["meta_missing"] == 1


def test_a_missing_object_fails_rather_than_fetching_lazily(server: Server, tmp_path: Path) -> None:
    repo = _repo(server, tmp_path)
    notedb.fetch_history(repo, "main", "2024-10-01")
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
    notedb.fetch_history(repo, "main", "2024-10-01")
    repo.fetch_refs("meta", [notedb.change_ref(1234, "meta")])
    assert slept == pytest.approx([1.5])
    assert [entry["purpose"] for entry in repo.log] == ["history", "meta"]


def test_month_bounds_roll_over_the_year() -> None:
    assert notedb.month_bounds("2024-12") == ("2024-12-01", "2025-01-01")


def test_candidates_reach_back_further_for_a_host_that_merges_uploads_unchanged() -> None:
    assert notedb.candidates_since("aosp", "2024-11-01") == "2024-08-03"
    assert notedb.candidates_since("chromium", "2024-11-01") == "2024-10-18"


def test_a_rebased_step_records_parents_and_drops_the_upstream_hunk(tmp_path: Path) -> None:
    """Patch set 2 sits on a newer base that changed line 2; the author fixed line 4.

    The reviewer commented on both lines. Only the author's fix answers a comment; the other
    hunk is the rebase, and the NoteDb build drops it as `rebase_edit`.
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

    fetchers = notedb.embedded_fetchers(row)
    kept, drops = build_from_change("aosp", row, *fetchers, drop_rebase_edits=True)
    assert [e["comments"] for e in kept] == [["spaces"]]
    assert drops["rebase_edit"] == 1
    everything, legacy = build_from_change("aosp", row, *fetchers)
    assert len(everything) == 2 and "rebase_edit" not in legacy, "the REST build is unchanged"


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
    notedb.fetch_history(repo, "main", "2024-09-01T12:00:00")
    walk = repo.git("rev-list", "refs/heads/main").decode().split()
    assert side_1 in walk and base not in walk, "a shallow history, as the route always has"
    repo.fetch_objects("patch_sets", [patch_set], "--depth=2", "--filter=tree:0")
    assert side_1 in repo.git("rev-list", "refs/heads/main").decode().split()
