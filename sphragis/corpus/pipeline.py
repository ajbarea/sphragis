"""The dedup, split and freeze stages, over example dicts.

Each stage is a pure function plus a thin write. `freeze_windows` is the only one that
touches disk, and it refuses to overwrite: a frozen window is what results cite, so
replacing one silently would change the meaning of a number already reported.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sphragis.corpus.dedup import dedup
from sphragis.corpus.manifest import corpus_manifest
from sphragis.corpus.split import assign_windows, straddling_changes


def run_dedup(
    examples: Sequence[Mapping[str, Any]], **options: Any
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Three-stage dedup, returning what survived and what was removed by bucket."""
    return dedup(examples, **options)


def run_split(
    examples: Sequence[Mapping[str, Any]], bounds: Mapping[str, tuple[str, str]]
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Time windows, plus the straddling-change check that must always come back empty."""
    windows = assign_windows(examples, bounds)
    return windows, straddling_changes(windows)


def freeze_windows(
    root: Path,
    org: str,
    windows: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    stats: Mapping[str, Any],
) -> dict[str, Any]:
    """Write each window as JSONL and the manifest that pins their counts and hashes."""
    org_dir = Path(root) / org
    manifest_path = org_dir / "manifest.json"
    if manifest_path.exists():
        raise FileExistsError(
            f"{manifest_path} exists and a frozen window is what results cite; "
            "delete it deliberately if the corpus really is being rebuilt"
        )
    splits = org_dir / "splits"
    splits.mkdir(parents=True, exist_ok=True)
    for name, rows in windows.items():
        body = "".join(json.dumps(dict(row), sort_keys=True) + "\n" for row in rows)
        (splits / f"{name}.jsonl").write_text(body)

    manifest = corpus_manifest(
        org=org,
        windows={name: [str(r["id"]) for r in rows] for name, rows in windows.items()},
        stats=stats,
    )
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest
