"""scripts/notedb_parity.py's own wiring: the parts round 2 review found broken (items 4c, 12-14).

Loaded via `importlib`, the pattern `test_fdlora_script.py` uses for a script outside the
`sphragis` package, so it runs the real file rather than a reimplementation of it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "notedb_parity.py"


def _load() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("_under_test_notedb_parity", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def parity() -> types.ModuleType:
    return _load()


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "t@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    (root / "README").write_text("x\n")
    subprocess.run(["git", "-C", str(root), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "init"], check=True)


# ---------------------------------------------------------------------------
# item 14: rest_root relative to its own checkout, not this process's cwd
# ---------------------------------------------------------------------------


def test_relative_to_repo_uses_the_checkouts_own_toplevel_not_this_processs_cwd(
    parity: types.ModuleType, tmp_path: Path
) -> None:
    """A separate repository holds `rest_root` nested inside it; pytest's own cwd is a
    different repository entirely (this one). The old code ran a bare `git rev-parse
    --show-toplevel`, which reads *this* process's cwd -- the wrong checkout -- so it must
    resolve against `rest_root` itself, `-C`, not the ambient working directory."""
    other_repo = tmp_path / "other-repo"
    other_repo.mkdir()
    _init_repo(other_repo)
    rest_root = other_repo / "datasets" / "gerrit" / "aosp"
    rest_root.mkdir(parents=True)
    result = parity._relative_to_repo(rest_root)
    assert result == "datasets/gerrit/aosp"
    assert not Path(result).is_absolute()


def test_relative_to_repo_resolves_a_worktree_to_its_own_toplevel(
    parity: types.ModuleType, tmp_path: Path
) -> None:
    """A worktree's top-level is itself, not the main checkout's: `-C` must land there too."""
    main_repo = tmp_path / "main-repo"
    main_repo.mkdir()
    _init_repo(main_repo)
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "-C", str(main_repo), "worktree", "add", "-q", "-b", "wt", str(worktree)],
        check=True,
    )
    rest_root = worktree / "datasets" / "gerrit" / "aosp"
    rest_root.mkdir(parents=True)
    assert parity._relative_to_repo(rest_root) == "datasets/gerrit/aosp"


def test_relative_to_repo_falls_back_to_the_resolved_path_outside_any_checkout(
    parity: types.ModuleType, tmp_path: Path
) -> None:
    outside = tmp_path / "no-repo-here"
    outside.mkdir()
    assert parity._relative_to_repo(outside) == str(outside.resolve())


# ---------------------------------------------------------------------------
# item 12: classify_enumeration under the branches actually read, no hardcoded default
# ---------------------------------------------------------------------------


def _rest_only_row(number: int, branch: str) -> dict[str, Any]:
    return {
        "_number": number,
        "submitted": "2024-11-10 00:00:00.000000000",
        "branch": branch,
        "revisions": {"c" * 40: {"created": "2024-11-05 00:00:00.000000000"}},
    }


def test_classify_enumeration_labels_a_branch_outside_what_was_read(
    parity: types.ModuleType,
) -> None:
    """Restricted to `release`: a REST-only change on `main` is unreached, not `main`-specific."""
    rest = {"2024-11": [_rest_only_row(501, "main")]}
    result = parity.classify_enumeration("2024-11", {}, rest, {}, ["refs/heads/release"])
    assert result["rest_only"] == {"branch_not_read": 1}
    assert result["rest_only_changes"]["branch_not_read"] == [501]


def test_classify_enumeration_does_not_mislabel_a_change_on_a_branch_that_was_read(
    parity: types.ModuleType,
) -> None:
    """The old hardcoded check (`branch != BRANCH_DEFAULT`) would call this
    `other_branch_never_reached_main` even though `release` -- the change's own branch -- was
    exactly what this run read; the label must track what was actually asked for."""
    rest = {"2024-11": [_rest_only_row(502, "release")]}
    result = parity.classify_enumeration("2024-11", {}, rest, {}, ["refs/heads/release"])
    assert "branch_not_read" not in result["rest_only"]
    assert "other_branch_never_reached_main" not in result["rest_only"]
    assert result["rest_only"] == {"main_commit_not_in_history": 1}


# ---------------------------------------------------------------------------
# item 11B: collect() is called with a submitted_between span, not unbounded
# ---------------------------------------------------------------------------


def test_collect_span_covers_every_compared_month(parity: types.ModuleType) -> None:
    bounds = {m: parity.month_bounds(m) for m in parity.MONTHS}
    assert parity.collect_span(bounds, parity.MONTHS) == ("2024-11-01", "2025-02-01")


def test_collect_span_is_the_min_start_and_max_end_whatever_the_month_order(
    parity: types.ModuleType,
) -> None:
    bounds = {
        "2024-11": ("2024-11-01", "2024-12-01"),
        "2025-01": ("2025-01-01", "2025-02-01"),
        "2025-06": ("2025-06-01", "2025-07-01"),
    }
    assert parity.collect_span(bounds, ["2025-06", "2024-11", "2025-01"]) == (
        "2024-11-01",
        "2025-07-01",
    )


# ---------------------------------------------------------------------------
# item 13: the history-since marker is keyed on the branch set, not the date alone
# ---------------------------------------------------------------------------


def test_history_fetched_is_none_for_a_branch_never_fetched_before(
    parity: types.ModuleType, tmp_path: Path
) -> None:
    """A rerun asking for a new branch, at the same `since` already held, must still trigger a
    fetch: the old marker (a bare date string) could not tell "new branch" from "nothing new"."""
    marker = tmp_path / "history-since.txt"
    parity._record_history_fetch(marker, ["refs/heads/main"], "2024-08-01")
    assert parity._history_fetched(marker, ["refs/heads/main"], "2024-08-01") is not None
    assert parity._history_fetched(marker, ["refs/heads/release"], "2024-08-01") is None


def test_history_fetched_is_none_when_since_deepens(
    parity: types.ModuleType, tmp_path: Path
) -> None:
    marker = tmp_path / "history-since.txt"
    parity._record_history_fetch(marker, ["refs/heads/main"], "2024-08-01")
    assert parity._history_fetched(marker, ["refs/heads/main"], "2024-07-01") is None
    assert parity._history_fetched(marker, ["refs/heads/main"], "2024-09-01") is not None


def test_history_fetched_is_none_for_a_legacy_bare_date_marker(
    parity: types.ModuleType, tmp_path: Path
) -> None:
    """A marker from before this fix (a bare date, not JSON) must not be trusted as covering
    any branch: unreadable or unrecognised, so a fetch happens rather than being skipped."""
    marker = tmp_path / "history-since.txt"
    marker.write_text("2024-08-01\n")
    assert parity._history_fetched(marker, ["refs/heads/main"], "2024-08-01") is None


def test_rerunning_with_an_extra_branch_does_not_crash_on_an_unfetched_ref(
    parity: types.ModuleType, tmp_path: Path
) -> None:
    """End to end, against a real repository: the exact sequence `_run` executes (marker
    decision, `fetch_history`, then a `git log` over the branches read). The old marker (a bare
    date, unchanged between runs) skipped the second `fetch_history` entirely, so `release`
    was never fetched and the `git log` below failed with `unknown revision 'refs/heads/release'`.
    """
    from sphragis.corpus.notedb import Pacer, Repo, fetch_history

    server = tmp_path / "server.git"
    subprocess.run(["git", "init", "-q", "--bare", str(server)], check=True)
    subprocess.run(
        ["git", "--git-dir", str(server), "config", "uploadpack.allowFilter", "true"], check=True
    )
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x", "GIT_AUTHOR_DATE": "2024-09-01T00:00:00Z",
           "GIT_COMMITTER_DATE": "2024-09-01T00:00:00Z"}  # fmt: skip

    def run_git(*args: str) -> str:
        return subprocess.run(
            ["git", "--git-dir", str(server), *args],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, **env},
        ).stdout.strip()

    empty_tree = run_git("hash-object", "-t", "tree", "--stdin", "-w")
    commit = run_git("commit-tree", empty_tree, "-m", "one")
    run_git("update-ref", "refs/heads/main", commit)
    run_git("update-ref", "refs/heads/release", commit)

    work = tmp_path / "work"
    work.mkdir()
    repo = Repo.open(work / "client.git", f"file://{server}", Pacer(0))
    marker = work / "history-since.txt"

    # First run: only `main` is read.
    if parity._history_fetched(marker, ["refs/heads/main"], "2024-08-01") is None:
        fetch_history(repo, ["refs/heads/main"], "2024-08-01")
        parity._record_history_fetch(marker, ["refs/heads/main"], "2024-08-01")

    # Second run over the same work directory: `release` is now also read, `since` unchanged.
    both = ["refs/heads/main", "refs/heads/release"]
    if parity._history_fetched(marker, both, "2024-08-01") is None:
        fetch_history(repo, both, "2024-08-01")
        parity._record_history_fetch(marker, both, "2024-08-01")

    # Must not raise "unknown revision 'refs/heads/release'".
    repo.git("log", "--format=%H %ct", *both)


def test_record_history_fetch_unions_branches_across_reruns(
    parity: types.ModuleType, tmp_path: Path
) -> None:
    marker = tmp_path / "history-since.txt"
    parity._record_history_fetch(marker, ["refs/heads/main"], "2024-08-01")
    parity._record_history_fetch(marker, ["refs/heads/release"], "2024-08-01")
    held = json.loads(marker.read_text())
    assert sorted(held["branches"]) == ["refs/heads/main", "refs/heads/release"]
    assert (
        parity._history_fetched(marker, ["refs/heads/main", "refs/heads/release"], "2024-08-01")
        is not None
    )


# ---------------------------------------------------------------------------
# item 4c: --keep-work
# ---------------------------------------------------------------------------


def test_work_directory_is_deleted_by_default_after_a_run(
    parity: types.ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`main()`'s own cleanup, exercised directly: `_run` raising must still delete `--work`
    unless `--keep-work` was passed."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "marker").write_text("x")

    def boom(args: Any, salt: Any) -> None:
        raise RuntimeError("simulated failure mid-run")

    monkeypatch.setattr(parity, "_run", boom)
    monkeypatch.setattr(parity, "require_salt", lambda: "salt")

    monkeypatch.setattr(
        sys, "argv", ["notedb_parity.py", "--work", str(work), "--rest-root", str(tmp_path)]
    )
    with pytest.raises(RuntimeError, match="simulated failure"):
        parity.main()
    assert not work.exists(), "work must be deleted on an error, not just a clean exit"


def test_keep_work_flag_preserves_the_directory_on_error(
    parity: types.ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "marker").write_text("x")

    def boom(args: Any, salt: Any) -> None:
        raise RuntimeError("simulated failure mid-run")

    monkeypatch.setattr(parity, "_run", boom)
    monkeypatch.setattr(parity, "require_salt", lambda: "salt")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "notedb_parity.py",
            "--work",
            str(work),
            "--rest-root",
            str(tmp_path),
            "--keep-work",
        ],
    )
    with pytest.raises(RuntimeError, match="simulated failure"):
        parity.main()
    assert (work / "marker").is_file(), "--keep-work must preserve the directory on an error"
