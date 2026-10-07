"""Immutable raw snapshots.

Experiments replay from these rather than from a live server. Gerrit changes can be
edited or deleted upstream, so a rerun months later would not reproduce; the snapshot is
the reproducibility floor. Nothing downstream may re-fetch.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sphragis.durable import write_atomic
from sphragis.provenance import provenance_header


def snapshot_path(root: Path, org: str, month: str) -> Path:
    """Where one organization-month of raw changes lives."""
    return Path(root) / org / "raw" / f"{month}.ndjson.gz"


def snapshot_record_path(snapshot: Path) -> Path:
    """Where a snapshot's record of how it was fetched lives."""
    return snapshot.with_suffix("").with_suffix(".record.json")


def unfinished_snapshot(snapshot: Path) -> bool:
    """Whether a snapshot's record marks its write unfinished, or cannot be read.

    The record is the completion marker, as a build's is: written open before the snapshot and
    closed after it. A missing record, or one from before the marker (no `complete` key), says
    nothing either way.
    """
    record_path = snapshot_record_path(Path(snapshot))
    if not record_path.is_file():
        return False
    try:
        record = json.loads(record_path.read_bytes())
    except ValueError:
        return True
    return not isinstance(record, dict) or not record.get("complete", True)


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
    write_atomic(snapshot_record_path(path), _record_bytes({**header, "complete": False}))
    write_atomic(path, gzip.compress(body.encode()))
    write_atomic(snapshot_record_path(path), _record_bytes({**header, "complete": True}))
    return path


def read_snapshot(path: Path) -> list[dict[str, Any]]:
    """Read a snapshot back, refusing one whose write did not finish."""
    if unfinished_snapshot(path):
        raise ValueError(f"{path}: its fetch did not finish; fetch the month again")
    text = gzip.decompress(Path(path).read_bytes()).decode()
    return [json.loads(line) for line in text.splitlines() if line]


def _record_bytes(record: Mapping[str, Any]) -> bytes:
    return json.dumps(record, indent=2).encode()
