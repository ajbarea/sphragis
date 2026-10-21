"""`scripts/verify_backup.sh` compares a local tree with its copy by sha256, through ssh."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_backup.sh"


def _verify(tmp_path: Path, *names: str) -> subprocess.CompletedProcess[str]:
    """Run the script with an `ssh` that runs its command here, on the "remote" tree."""
    stub = tmp_path / "bin" / "ssh"
    stub.parent.mkdir(exist_ok=True)
    # Options and host are dropped; the last argument is the remote command.
    stub.write_text('#!/bin/sh\nwhile [ "$#" -gt 1 ]; do shift; done\nexec sh -c "$1"\n')
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{stub.parent}:{os.environ['PATH']}", "SSH_OPTS": "-o X=1"}
    argv = [str(SCRIPT), "host", str(tmp_path / "local"), str(tmp_path / "remote"), *names]
    return subprocess.run(argv, env=env, capture_output=True, text=True, check=False)


def _tree(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)


@pytest.fixture
def trees(tmp_path: Path) -> Path:
    files = {"org/a.json": "1", "org/b/c.jsonl": "2", "org/.locks/x": "held"}
    _tree(tmp_path / "local", files)
    _tree(tmp_path / "remote", {**files, "org/.locks/x": "another"})
    return tmp_path


def test_identical_trees_pass_and_locks_are_skipped(trees: Path) -> None:
    result = _verify(trees, "org")
    assert result.returncode == 0, result.stderr
    assert "all 2 files identical" in result.stdout


def test_a_changed_file_is_counted_and_fails(trees: Path) -> None:
    (trees / "remote" / "org" / "a.json").write_text("changed")
    result = _verify(trees, "org")
    assert result.returncode == 1
    assert "1 of 2 file(s) differ" in result.stderr


def test_a_file_missing_on_the_copy_fails(trees: Path) -> None:
    (trees / "remote" / "org" / "b" / "c.jsonl").unlink()
    result = _verify(trees, "org")
    assert result.returncode == 1
    assert "1 of 2 file(s) differ" in result.stderr


def test_no_names_or_an_empty_tree_is_refused(trees: Path) -> None:
    assert _verify(trees).returncode == 1
    (trees / "local" / "empty").mkdir()
    (trees / "remote" / "empty").mkdir()
    result = _verify(trees, "empty")
    assert result.returncode == 1
    assert "0 of 0" in result.stderr


def test_a_build_checkpoint_is_skipped(tmp_path: Path) -> None:
    """A running build appends to its checkpoint between the copy and the check."""
    files = {"org/raw/2025-02.ndjson.gz": "1"}
    _tree(tmp_path / "local", {**files, "org/raw/2025-02.build.partial.jsonl": "grown"})
    _tree(tmp_path / "remote", files)
    result = _verify(tmp_path, "org")
    assert result.returncode == 0, result.stderr
    assert "all 1 files identical" in result.stdout
