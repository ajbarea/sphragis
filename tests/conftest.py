"""Fixtures the whole suite gets, whatever invoked it.

Several tests build a scratch git repository: the provenance record has to be read out of
a real one, and the job scripts' guards have to be exercised against real commits. Git
exports `GIT_DIR`, `GIT_INDEX_FILE`, `GIT_WORK_TREE` and friends into the environment of
every hook it runs, so a suite invoked from a pre-commit hook inherits them, and `git
init` or `git add` in a scratch directory then acts on the repository being committed to
instead of on the scratch one.

That is not hypothetical, and it is not survivable. Run from the pre-commit hook inside a
worktree, where `GIT_DIR` is exported as an absolute path rather than the bare `.git` a
plain checkout exports, this suite reinitialized the shared repository: the index was
replaced by the scratch repository's two files, `core.bare = true` and a `[user]` section
naming the scratch identity were written into the shared config, and the main checkout
stopped being a working tree until all three were undone by hand.

`tests/unit/experiment/test_cluster_env.py` had already met this and filtered the
variables out for its own git calls. One test file defending itself is not a fix, because
the next test to shell out to git does not inherit the reasoning. Stripping the variables
once, here, is the fix: a test that shells out to git needs no `env` argument of its own,
and cannot reintroduce the hazard by forgetting one.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

#: Everything git exports to a hook starts with this. Matched by prefix rather than by a
#: list, because the list is git's and grows with it.
GIT_PREFIX = "GIT_"


@pytest.fixture(autouse=True, scope="session")
def _no_inherited_git_environment() -> Iterator[None]:
    """Remove git's exported environment for the duration of the run.

    Session-scoped and autouse: the hazard is the environment the interpreter started
    with, so it is removed once rather than per test, and no test has to ask for it.
    """
    inherited = {name: os.environ[name] for name in list(os.environ) if name.startswith(GIT_PREFIX)}
    for name in inherited:
        del os.environ[name]
    yield
    os.environ.update(inherited)
