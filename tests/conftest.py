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

import ipaddress
import os
import socket
from collections.abc import Iterator
from typing import Any

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


def _is_local(address: Any) -> bool:
    if not isinstance(address, tuple):
        return True  # AF_UNIX paths and the like
    try:
        return ipaddress.ip_address(address[0]).is_loopback
    except ValueError:
        return address[0] == "localhost"


@pytest.fixture(autouse=True, scope="session")
def _offline() -> Iterator[None]:
    """Refuse every socket to a host other than loopback, and every remote name lookup.

    A unit test that reaches a live host is a collection the study did not decide on: a rebased
    test once sent a REST query to chromium-review, whose robots.txt disallows it. Refusing at
    the socket makes the suite offline whatever a test forgets to fake.
    """
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def connect(self: socket.socket, address: Any) -> None:
        if not _is_local(address):
            raise OSError(f"offline test suite: refused a connection to {address!r}")
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if not _is_local(address):
            raise OSError(f"offline test suite: refused a connection to {address!r}")
        return real_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in (None, "localhost") and not _is_local((host,)):
            raise OSError(f"offline test suite: refused a lookup of {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    mp = pytest.MonkeyPatch()
    mp.setattr(socket.socket, "connect", connect)
    mp.setattr(socket.socket, "connect_ex", connect_ex)
    mp.setattr(socket, "getaddrinfo", getaddrinfo)
    yield
    mp.undo()
