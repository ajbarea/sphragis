"""Relation chains: links, components and parents read from a clone."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from sphragis.corpus.chains import Merged, chains, links, merged, parents


def _m(number: int, commit: str, uploaded: str, submitted: str, **kw: str) -> Merged:
    """A merged change whose last authored patch set is its merged commit unless `authored`."""
    authored = kw.get("authored", commit)
    return Merged(
        number,
        kw.get("project", "p"),
        kw.get("change_id", f"I{number}"),
        commit,
        authored,
        uploaded,
        submitted,
        frozenset({commit, authored, *kw.get("others", "").split()}),
    )


def test_a_parent_still_in_review_at_the_childs_upload_is_a_link() -> None:
    parent = _m(1, "a", "2025-01-01", "2025-01-05")
    child = _m(2, "b", "2025-01-03", "2025-01-06")  # uploaded on a, before a merged
    assert links([parent, child], {"b": ["a"]}) == [(2, 1)]


def test_a_parent_already_merged_is_the_branch_tip_not_a_link() -> None:
    parent = _m(1, "a", "2025-01-01", "2025-01-02")
    child = _m(2, "b", "2025-01-03", "2025-01-04")
    assert links([parent, child], {"b": ["a"]}) == []


def test_another_project_or_an_unknown_parent_is_not_a_link() -> None:
    parent = _m(1, "a", "2025-01-01", "2025-01-05", project="q")
    child = _m(2, "b", "2025-01-03", "2025-01-06")
    assert links([parent, child], {"b": ["a", "zzz"]}) == []


def test_chains_are_components_named_by_their_smallest_change() -> None:
    changes = [_m(n, f"c{n}", "t", "t") for n in (1, 2, 3, 4, 5)]
    assert chains(changes, [(3, 2), (2, 1), (5, 4)]) == {1: 1, 2: 1, 3: 1, 4: 4, 5: 4}


def test_a_shared_change_id_joins_a_chain_as_the_bootstrap_already_does() -> None:
    changes = [
        _m(1, "a", "t", "t", change_id="Ix"),
        _m(7, "b", "t", "t", change_id="Ix"),  # a cherry-pick on another branch
        _m(9, "c", "t", "t"),
    ]
    assert chains(changes, []) == {1: 1, 7: 1, 9: 9}


def test_merged_takes_the_last_authored_patch_set_and_skips_the_rest() -> None:
    rows = [
        {
            "_number": 5,
            "project": "p",
            "change_id": "I5",
            "status": "MERGED",
            "current_revision": "s6",
            "submitted": "2025-02-03",
            "revisions": {
                "s4": {"_number": 1, "kind": "REWORK", "created": "2025-01-01"},
                "s5": {"_number": 2, "kind": "REWORK", "created": "2025-02-01"},
                "s6": {"_number": 3, "kind": "TRIVIAL_REBASE", "created": "2025-02-02"},
            },
        },
        {
            "_number": 8,
            "project": "p",
            "change_id": "I8",
            "status": "MERGED",
            "current_revision": "t2",
            "submitted": "2025-03-03",
            "revisions": {
                "t1": {"_number": 1, "kind": "NO_CODE_CHANGE", "created": "2025-03-01"},
                "t2": {"_number": 2, "kind": "NO_CHANGE", "created": "2025-03-02"},
            },
        },
        {"_number": 6, "status": "ABANDONED", "current_revision": "s6", "revisions": {}},
        {"_number": 7, "status": "MERGED", "current_revision": "s7", "revisions": {}},
    ]
    first, second = merged(rows)
    assert (first.commit, first.authored, first.uploaded) == ("s6", "s5", "2025-02-01")
    assert first.patch_sets == frozenset({"s4", "s5", "s6"})
    # No authored patch set at all: the first stands for the author's.
    assert (second.authored, second.uploaded) == ("t1", "2025-03-01")


def test_a_rebased_change_links_through_its_authored_patch_set_to_any_patch_set() -> None:
    """Qt's case: the last patch set is a rebase onto the tip; the authored one sat on B's
    second patch set, which B later replaced before it merged."""
    parent = _m(1, "b-final", "2025-01-01", "2025-01-09", others="b-ps2")
    child = _m(2, "a-final", "2025-01-03", "2025-01-10", authored="a-ps1")
    assert links([parent, child], {"a-final": ["b-final"], "a-ps1": ["b-ps2"]}) == [(2, 1)]


def test_parents_are_read_from_the_clone_and_a_missing_commit_left_out(tmp_path: Path) -> None:
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.org",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.org",
    }

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], env=env, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "a")
    a = git("rev-parse", "HEAD")
    git("commit", "-q", "--allow-empty", "-m", "b")
    b = git("rev-parse", "HEAD")
    missing = "0" * 40
    assert parents(tmp_path, [b, a, missing]) == {b: [a], a: []}
