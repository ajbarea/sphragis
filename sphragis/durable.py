"""Writes to the raw tier that survive a hard stop.

A snapshot is the reproducibility floor and its checkpoint is the only record of a month in
progress, and neither can be rebuilt from anything downstream. WSL can be stopped without
warning, and ext4 can then keep a file's new length while losing the data it grew for, leaving
zeros. A snapshot is written whole or not at all, and a checkpoint keeps every record whose
append returned.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any


def write_atomic(path: Path, data: bytes) -> None:
    """Replace `path` with `data`; a crash leaves the old file or the new one, never part of one."""
    path = Path(path)
    staging = path.with_name(f".{path.name}.tmp")
    try:
        with staging.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    _sync_directory(path.parent)


def append_record(handle: IO[str], entry: Mapping[str, Any]) -> None:
    """Append one JSON line to an append-only log, returning once it is on disk."""
    handle.write(json.dumps(entry) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def read_records(path: Path) -> list[Any]:
    """Every whole record in an append-only JSON-lines log, after cutting off a torn tail.

    A record is whole once its newline lands. A hard stop mid-append leaves the last record
    unfinished, or zeros where the file grew before its data was written; that tail is cut off,
    so the next append starts a clean line and the lost record is made again. An unreadable line
    with whole records after it is damage of another kind, and raises with the file untouched.
    """
    path = Path(path)
    data = path.read_bytes()
    lines = data.split(b"\n")
    records: list[Any] = []
    whole = 0
    unreadable: int | None = None
    offset = 0
    for number, line in enumerate(lines, 1):
        terminated = number < len(lines)
        offset += len(line) + terminated
        if not line.strip():
            continue
        try:
            if not terminated:
                raise ValueError("no newline")
            record = json.loads(line)
        except ValueError:
            unreadable = unreadable or number
            continue
        if unreadable is not None:
            raise ValueError(
                f"{path}: line {unreadable} is unreadable but whole records follow it, so it is "
                "not a torn append; the log is left as it is"
            )
        records.append(record)
        whole = offset
    if data[whole:].strip():
        print(
            f"{path}: cut {len(data) - whole} bytes of a torn last record; it is made again",
            file=sys.stderr,
        )
        with path.open("r+b") as handle:
            handle.truncate(whole)
            handle.flush()
            os.fsync(handle.fileno())
    return records


def _sync_directory(directory: Path) -> None:
    """Make a rename in `directory` durable, not only the renamed file's contents."""
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
