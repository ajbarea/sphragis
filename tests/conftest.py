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
import re
import socket
import tomllib
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

#: For `test_skip_guard.py`, which runs this conftest in a child session.
pytest_plugins = ("pytester",)

#: Everything git exports to a hook starts with this. Matched by prefix rather than by a
#: list, because the list is git's and grows with it.
GIT_PREFIX = "GIT_"


@pytest.fixture(autouse=True, scope="session")
def _no_inherited_git_environment() -> Iterator[None]:
    """Remove git's exported environment for the duration of the run, and pin git to `file`.

    Session-scoped and autouse: the hazard is the environment the interpreter started
    with, so it is removed once rather than per test, and no test has to ask for it.

    `GIT_ALLOW_PROTOCOL=file` is set alongside the removal: it is itself a `GIT_*` variable,
    so a test that shells out to git without its own `env=` (the point of this fixture) still
    gets it. Any test's git subprocess that tries http(s) or ssh is then refused by git itself,
    before a socket is opened -- a second guard alongside the in-process one below, covering
    exactly what that one does not (subprocess network I/O).
    """
    inherited = {name: os.environ[name] for name in list(os.environ) if name.startswith(GIT_PREFIX)}
    for name in inherited:
        del os.environ[name]
    os.environ["GIT_ALLOW_PROTOCOL"] = "file"
    yield
    del os.environ["GIT_ALLOW_PROTOCOL"]
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
_real_sendmsg = socket.socket.sendmsg
_real_lookups = {
    "getaddrinfo": socket.getaddrinfo,
    "gethostbyname": socket.gethostbyname,
    "gethostbyname_ex": socket.gethostbyname_ex,
    "gethostbyaddr": socket.gethostbyaddr,
}
_real_getnameinfo = socket.getnameinfo


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


def _sendmsg(self: socket.socket, buffers: Any, *args: Any) -> int:
    if len(args) >= 3 and not _is_local(args[2]):
        raise _refuse(f"a message to {args[2]!r}")
    return _real_sendmsg(self, buffers, *args)


def _getnameinfo(address: Any, flags: int) -> Any:
    if not _is_local(address):
        raise _refuse(f"a reverse lookup of {address!r}")
    return _real_getnameinfo(address, flags)


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
    socket.gethostbyaddr = _lookup("gethostbyaddr")
    socket.socket.sendmsg = _sendmsg  # ty: ignore[invalid-assignment]
    socket.getnameinfo = _getnameinfo  # ty: ignore[invalid-assignment]


def pytest_unconfigure(config: pytest.Config) -> None:
    socket.socket.connect = _real_connect  # ty: ignore[invalid-assignment]
    socket.socket.connect_ex = _real_connect_ex  # ty: ignore[invalid-assignment]
    socket.socket.sendto = _real_sendto  # ty: ignore[invalid-assignment]
    socket.getaddrinfo = _real_lookups["getaddrinfo"]  # ty: ignore[invalid-assignment]
    socket.gethostbyname = _real_lookups["gethostbyname"]  # ty: ignore[invalid-assignment]
    socket.gethostbyname_ex = _real_lookups["gethostbyname_ex"]  # ty: ignore[invalid-assignment]
    socket.gethostbyaddr = _real_lookups["gethostbyaddr"]  # ty: ignore[invalid-assignment]
    socket.socket.sendmsg = _real_sendmsg  # ty: ignore[invalid-assignment]
    socket.getnameinfo = _real_getnameinfo


#: Every skip the run reported, from collection (a module-level `importorskip`) and from tests.
_SKIPS: list[pytest.CollectReport | pytest.TestReport] = []

#: The skips `pytest_sessionfinish` refused, for the terminal summary to name.
_UNACCEPTED: list[str] = []

#: Beside this conftest rather than at the rootdir, which `--rootdir` or an IDE can move.
_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

_MISSING_MODULE = re.compile(r"could not import '([^']+)'")

#: The one skip reason besides an absent GPU stack: a machine with the stack but no GPU
#: (`test_model_stack.py`'s `skipif`).
_NO_CUDA = "needs a CUDA device"


def _gpu_stack(pyproject: Path) -> frozenset[str]:
    """The packages the `experiment` extra installs and the `dev` group does not.

    A test may skip only because one of these is absent: CI and `make sync` install `dev` and
    never the extra, so these tests run under `make gpu-local` or on the clusters instead.
    The skip is recognised by `importorskip`'s own message, so one given a `reason=`, or a
    package whose import name is not its distribution name, is refused: a false alarm the run
    reports, never a skip let through.
    """
    project = tomllib.loads(pyproject.read_text())

    def names(requirements: Iterable[str]) -> set[str]:
        return {canonicalize_name(Requirement(requirement).name) for requirement in requirements}

    extra = names(project["project"]["optional-dependencies"]["experiment"])
    dev = names(r for r in project["dependency-groups"]["dev"] if isinstance(r, str))
    return frozenset(extra - dev)


def _unaccepted_skips(
    reports: Iterable[pytest.CollectReport | pytest.TestReport], gpu_stack: frozenset[str]
) -> list[str]:
    """Every skip not explained by the GPU stack or a GPU being absent, as `nodeid: reason`."""
    unaccepted = []
    for report in reports:
        longrepr = report.longrepr
        reason = longrepr[2] if isinstance(longrepr, tuple) else str(longrepr)
        # Anchored: importorskip's message opens the reason; a module named mid-text is refused.
        missing = _MISSING_MODULE.match(reason.removeprefix("Skipped: "))
        if missing and canonicalize_name(missing[1].split(".")[0]) in gpu_stack:
            continue
        if reason.removeprefix("Skipped: ") == _NO_CUDA:
            continue
        unaccepted.append(f"{report.nodeid}: {reason}")
    return unaccepted


def pytest_collectreport(report: pytest.CollectReport) -> None:
    if report.skipped:
        _SKIPS.append(report)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.skipped and not hasattr(report, "wasxfail"):
        _SKIPS.append(report)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the run on a refusal no test saw, or on a skip for any reason but the GPU stack.

    A refusal caught where no test's teardown sees it, as in a module fixture, is one. A skip
    is the other: a test that `importorskip`s a package `make sync` does not install passes green
    without running, as five modules of script tests once did in CI.
    """
    if _VIOLATIONS:
        print(f"\noffline test suite: the network was tried: {_VIOLATIONS}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    if _SKIPS:
        _UNACCEPTED.extend(_unaccepted_skips(_SKIPS, _gpu_stack(_PYPROJECT)))
        # Only a passing run is turned into a failure: an interrupt or a usage error keeps its code.
        if _UNACCEPTED and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if _UNACCEPTED:
        terminalreporter.section("skips the suite does not accept", sep="=", red=True, bold=True)
        terminalreporter.line("only an absent GPU stack or GPU may skip a test (tests/conftest.py)")
        for skip in _UNACCEPTED:
            terminalreporter.line(skip, red=True)


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
