"""One process's claim on a host that asks for a single client, shared by every route to it.

A host that asks for concurrency 1 counts every client machine-wide, so the REST transport
(`cli.http_transport`) and the git route (`notedb.Repo`) take the same lock files for it
(`lock_names`) and keep its quiet deadline there.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import IO

LOCK_DIR = Path.home() / ".cache" / "sphragis" / "hosts"

#: The claim Chrome Infra's infra-dev answer of 2026-10-15 is read under, named for the host the
#: request was made for: one client at no more than one request a second, read for
#: chromium-review, android-review and fuchsia-review together (research log, 2026-10-19).
CHROME_INFRA = "chromium-review.googlesource.com"

#: Hosts claimed, and paced, as one client: the review hosts Chrome Infra's answer covers, and
#: AOSP's git host, which serves android-review's Gerrit instance (research log, 2026-10-18).
CLAIM_AS = {
    host: CHROME_INFRA
    for host in (
        "chromium-review.googlesource.com",
        "android-review.googlesource.com",
        "fuchsia-review.googlesource.com",
        "android.googlesource.com",
    )
}


def claim_name(host: str) -> str:
    """The one name `host` is claimed and paced under (`CLAIM_AS`), the host's own elsewhere."""
    return CLAIM_AS.get(host, host)


class HostClaim:
    """One process's exclusive claim on a host, and the host's quiet deadline, kept on disk.

    A host that asks for concurrency 1 counts every client machine-wide, and two stages run in
    two terminals would each pace themselves correctly and together break the limit. The lock
    file also carries the wall-clock time before which the host must not be asked again, so a
    pause a 5xx started outlives the process that saw it: `collect`-style loops start a fresh
    process per month. A claim under a shared name holds several files (`lock_names`), reads the
    latest deadline any of them carries and writes a new one to all of them.
    """

    def __init__(self, host: str, handles: list[IO[str]]) -> None:
        self.host, self._handles = host, handles

    def quiet_until(self) -> float:
        deadlines = [0.0]
        for handle in self._handles:
            handle.seek(0)
            text = handle.read().strip()
            deadlines.append(float(text) if text else 0.0)
        return max(deadlines)

    def hold_until(self, deadline: float) -> None:
        if deadline <= self.quiet_until():
            return
        for handle in self._handles:
            handle.seek(0)
            handle.truncate()
            handle.write(f"{deadline}\n")
            handle.flush()
            os.fsync(handle.fileno())

    def release(self) -> None:
        for handle in self._handles:
            handle.close()


_CLAIMS: dict[tuple[Path, str], HostClaim] = {}


def lock_names(host: str) -> list[str]:
    """The lock files a claim on `host` holds: its claim name's and, under a shared claim, each
    member host's own, which code from before the claim was shared locks, so old and new code
    still exclude each other and a pause either writes is read by both."""
    name = claim_name(host)
    return sorted({name, *(member for member, shared in CLAIM_AS.items() if shared == name)})


def claim_host(host: str, lock_dir: Path | None = None) -> HostClaim:
    """This process's claim on `host`, taken once and shared by every transport it builds.

    Exits if another process holds any of its lock files (`lock_names`). The locks live at a fixed
    path under the user's home, so processes started from different environments contend for the
    same files.
    """
    directory = lock_dir or LOCK_DIR
    name = claim_name(host)
    key = (directory, name)
    if key in _CLAIMS:
        return _CLAIMS[key]
    directory.mkdir(parents=True, exist_ok=True)
    handles: list[IO[str]] = []
    for lock in lock_names(host):
        handle = (directory / f"{lock}.lock").open("a+")
        handles.append(handle)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            for taken in handles:
                taken.close()
            also = f" or a host claimed with it as {name}" if name != host else ""
            raise SystemExit(
                f"refusing {host}: another process is already fetching from it{also}, and the "
                "host asks for a concurrency of 1"
            ) from None
    _CLAIMS[key] = HostClaim(host, handles)
    return _CLAIMS[key]


def release_host(host: str, lock_dir: Path | None = None) -> None:
    """Give up this process's claim on `host`, keeping its quiet deadline on disk."""
    claim = _CLAIMS.pop((lock_dir or LOCK_DIR, claim_name(host)), None)
    if claim is not None:
        claim.release()
