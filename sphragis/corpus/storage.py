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

from sphragis.provenance import provenance_header


def snapshot_path(root: Path, org: str, month: str) -> Path:
    """Where one organization-month of raw changes lives."""
    return Path(root) / org / "raw" / f"{month}.ndjson.gz"


def snapshot_record_path(snapshot: Path) -> Path:
    """Where a snapshot's record of how it was fetched lives."""
    return snapshot.with_suffix("").with_suffix(".record.json")


def write_snapshot(
    root: Path,
    org: str,
    month: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    record: Mapping[str, Any],
    overwrite: bool = False,
) -> Path:
    """Write one organization-month as gzipped NDJSON, plus the record of how it was got."""
    path = snapshot_path(root, org, month)
    if path.exists() and not overwrite:
        raise FileExistsError(
            f"{path} exists and raw snapshots are immutable; pass overwrite=True to replace it"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    path.write_bytes(gzip.compress(body.encode()))
    snapshot_record_path(path).write_text(
        json.dumps({**provenance_header(), **dict(record), "rows": len(rows)}, indent=2)
    )
    return path


def read_snapshot(path: Path) -> list[dict[str, Any]]:
    """Read a snapshot back."""
    text = gzip.decompress(Path(path).read_bytes()).decode()
    return [json.loads(line) for line in text.splitlines() if line]
