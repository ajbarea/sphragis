"""The guard in `tests/conftest.py`, and the damage it exists to prevent.

The first half reproduces the failure on a victim repository built for the purpose, so
the mechanism is pinned rather than described. The second half asserts the suite's own
environment carries none of it. Remove the fixture and the second half fails; change git
so that `git init` no longer honours `GIT_DIR` and the first half fails, which is the
signal that the guard has stopped guarding anything.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import GIT_PREFIX


def _scratch_repo(root: Path, env: dict[str, str] | None = None) -> None:
    """The sequence a test that needs a real repository runs, exactly as one would write it."""
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "probe.py").write_text("x = 1\n")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "add", "-A"],
    ):
        subprocess.run(command, cwd=root, env=env, check=True, capture_output=True)


def test_an_inherited_gitdir_reaches_the_repository_that_exported_it(tmp_path: Path) -> None:
    """With `GIT_DIR` set, the scratch sequence writes into the victim, not into `cwd`.

    This is the whole hazard in six lines. `git init` reinitializes the directory `GIT_DIR`
    names and sets `core.bare` there, and `git add` writes the scratch files into that
    repository's index. Neither command is given a path and neither warns.
    """
    victim = tmp_path / "victim"
    victim.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=victim, check=True, capture_output=True)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    _scratch_repo(scratch, env={**os.environ, "GIT_DIR": str(victim / ".git")})

    reached = subprocess.run(
        ["git", "-C", str(victim), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.split()
    assert "scripts/probe.py" in reached, (
        "the hazard no longer reproduces; if git has changed, tests/conftest.py's fixture "
        "may no longer be what protects the repository"
    )
    assert not (scratch / ".git").exists(), "the scratch repository was never created"


def test_the_same_sequence_touches_nothing_when_the_environment_is_clean(tmp_path: Path) -> None:
    """The suite's own environment, which the conftest fixture has already stripped."""
    victim = tmp_path / "victim"
    victim.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=victim, check=True, capture_output=True)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    _scratch_repo(scratch)

    reached = subprocess.run(
        ["git", "-C", str(victim), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.split()
    assert reached == []
    assert (scratch / ".git").is_dir(), "the scratch repository is where the commands landed"


def test_no_git_variable_reaches_a_test() -> None:
    """What the fixture promises, `GIT_ALLOW_PROTOCOL` aside: that one is the fixture's own
    deliberate addition (a git subprocess is refused http(s)/ssh, file only), not a leak.
    Fails if it is removed, narrowed, or scoped away."""
    inherited = sorted(name for name in os.environ if name.startswith(GIT_PREFIX))
    assert inherited == ["GIT_ALLOW_PROTOCOL"], f"git's exported environment reached: {inherited}"
    assert os.environ["GIT_ALLOW_PROTOCOL"] == "file"


@pytest.mark.parametrize(
    "variable", ["GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY"]
)
def test_the_variables_a_hook_exports_are_covered_by_the_prefix(variable: str) -> None:
    """The fixture matches by prefix, so this is what that prefix has to cover."""
    assert variable.startswith(GIT_PREFIX)
