"""Writes to the raw tier that survive a hard stop.

A snapshot is the reproducibility floor and its checkpoint is the only record of a month in
progress, and neither can be rebuilt from anything downstream. WSL can be stopped without
warning, and ext4 can then keep a file's new length while losing the data it grew for, leaving
zeros. A file written here lands whole or not at all, and a log keeps every record whose append
returned.
"""

from __future__ import annotations

import errno
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any


def write_atomic(path: Path, data: bytes) -> None:
    """Replace `path` with `data`; a crash leaves the old file or the new one, never part of one."""
    path = Path(path)
    fd, staging = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
    except BaseException:
        Path(staging).unlink(missing_ok=True)
        raise
    _sync_directory(path.parent)


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
