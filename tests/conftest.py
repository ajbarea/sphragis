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


class OfflineViolation(RuntimeError):
    """A test tried to reach a host other than loopback.

    Not an OSError: the REST transport turns OSError into a retryable 503, which a build then
    counts as a drop, so a leak raised as OSError finished green with the word never printed.
    """


#: Every refused attempt, so a test whose code swallows the exception still fails.
_VIOLATIONS: list[str] = []


def _is_local(address: Any) -> bool:
    if not isinstance(address, tuple):
        return True  # AF_UNIX paths and the like
    try:
        return ipaddress.ip_address(address[0]).is_loopback
    except ValueError:
        return address[0] == "localhost"


def _refuse(what: str) -> OfflineViolation:
    _VIOLATIONS.append(what)
    return OfflineViolation(f"offline test suite: refused {what}")


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_sendto = socket.socket.sendto
_real_lookups = {
    "getaddrinfo": socket.getaddrinfo,
    "gethostbyname": socket.gethostbyname,
    "gethostbyname_ex": socket.gethostbyname_ex,
}


def _connect(self: socket.socket, address: Any) -> None:
    if not _is_local(address):
        raise _refuse(f"a connection to {address!r}")
    return _real_connect(self, address)


def _connect_ex(self: socket.socket, address: Any) -> int:
    if not _is_local(address):
        raise _refuse(f"a connection to {address!r}")
    return _real_connect_ex(self, address)


def _sendto(self: socket.socket, data: Any, *args: Any) -> int:
    address = args[-1]
    if not _is_local(address):
        raise _refuse(f"a datagram to {address!r}")
    return _real_sendto(self, data, *args)


def _lookup(name: str) -> Any:
    def lookup(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in (None, "localhost") and not _is_local((host,)):
            raise _refuse(f"a lookup of {host!r}")
        return _real_lookups[name](host, *args, **kwargs)

    return lookup


def pytest_configure(config: pytest.Config) -> None:
    """Refuse every in-process socket to a non-loopback host, from collection onward.

    A unit test that reaches a live host is a collection the study did not decide on: a rebased
    test once sent a REST query to chromium-review, whose robots.txt disallows it. Installed at
    configure time rather than in a fixture so code run while test modules are collected is
    covered too. A subprocess is outside this guard; no test runs one against a remote host,
    and the suite passes under `unshare -n`.
    """
    socket.socket.connect = _connect  # ty: ignore[invalid-assignment]
    socket.socket.connect_ex = _connect_ex  # ty: ignore[invalid-assignment]
    socket.socket.sendto = _sendto  # ty: ignore[invalid-assignment]
    socket.getaddrinfo = _lookup("getaddrinfo")
    socket.gethostbyname = _lookup("gethostbyname")
    socket.gethostbyname_ex = _lookup("gethostbyname_ex")


def pytest_unconfigure(config: pytest.Config) -> None:
    socket.socket.connect = _real_connect  # ty: ignore[invalid-assignment]
    socket.socket.connect_ex = _real_connect_ex  # ty: ignore[invalid-assignment]
    socket.socket.sendto = _real_sendto  # ty: ignore[invalid-assignment]
    socket.getaddrinfo = _real_lookups["getaddrinfo"]  # ty: ignore[invalid-assignment]
    socket.gethostbyname = _real_lookups["gethostbyname"]  # ty: ignore[invalid-assignment]
    socket.gethostbyname_ex = _real_lookups["gethostbyname_ex"]  # ty: ignore[invalid-assignment]


@pytest.fixture(autouse=True)
def _no_network_attempt() -> Iterator[None]:
    """Fail the test that tried, even when its code caught the refusal."""
    before = len(_VIOLATIONS)
    yield
    attempted = _VIOLATIONS[before:]
    if attempted:
        pytest.fail(f"tried to reach the network: {attempted}")


@pytest.fixture
def offline_refusals() -> Iterator[list[str]]:
    """For the guard's own tests: the refusals they trip on purpose, cleared before the check."""
    before = len(_VIOLATIONS)
    tripped: list[str] = []
    yield tripped
    tripped.extend(_VIOLATIONS[before:])
    del _VIOLATIONS[before:]
