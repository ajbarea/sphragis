"""Corpus manifest: per-window counts and content hashes, and their verification."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sphragis.provenance import provenance_header


def window_hash(example_ids: Iterable[str]) -> str:
    """Content hash over a window, independent of the order ids arrive in."""
    digest = hashlib.sha256()
    for example_id in sorted(example_ids):
        digest.update(example_id.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def corpus_manifest(
    *, org: str, windows: Mapping[str, Sequence[str]], stats: Mapping[str, Any]
) -> dict[str, Any]:
    """Capture counts, hashes and provenance for one organization's corpus."""
    return {
        **provenance_header(),
        "org": org,
        "counts": {name: len(ids) for name, ids in windows.items()},
        "hashes": {name: window_hash(ids) for name, ids in windows.items()},
        "stats": dict(stats),
    }


def verify(manifest: Mapping[str, Any], windows: Mapping[str, Sequence[str]]) -> list[str]:
    """Describe every way ``windows`` disagrees with ``manifest``; empty means clean."""
    problems: list[str] = []
    for name, ids in windows.items():
        expected_count = manifest["counts"].get(name)
        expected_hash = manifest["hashes"].get(name)
        if expected_hash is None:
            problems.append(f"{name}: absent from the manifest")
            continue
        actual_hash = window_hash(ids)
        if expected_count != len(ids) or expected_hash != actual_hash:
            problems.append(
                f"{name}: manifest says {expected_count} items ({expected_hash[:12]}), "
                f"found {len(ids)} ({actual_hash[:12]})"
            )
    return problems
