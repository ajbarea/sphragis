"""The stage bodies: dedup, split and freeze wired over real example dicts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sphragis.corpus.pipeline import freeze_windows, run_dedup, run_split

BOUNDS = {
    "pilot": ("2024-10-01", "2024-11-01"),
    "train": ("2024-11-01", "2025-09-01"),
    "dev": ("2025-09-01", "2025-11-01"),
    "test": ("2025-11-01", "2026-09-01"),
}


def _ex(i: int, change: str, created: str, before: str) -> dict[str, Any]:
    return {
        "id": f"openstack:{change}:f.py:1:{i}",
        "change_id": change,
        "created": created,
        "before": before,
        "after": before + " # fixed",
        "org": "openstack",
    }


EXAMPLES = [
    _ex(1, "I1", "2024-10-05", "alpha beta gamma delta"),
    _ex(2, "I1", "2024-10-05", "epsilon zeta eta theta"),
    _ex(3, "I2", "2024-12-05", "iota kappa lambda mu"),
    _ex(4, "I3", "2024-12-06", "alpha beta gamma delta"),
]


def test_dedup_reports_what_it_removed_and_why() -> None:
    kept, removed = run_dedup(EXAMPLES)
    assert len(kept) == 3, "the repeated before/after pair is an exact duplicate"
    assert removed["exact"] == 1
    assert set(removed) == {"exact", "near_duplicate", "boilerplate"}


def test_split_assigns_whole_changes_and_never_straddles() -> None:
    windows, problems = run_split(EXAMPLES, BOUNDS)
    assert problems == []
    assert {e["change_id"] for e in windows["pilot"]} == {"I1"}
    assert {e["change_id"] for e in windows["train"]} == {"I2", "I3"}


def test_split_raises_if_a_change_would_straddle() -> None:
    # Guarded by construction, so this asserts the guard rather than the behaviour.
    straddling = [_ex(1, "I9", "2024-10-31", "x"), _ex(2, "I9", "2024-11-02", "y")]
    windows, problems = run_split(straddling, BOUNDS)
    assert problems == []
    assert len(windows["pilot"]) == 2 and windows["train"] == []


def test_freeze_writes_a_manifest_and_the_window_files(tmp_path: Path) -> None:
    windows, _ = run_split(EXAMPLES, BOUNDS)
    manifest = freeze_windows(tmp_path, "openstack", windows, stats={"dropped": 7})
    assert (tmp_path / "openstack" / "manifest.json").exists()
    assert (tmp_path / "openstack" / "splits" / "pilot.jsonl").exists()
    assert manifest["counts"]["pilot"] == 2
    assert manifest["stats"]["dropped"] == 7


def test_freeze_output_verifies_against_itself(tmp_path: Path) -> None:
    from sphragis.corpus.manifest import verify

    windows, _ = run_split(EXAMPLES, BOUNDS)
    manifest = freeze_windows(tmp_path, "openstack", windows, stats={})
    ids = {name: [e["id"] for e in rows] for name, rows in windows.items()}
    assert verify(manifest, ids) == []


def test_freeze_refuses_to_overwrite_a_frozen_window(tmp_path: Path) -> None:
    windows, _ = run_split(EXAMPLES, BOUNDS)
    freeze_windows(tmp_path, "openstack", windows, stats={})
    with pytest.raises(FileExistsError, match="frozen"):
        freeze_windows(tmp_path, "openstack", windows, stats={})


def test_frozen_windows_are_jsonl_one_example_per_line(tmp_path: Path) -> None:
    windows, _ = run_split(EXAMPLES, BOUNDS)
    freeze_windows(tmp_path, "openstack", windows, stats={})
    lines = (tmp_path / "openstack" / "splits" / "train.jsonl").read_text().strip().split("\n")
    assert len(lines) == 2
    assert all(json.loads(line)["org"] == "openstack" for line in lines)
