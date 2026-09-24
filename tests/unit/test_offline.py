"""The suite never reaches a live host: a socket to anything but loopback is refused.

The addresses are reserved (RFC 5737, RFC 2606), so a guard that failed would still reach no one.
"""

from __future__ import annotations

import socket

import pytest


def test_a_socket_to_a_remote_address_is_refused() -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s,
        pytest.raises(OSError, match="offline"),
    ):
        s.connect(("192.0.2.1", 443))


def test_a_name_lookup_for_a_remote_host_is_refused() -> None:
    with pytest.raises(OSError, match="offline"):
        socket.getaddrinfo("gerrit.invalid", 443)


def test_loopback_stays_open() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=2):
            pass
