"""Every directory holding corpus text stays out of git, including stages added later."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

# Directories under datasets/gerrit/<org>/ whose files carry code and review comments.
CORPUS_TEXT = ("raw", "examples", "refined", "splits")


@pytest.mark.parametrize("directory", CORPUS_TEXT)
def test_corpus_text_directories_are_ignored(directory: str) -> None:
    probe = f"datasets/gerrit/anyorg/{directory}/2024-10.jsonl"
    result = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "-q", probe], capture_output=True
    )
    assert result.returncode == 0, f"{probe} would be committable; add it to .gitignore"


def test_manifests_are_committable() -> None:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "-q", "datasets/gerrit/anyorg/manifest.json"],
        capture_output=True,
    )
    assert result.returncode == 1, "manifests are what results cite and must be committed"
