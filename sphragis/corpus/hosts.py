"""One process's claim on a host that asks for a single client, shared by every route to it.

A host that asks for concurrency 1 counts every client machine-wide, so the REST transport
(`cli.http_transport`) and the git route (`notedb.Repo`) take the same lock file for it and keep
its quiet deadline there.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import IO

LOCK_DIR = Path.home() / ".cache" / "sphragis" / "hosts"


class HostClaim:
    """One process's exclusive claim on a host, and the host's quiet deadline, kept on disk.

    A host that asks for concurrency 1 counts every client machine-wide, and two stages run in
    two terminals would each pace themselves correctly and together break the limit. The lock
    file also carries the wall-clock time before which the host must not be asked again, so a
    pause a 5xx started outlives the process that saw it: `collect`-style loops start a fresh
    process per month.
    """

    def __init__(self, host: str, handle: IO[str]) -> None:
        self.host, self._handle = host, handle

    def quiet_until(self) -> float:
        self._handle.seek(0)
        text = self._handle.read().strip()
        return float(text) if text else 0.0

    def hold_until(self, deadline: float) -> None:
        if deadline <= self.quiet_until():
            return
        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(f"{deadline}\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def release(self) -> None:
        self._handle.close()


_CLAIMS: dict[tuple[Path, str], HostClaim] = {}


def claim_host(host: str, lock_dir: Path | None = None) -> HostClaim:
    """This process's claim on `host`, taken once and shared by every transport it builds.

    Exits if another process holds it. The lock lives at a fixed path under the user's home, so
    processes started from different environments contend for the same file.
    """
    directory = lock_dir or LOCK_DIR
    key = (directory, host)
    if key in _CLAIMS:
        return _CLAIMS[key]
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / f"{host}.lock").open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise SystemExit(
            f"refusing {host}: another process is already fetching from it, and the host asks "
            "for a concurrency of 1"
        ) from None
    _CLAIMS[key] = HostClaim(host, handle)
    return _CLAIMS[key]


def release_host(host: str, lock_dir: Path | None = None) -> None:
    """Give up this process's claim on `host`, keeping its quiet deadline on disk."""
    claim = _CLAIMS.pop((lock_dir or LOCK_DIR, host), None)
    if claim is not None:
        claim.release()
