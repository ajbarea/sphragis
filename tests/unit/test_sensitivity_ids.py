"""scripts/sensitivity_ids.py: the examples each registered sensitivity of H1 removes."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "sensitivity_ids", ROOT / "scripts" / "sensitivity_ids.py"
)
assert _spec is not None and _spec.loader is not None
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

TRAILERS = {
    "since": "2024-10-01",
    "merged_before": "2025-11-01",
    "orgs": {"openstack": {"changes_ai": ["openstack/nova I1", "x/y z I2"]}},
}


def test_the_ai_flag_reads_project_and_change_id_from_the_trailer_artifact() -> None:
    assert script.flagged_changes(TRAILERS, "openstack") == {
        ("openstack/nova", "I1"),
        ("x/y z", "I2"),
    }


def test_the_trailer_searches_must_span_the_window() -> None:
    assert script.covers(TRAILERS, "dev")
    # Searched up to the test window's start, so no test-window change was ever flagged.
    assert not script.covers(TRAILERS, "test")


def _run(argv: list[str]) -> None:
    saved = sys.argv
    sys.argv = ["sensitivity_ids", *argv]
    try:
        script.main()
    finally:
        sys.argv = saved


def test_the_sealed_window_and_an_uncovered_window_are_refused(tmp_path: Path) -> None:
    trailers = tmp_path / "trailers.json"
    trailers.write_text(json.dumps(TRAILERS))
    argv = ["--root", str(tmp_path), "--org", "openstack", "--ai-trailers", str(trailers)]
    with pytest.raises(SystemExit, match="test window is sealed"):
        _run([*argv, "--window", "test", "--out", str(tmp_path / "o.json")])
    (tmp_path / "openstack").mkdir()
    (tmp_path / "openstack" / "seal.json").write_text(json.dumps({"accepted_at": "2027-02-01"}))
    with pytest.raises(SystemExit, match="stops at the seal"):
        _run([*argv, "--window", "test", "--out", str(tmp_path / "o.json")])
