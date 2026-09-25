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


def _rest_root(tmp_path: Path) -> Path:
    """A REST root the startup check accepts: it holds a raw/ directory."""
    root = tmp_path / "rr"
    (root / "raw").mkdir(parents=True, exist_ok=True)
    return root


def _patch_run_to_raise(parity: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(args: Any, salt: Any, scratch: Path) -> None:
        raise RuntimeError("simulated failure mid-run")

    monkeypatch.setattr(parity, "_run", boom)
    monkeypatch.setattr(parity, "require_salt", lambda: "salt")


def test_main_deletes_only_the_scratch_directory_it_created_on_error(
    parity: types.ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`main()`'s own cleanup, exercised directly: `_run` raising before any fetch must still
    delete the scratch directory this run created under `--work`, but `--work` itself and
    whatever it already held (a file this run never created) must survive untouched -- the
    destructive bug fixed here once deleted `--work` wholesale."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "pre-existing-file").write_text("must survive")

    _patch_run_to_raise(parity, monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        ["notedb_parity.py", "--work", str(work), "--rest-root", str(_rest_root(tmp_path))],
    )
    with pytest.raises(RuntimeError, match="simulated failure"):
        parity.main()
    assert work.is_dir(), "--work itself must never be deleted"
    assert (work / "pre-existing-file").read_text() == "must survive"
    remaining = list(work.iterdir())
    assert remaining == [work / "pre-existing-file"], (
        "cleanup must remove only the scratch dir this run made, leaving what --work already held"
    )


def test_ledger_is_copied_out_before_a_failed_runs_scratch_directory_is_deleted(
    parity: types.ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """The ledger records what a run asked the server for, but it lives inside the scratch
    directory `main()` deletes on the way out -- so without copying it out first, a failed
    run's own record of its network requests is lost with the scratch dir that held it."""
    work = tmp_path / "work"
    work.mkdir()

    def boom(args: Any, salt: Any, scratch: Path) -> None:
        (scratch / "ledger.jsonl").write_text(
            json.dumps(
                {
                    "at": "2026-01-01T00:00:00+00:00",
                    "host": "example.invalid",
                    "purpose": "history",
                    "items": 2,
                    "http_requests": 1,
                    "seconds": 0.01,
                    "returncode": 0,
                }
            )
            + "\n"
        )
        raise RuntimeError("simulated failure mid-run")

    monkeypatch.setattr(parity, "_run", boom)
    monkeypatch.setattr(parity, "require_salt", lambda: "salt")
    monkeypatch.setattr(
        sys,
        "argv",
        ["notedb_parity.py", "--work", str(work), "--rest-root", str(_rest_root(tmp_path))],
    )
    with pytest.raises(RuntimeError, match="simulated failure"):
        parity.main()

    ledgers = sorted(work.glob("ledger-*.jsonl"))
    assert len(ledgers) == 1, "the ledger must be copied out before the scratch dir is deleted"
    entries = [json.loads(line) for line in ledgers[0].read_text().splitlines()]
    assert entries == [
        {
            "at": "2026-01-01T00:00:00+00:00",
            "host": "example.invalid",
            "purpose": "history",
            "items": 2,
            "http_requests": 1,
            "seconds": 0.01,
            "returncode": 0,
        }
    ]
    assert set(entries[0]) == {
        "at",
        "host",
        "purpose",
        "items",
        "http_requests",
        "seconds",
        "returncode",
    }, "the preserved ledger must hold no raw identity, only operation, purpose, counts and timing"
    assert str(ledgers[0]) in capsys.readouterr().out, "the preserved path must be printed"


def test_no_ledger_file_is_written_when_the_run_made_no_ledger(
    parity: types.ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that fails before `Repo.open` (no ledger at all) must not fabricate one."""
    work = tmp_path / "work"
    work.mkdir()
    _patch_run_to_raise(parity, monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        ["notedb_parity.py", "--work", str(work), "--rest-root", str(_rest_root(tmp_path))],
    )
    with pytest.raises(RuntimeError, match="simulated failure"):
        parity.main()
    assert list(work.glob("ledger-*.jsonl")) == []


def test_keep_work_flag_preserves_the_marked_scratch_directory_on_error(
    parity: types.ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    work.mkdir()

    _patch_run_to_raise(parity, monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "notedb_parity.py",
            "--work",
            str(work),
            "--rest-root",
            str(_rest_root(tmp_path)),
            "--keep-work",
        ],
    )
    with pytest.raises(RuntimeError, match="simulated failure"):
        parity.main()
    scratch = parity._find_marked_scratch(work)
    assert scratch is not None, "--keep-work must preserve the marked scratch directory"


def test_a_kept_scratch_directory_is_reused_by_the_next_run(
    parity: types.ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rerun-cache semantics (history marker keyed on branches, ledger reuse) depend on a
    second run finding the *same* scratch directory a first, kept run made -- not a new one."""
    work = tmp_path / "work"
    work.mkdir()

    seen: list[Path] = []

    def record(args: Any, salt: Any, scratch: Path) -> None:
        seen.append(scratch)
        (scratch / "history-since.txt").write_text(
            '{"branches": ["refs/heads/main"], "since": "x"}'
        )

    monkeypatch.setattr(parity, "_run", record)
    monkeypatch.setattr(parity, "require_salt", lambda: "salt")
    argv = [
        "notedb_parity.py",
        "--work",
        str(work),
        "--rest-root",
        str(_rest_root(tmp_path)),
        "--keep-work",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    parity.main()
    parity.main()
    assert len(seen) == 2
    assert seen[0] == seen[1], "the second run must reuse the first run's scratch directory"
    assert (seen[0] / "history-since.txt").is_file(), "the reused directory keeps its contents"


# ---------------------------------------------------------------------------
# item 1: --work never deletes anything it did not create itself
# ---------------------------------------------------------------------------


def test_refuse_unsafe_work_when_work_is_the_checkout_root(parity: types.ModuleType) -> None:
    """`--work .` (from inside the checkout) must be refused before doing any work."""
    checkout = parity._checkout_root()
    with pytest.raises(SystemExit, match="repository checkout"):
        parity._refuse_unsafe_work(
            checkout, checkout / "datasets/gerrit/aosp", checkout / "out.json"
        )


def test_refuse_unsafe_work_when_work_is_an_ancestor_of_rest_root(
    parity: types.ModuleType, tmp_path: Path
) -> None:
    """`--work <checkout>/datasets/gerrit` is an ancestor of the default `--rest-root`; deleting
    the scratch dir this run creates under it must never risk the REST corpus alongside it."""
    rest_root = tmp_path / "datasets" / "gerrit" / "aosp"
    rest_root.mkdir(parents=True)
    work = rest_root.parent  # tmp_path/datasets/gerrit
    with pytest.raises(SystemExit, match="--rest-root"):
        parity._refuse_unsafe_work(work, rest_root, tmp_path / "out.json")


def test_refuse_unsafe_work_when_work_is_the_out_directory(
    parity: types.ModuleType, tmp_path: Path
) -> None:
    """`--work` naming the directory `--out` is written into must be refused: an artifact
    written then swept up by cleanup is silently lost."""
    out = tmp_path / "results" / "notedb-parity-aosp.json"
    with pytest.raises(SystemExit, match="--out"):
        parity._refuse_unsafe_work(out.parent, tmp_path / "rr", out)


def test_refuse_unsafe_work_follows_a_symlinked_work_path(
    parity: types.ModuleType, tmp_path: Path
) -> None:
    """A `--work` that is itself a symlink to a dangerous location must not slip past the guard
    unresolved."""
    checkout = parity._checkout_root()
    link = tmp_path / "work-link"
    link.symlink_to(checkout)
    with pytest.raises(SystemExit):
        parity._refuse_unsafe_work(link, tmp_path / "rr", tmp_path / "out.json")


def test_refuse_unsafe_work_allows_an_ordinary_scratch_location(
    parity: types.ModuleType, tmp_path: Path
) -> None:
    """The common case -- a `--work` unrelated to the checkout, rest-root or out -- is not
    refused."""
    work = tmp_path / "scratch"
    parity._refuse_unsafe_work(work, tmp_path / "rr", tmp_path / "out.json")  # must not raise


def test_rmtree_marked_refuses_an_unmarked_directory(
    parity: types.ModuleType, tmp_path: Path
) -> None:
    """The last line of defence: even called directly, `_rmtree_marked` must refuse a directory
    that does not carry the marker this script itself writes on creation."""
    unmarked = tmp_path / "not-mine"
    unmarked.mkdir()
    (unmarked / "keepme").write_text("x")
    with pytest.raises(RuntimeError, match="refusing to delete"):
        parity._rmtree_marked(unmarked)
    assert (unmarked / "keepme").is_file()


def test_rmtree_marked_does_not_follow_a_symlink_to_the_checkout(
    parity: types.ModuleType, tmp_path: Path
) -> None:
    """A symlink inside the scratch directory that points at the checkout (or anywhere else)
    must not be traversed when the scratch directory itself is deleted."""
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keepme"
    sentinel.write_text("keep")

    scratch = parity._make_scratch(tmp_path / "work")
    (scratch / "link-to-outside").symlink_to(outside)
    parity._rmtree_marked(scratch)
    assert not scratch.exists()
    assert sentinel.is_file(), "a symlink inside the deleted scratch dir must not be followed"


def test_cleanup_survives_a_second_signal_via_subprocess(tmp_path: Path) -> None:
    """M25: a mutant that dropped the `_uninterruptible` wrap around `main()`'s own cleanup call.
    Run in a child process (as `test_fetch_month_cleanup_survives_a_second_signal_via_subprocess`
    in `test_notedb.py` does for the route's own cleanup): an unprotected mutant kills the child
    outright on the first self-signal, before it ever reaches the real `shutil.rmtree`, leaving
    the marked scratch directory behind -- the parent sees that leftover as a clean failure
    rather than losing its own process to a stray SIGTERM."""
    work = tmp_path / "work"
    work.mkdir()
    rest_root = _rest_root(tmp_path)
    script = f"""
import os
import signal
import sys
import time

sys.path.insert(0, {str(_ROOT)!r})
import shutil

real_rmtree = shutil.rmtree

def slow_rmtree(path, *a, **kw):
    os.kill(os.getpid(), signal.SIGTERM)
    os.kill(os.getpid(), signal.SIGINT)
    time.sleep(0.2)
    return real_rmtree(path, *a, **kw)

shutil.rmtree = slow_rmtree

import importlib.util
spec = importlib.util.spec_from_file_location("_under_test_notedb_parity", {str(_SCRIPT)!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

mod._run = lambda args, salt, scratch: None
mod.require_salt = lambda: "salt"
sys.argv = ["notedb_parity.py", "--work", {str(work)!r}, "--rest-root", {str(rest_root)!r}]
mod.main()
print("DONE")
"""
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    assert "DONE" in proc.stdout
    assert list(work.iterdir()) == [], "cleanup must survive a second signal mid-rmtree"


def test_a_rest_root_without_raw_snapshots_is_refused_before_any_fetch(
    parity: types.ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A mistyped --rest-root must stop the run before it spends a request on the host."""
    ran = []
    monkeypatch.setattr(parity, "_run", lambda *a: ran.append(a))
    monkeypatch.setattr(parity, "require_salt", lambda: "salt")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "notedb_parity.py",
            "--work",
            str(tmp_path / "w"),
            "--rest-root",
            str(tmp_path / "missing"),
        ],
    )
    (tmp_path / "w").mkdir()
    with pytest.raises(SystemExit, match="no raw/ REST snapshots"):
        parity.main()
    assert not ran


def test_rest_examples_reads_the_stage_it_is_asked_for(
    parity: types.ModuleType, tmp_path: Path
) -> None:
    for stage, comment in (("examples", "built"), ("refined", "refined")):
        (tmp_path / stage).mkdir()
        row = {"project": parity.PROJECT, "change_id": "I1", "created": "t", "id": comment}
        (tmp_path / stage / "2024-11.jsonl").write_text(json.dumps(row) + "\n")
    assert [e["id"] for e in parity.rest_examples(tmp_path)[("I1", "t")]] == ["built"]
    assert [e["id"] for e in parity.rest_examples(tmp_path, "refined")[("I1", "t")]] == ["refined"]


def test_compare_examples_refines_the_git_side_when_given_an_index(
    parity: types.ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The build keeps a one-click "Acknowledged" the refined REST corpus no longer holds; only
    with `refine_index` does the git side go through the same rules before it is compared."""
    example = {
        "id": "e1",
        "change_id": "I1",
        "created": "t",
        "comments": ["Acknowledged", "rename this"],
        **{f: "" for f in parity.EXAMPLE_FIELDS if f not in ("id", "change_id", "created")},
    }
    refined = {**example, "comments": ["rename this"]}
    monkeypatch.setattr(parity, "build_from_change", lambda *a: ([dict(example)], {}))
    monkeypatch.setattr(parity, "embedded_fetchers", lambda git: (None, None))
    pair = ({"change_id": "I1", "created": "t"}, {"change_id": "I1", "created": "t"})
    rest = {("I1", "t"): [refined]}
    plain = parity.compare_examples([pair], rest, tmp_path / "a.jsonl")
    both = parity.compare_examples(
        [pair], rest, tmp_path / "b.jsonl", refine_index=parity.index_changes([])
    )
    assert plain["counts"].get("identical", 0) == 0
    assert both["counts"]["identical"] == 1


def test_main_refuses_an_unsafe_work_before_any_fetch(
    parity: types.ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ran = []
    monkeypatch.setattr(parity, "_run", lambda *a: ran.append(a))
    monkeypatch.setattr(parity, "require_salt", lambda: "salt")
    checkout = parity._checkout_root()
    argv = ["notedb_parity.py", "--work", str(checkout), "--rest-root", str(_rest_root(tmp_path))]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="repository checkout"):
        parity.main()
    assert not ran


def test_a_work_inside_the_checkout_must_be_ignored_by_git(
    parity: types.ModuleType, tmp_path: Path
) -> None:
    """The scratch repository holds raw identities, so it may only sit where git ignores it."""
    checkout = parity._checkout_root()
    rest, out = _rest_root(tmp_path), tmp_path / "out" / "a.json"
    with pytest.raises(SystemExit, match="does not ignore"):
        parity._refuse_unsafe_work(checkout / "docs" / "parity-scratch", rest, out)
    parity._refuse_unsafe_work(checkout / "scratch" / "parity-work", rest, out)
