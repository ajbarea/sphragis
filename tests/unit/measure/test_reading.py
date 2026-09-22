"""Controls for the tool that exists so a number is never quoted without its coordinates."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "reading.py"
_spec = importlib.util.spec_from_file_location("reading", _SCRIPT)
assert _spec and _spec.loader
reading = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reading)


def _artifact(tmp_path: Path) -> Path:
    report = {
        "readings": {
            estimand: {
                rule: {
                    "verdict": "mixed",
                    "per_org": {
                        "a": {"estimate": 0.03, "low": 0.004, "high": 0.056},
                        "b": {"estimate": -0.005, "low": -0.04, "high": 0.03},
                    },
                }
                for rule in ("crossed", "median_seed")
            }
            for estimand in ("pooled", "change_averaged")
        }
    }
    path = tmp_path / "run-seeds.json"
    path.write_text(json.dumps(report))
    return path


def test_the_registered_rule_is_the_one_the_study_reports() -> None:
    assert reading.REGISTERED == ("pooled", "crossed")


def test_every_row_carries_its_coordinates(tmp_path: Path) -> None:
    """The failure this tool exists for: an estimate printed without the rule it belongs to."""
    rows = reading.rows(_artifact(tmp_path), estimand=None, rule=None)
    assert len(rows) == 8, rows
    for run, estimand, rule, arm, estimate, span, _ in rows:
        assert run and estimand and rule and arm and estimate and span


def test_filtering_returns_only_the_cell_asked_for(tmp_path: Path) -> None:
    rows = reading.rows(_artifact(tmp_path), estimand="pooled", rule="crossed")
    assert {(row[1], row[2]) for row in rows} == {("pooled", "crossed")}
    assert len(rows) == 2


def test_a_filter_that_matches_nothing_is_refused_rather_than_silent(tmp_path: Path) -> None:
    """Printing nothing would read as 'no such effect' instead of 'no such cell'."""
    with pytest.raises(SystemExit, match="no reading matches"):
        reading.rows(_artifact(tmp_path), estimand="pooled", rule="nonexistent")


def test_a_single_seed_run_is_refused_rather_than_read_as_a_study(tmp_path: Path) -> None:
    path = tmp_path / "one.json"
    path.write_text(json.dumps({"results": {}, "seeds": [1]}))
    with pytest.raises(SystemExit, match="no `readings` block"):
        reading.rows(path, estimand=None, rule=None)


def test_the_committed_placebo_reads_the_way_the_log_reported_it() -> None:
    """Against the real artifact, not a fixture: the tool and the run must agree."""
    root = Path(__file__).resolve().parents[3]
    artifact = root / "datasets" / "results" / "rq1-placebo-qt-seeds.json"
    if not artifact.is_file():
        pytest.skip("the placebo study is not committed here")
    rows = reading.rows(artifact, estimand="pooled", rule="crossed")
    by_arm = {row[3]: row[4] for row in rows}
    assert by_arm["qt-a"] == "+0.0305"
