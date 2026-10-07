"""Writes to the raw tier that survive a hard stop.

A snapshot is the reproducibility floor and its checkpoint is the only record of a month in
progress, and neither can be rebuilt from anything downstream. WSL can be stopped without
warning, and ext4 can then keep a file's new length while losing the data it grew for, leaving
zeros. A file written here lands whole or not at all, and a log keeps every record whose append
returned.
"""

from __future__ import annotations

import errno
import fcntl
import glob
import json
import os
import socket
import stat
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

_HOST = socket.gethostname()


def write_atomic(path: Path, data: bytes) -> None:
    """Replace `path` with `data`; a crash leaves the old file or the new one, never part of one.

    The staging file is named for this host and process, so a stop that leaves one behind is swept
    by this host's next write of the same path once its process is gone; another host's is left
    alone, since its liveness cannot be seen from here. A replaced file keeps its mode; a new one
    gets the umask's, as `write_bytes` would give it.
    """
    path = Path(path)
    _sweep_staging(path)
    staging = path.with_name(f".{path.name}.{_HOST}.{os.getpid()}.tmp")
    fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "wb") as handle:
            if path.exists():
                os.fchmod(handle.fileno(), stat.S_IMODE(path.stat().st_mode))
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    _sync_directory(path.parent)


def write_json_atomic(path: Path, record: Any, *, sort_keys: bool = False) -> None:
    """Write a JSON record with `write_atomic`, indented, ending in a newline."""
    write_atomic(path, (json.dumps(record, indent=2, sort_keys=sort_keys) + "\n").encode())


def read_json_object(path: Path) -> dict[str, Any] | None:
    """A JSON record as a dict, or None when it is missing, unreadable or not an object."""
    try:
        record = json.loads(Path(path).read_bytes())
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


@contextmanager
def exclusive(lock: Path) -> Iterator[None]:
    """Hold `lock` for one process: a second one is refused at once rather than interleaved.

    The lock file is kept apart from what it guards, so it survives `read_records` replacing a
    log and leaves nothing in the directory it protects.
    """
    lock = Path(lock)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(f"{lock} is held by another process; let it finish") from None
        yield


def open_log(path: Path) -> IO[str]:
    """Open an append-only log, making its entry (and any directory made for it) durable."""
    path = Path(path)
    created = [p for p in (path, *path.parents) if not p.exists()]
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a", encoding="utf-8")
    for entry in created:
        _sync_directory(entry.parent)
    return handle


def append_record(handle: IO[str], entry: Mapping[str, Any]) -> None:
    """Append one JSON line to a log, returning once it is on disk."""
    handle.write(json.dumps(entry) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def read_records(path: Path) -> list[Any]:
    """Every whole record in a JSON-lines log whose records each stand alone.

    A record is whole once its newline lands. A hard stop can leave the last one unfinished, and
    a log appended without fsync can hold zeros anywhere ext4 grew the file before writing its
    data. Each unreadable line is dropped and the log rewritten without it, so the next append
    starts a clean line. The caller makes a dropped record again, which is sound only because no
    record depends on another.
    """
    path = Path(path)
    data = path.read_bytes()
    body, _, tail = data.rpartition(b"\n")
    dropped = 1 if tail.strip() else 0
    kept: list[bytes] = []
    records: list[Any] = []
    for line in body.split(b"\n"):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            dropped += 1
            continue
        kept.append(line)
    if dropped:
        whole = b"".join(line + b"\n" for line in kept)
        print(
            f"{path}: dropped {dropped} unreadable record(s), {len(data) - len(whole)} bytes; "
            "each is made again",
            file=sys.stderr,
        )
        write_atomic(path, whole)
    return records


def _sweep_staging(path: Path) -> None:
    """Remove staging files a stopped process on this host left beside `path`."""
    prefix = f".{path.name}.{_HOST}."
    for stale in path.parent.glob(f"{glob.escape(prefix)}*.tmp"):
        pid = stale.name[len(prefix) : -len(".tmp")]
        # This process's own name is free to take: it holds no write of this path open.
        if pid.isdigit() and (int(pid) == os.getpid() or not _alive(int(pid))):
            stale.unlink(missing_ok=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _sync_directory(directory: Path) -> None:
    """Make an entry in `directory` durable, not only the file it names.

    Some mounts (9p, drvfs, CIFS) refuse fsync on a directory; the entry is then as durable as
    that filesystem makes it, and the write it follows has already succeeded.
    """
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError as error:
        if error.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EBADF):
            raise
    finally:
        os.close(fd)
