"""Every directory holding corpus text stays out of git, including stages added later."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

# Directories under a corpus root's <org>/ whose files carry code and review comments, and one
# no stage writes yet: the rule is an allowlist, so a later stage is covered without an edit.
CORPUS_TEXT = ("raw", "examples", "refined", "splits", "a-later-stage")
ROOTS = ("datasets/gerrit", "datasets/gerrit-control")


@pytest.mark.parametrize("root", ROOTS)
@pytest.mark.parametrize("directory", CORPUS_TEXT)
def test_corpus_text_directories_are_ignored(root: str, directory: str) -> None:
    probe = f"{root}/anyorg/{directory}/2024-10.jsonl"
    result = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "-q", probe], capture_output=True
    )
    assert result.returncode == 0, f"{probe} would be committable; add it to .gitignore"


@pytest.mark.parametrize("name", ("manifest.json", "seal.json"))
def test_manifests_and_seals_are_committable(name: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "-q", f"datasets/gerrit/anyorg/{name}"],
        capture_output=True,
    )
    assert result.returncode == 1, "manifests and seals are what results cite; commit them"
