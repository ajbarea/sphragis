"""The suite never reaches a live host: a socket to anything but loopback is refused.

The addresses are reserved (RFC 5737, RFC 2606), so a guard that failed would still reach no one.
"""

from __future__ import annotations

import os
import socket
import subprocess

import pytest


def test_a_socket_to_a_remote_address_is_refused(offline_refusals: list[str]) -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s,
        pytest.raises(RuntimeError, match="offline"),
    ):
        s.connect(("192.0.2.1", 443))


def test_a_datagram_to_a_remote_address_is_refused(offline_refusals: list[str]) -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s,
        pytest.raises(RuntimeError, match="offline"),
    ):
        s.sendto(b"x", ("192.0.2.1", 53))


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("getaddrinfo", ("gerrit.invalid", 443)),
        ("gethostbyname", ("gerrit.invalid",)),
        ("gethostbyaddr", ("192.0.2.1",)),
        ("getnameinfo", (("192.0.2.1", 80), 0)),
    ],
)
def test_a_name_lookup_for_a_remote_host_is_refused(
    offline_refusals: list[str], name: str, args: tuple[int, ...]
) -> None:
    with pytest.raises(RuntimeError, match="offline"):
        getattr(socket, name)(*args)


def test_a_message_to_a_remote_address_is_refused(offline_refusals: list[str]) -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s,
        pytest.raises(RuntimeError, match="offline"),
    ):
        s.sendmsg([b"x"], [], 0, ("192.0.2.1", 53))


def test_a_leak_through_the_rest_transport_raises_rather_than_reading_as_a_503(
    offline_refusals: list[str],
) -> None:
    """OSError would become a retryable 503 there; the refusal must surface instead."""
    from sphragis.corpus import cli

    with pytest.raises(RuntimeError, match="offline"):
        cli.http_transport()("https://review.opendev.org/config/server/version")


def test_an_https_git_fetch_is_refused_by_the_suites_own_environment() -> None:
    """`GIT_ALLOW_PROTOCOL=file`, set on `os.environ` for the whole session: a second guard,
    for the git subprocesses the in-process socket guard above cannot see at all."""
    assert os.environ.get("GIT_ALLOW_PROTOCOL") == "file"
    done = subprocess.run(
        ["git", "ls-remote", "https://example.invalid/repo.git"],
        capture_output=True,
        text=True,
    )
    assert done.returncode != 0
    assert "not allowed" in done.stderr or "protocol" in done.stderr.lower()


def test_loopback_stays_open() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=2):
            pass
