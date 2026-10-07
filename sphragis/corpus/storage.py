"""Immutable raw snapshots.

Experiments replay from these rather than from a live server. Gerrit changes can be
edited or deleted upstream, so a rerun months later would not reproduce; the snapshot is
the reproducibility floor. Nothing downstream may re-fetch.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from sphragis.durable import read_json_object, write_atomic, write_json_atomic
from sphragis.provenance import provenance_header

GZIP_MAGIC = b"\x1f\x8b"


def snapshot_path(root: Path, org: str, month: str) -> Path:
    """Where one organization-month of raw changes lives."""
    return Path(root) / org / "raw" / f"{month}.ndjson.gz"


def snapshot_record_path(snapshot: Path) -> Path:
    """Where a snapshot's record of how it was fetched lives."""
    return snapshot.with_suffix("").with_suffix(".record.json")


def read_snapshot_record(snapshot: Path) -> dict[str, Any] | None:
    """A snapshot's record as a dict, or None when it is missing, unreadable or not an object."""
    return read_json_object(snapshot_record_path(Path(snapshot)))


def unfinished_snapshot(snapshot: Path) -> bool:
    """Whether a snapshot's record says its write did not finish.

    The record is the completion marker, as a build's is: written open before the snapshot and
    closed after it. Only an open record says so. A missing or unreadable record, or one from
    before the marker (no `complete` key), says nothing, so it never licenses replacing a
    snapshot without `overwrite`.
    """
    record = read_snapshot_record(snapshot)
    return record is not None and not record.get("complete", True)


def write_snapshot(
    root: Path,
    org: str,
    month: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    record: Mapping[str, Any],
    overwrite: bool = False,
) -> Path:
    """Write one organization-month as gzipped NDJSON, plus the record of how it was got.

    A month whose write was stopped partway is replaced without `overwrite`: nothing in it was
    finished, so nothing immutable is lost.
    """
    path = snapshot_path(root, org, month)
    if path.exists() and not overwrite and not unfinished_snapshot(path):
        raise FileExistsError(
            f"{path} exists and raw snapshots are immutable; pass overwrite=True to replace it"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    header = {**provenance_header(), **dict(record), "rows": len(rows)}
    # Each file lands whole. A stop before the closing record leaves the month unfinished,
    # whichever snapshot is on disk, so a new record never vouches for an old snapshot.
    write_json_atomic(snapshot_record_path(path), {**header, "complete": False})
    write_atomic(path, gzip.compress(body.encode()))
    write_json_atomic(snapshot_record_path(path), {**header, "complete": True})
    return path


def refused_snapshot(path: Path) -> str | None:
    """Why a snapshot on disk cannot be read as a fetched month, or None when it can."""
    if unfinished_snapshot(path):
        return "its fetch did not finish; fetch the month again"
    with Path(path).open("rb") as handle:
        head = handle.read(2)
    if not head:
        return "empty, so its write never landed; fetch the month again"
    if head != GZIP_MAGIC:
        # Zeros where a write's data never landed, on a snapshot from before atomic writes.
        return "not a gzip stream, so its write never landed; fetch the month again"
    return None


def iter_snapshot(path: Path) -> Iterator[dict[str, Any]]:
    """A snapshot's rows one at a time, refusing one whose write did not finish.

    Every reader of raw rows comes through here. An empty file is refused too: a snapshot of no
    rows is still a gzip stream, so an empty one is a write that never landed.
    """
    reason = refused_snapshot(path)
    if reason:
        raise ValueError(f"{path}: {reason}")
    with gzip.open(path, "rt") as lines:
        for line in lines:
            if line.strip():
                yield json.loads(line)


def read_snapshot(path: Path) -> list[dict[str, Any]]:
    """Read a snapshot back, refusing one whose write did not finish."""
    return list(iter_snapshot(path))
