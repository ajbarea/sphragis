"""Controls for the tool that exists so a number is never quoted without its coordinates."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from sphragis.experiment.cells import registered_plant

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
    assert artifact.is_file(), f"{artifact} is committed; the log's figures are read from it"
    rows = reading.rows(artifact, estimand="pooled", rule="crossed")
    by_arm = {row[3]: row[4] for row in rows}
    assert by_arm["qt-a"] == "+0.0305"


def _planted(tmp_path: Path) -> Path:
    def run(partition: int | None, verdict: str, estimate: float) -> dict:
        return {
            "partition": partition,
            "passed": verdict == "pass",
            "verdict": verdict,
            "projected_pass": 0.5,
            "halves": {
                "qt-a": {"estimate": estimate, "low": estimate - 0.02, "high": estimate + 0.02},
                "qt-b": {"estimate": 0.05, "low": 0.03, "high": 0.07},
            },
            "plant": {"qt-a": {"realised_fraction": 0.0007}, "qt-b": {}},
        }

    report = {
        "kind": "planted_check",
        "org": "qt",
        "convention": "code",
        "fraction": 1.0,
        "runs": 3,
        "metric": "logprob_per_token",
        "projected_pass_min": 0.3535,
        "projected_floor": 0.95,
        "rule_met": False,
        "registered_plant": ["marker", 0.5],
        "by_run": [run(13, "mixed", -0.004), run(4, "pass", 0.02), run(None, "pass", 0.03)],
    }
    path = tmp_path / "planted-check-qt-code1.0.json"
    path.write_text(json.dumps(report))
    return path


def test_a_check_5_artifact_reads_by_partition_and_half(tmp_path: Path) -> None:
    rows = reading.planted_rows(_planted(tmp_path))
    assert [row[:4] for row in rows[:2]] == [
        ("p4", "pass", "0.5000", "qt-a"),
        ("p4", "pass", "0.5000", "qt-b"),
    ]
    assert rows[2] == ("p13", "mixed", "0.5000", "qt-a", "-0.0040", "[-0.0240, +0.0160]", "0.0007")
    # A half the manifest gives no share is printed blank, not as zero.
    assert rows[3][-1] == ""
    # A run with no partition sorts last rather than failing.
    assert rows[-1][0] == "pNone"


def test_a_check_5_summary_says_whether_the_rule_held_and_the_plant_is_registered(
    tmp_path: Path,
) -> None:
    summary = reading.planted_summary(_planted(tmp_path))
    assert "2 of 3 pass on logprob_per_token" in summary
    assert "min 0.3535 (floor 0.95)" in summary
    assert summary.endswith("fails item 4's rule, not the registered plant")


def test_a_check_5_summary_of_an_unread_organization_says_none_is_registered(
    tmp_path: Path,
) -> None:
    path = _planted(tmp_path)
    report = json.loads(path.read_text())
    path.write_text(json.dumps({**report, "registered_plant": None}))
    assert reading.planted_summary(path).endswith("no plant registered yet")


def test_check_5_and_seed_artifacts_are_not_read_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "sys.argv", ["reading.py", str(_planted(tmp_path)), str(_artifact(tmp_path))]
    )
    with pytest.raises(SystemExit, match="pass one kind"):
        reading.main()


@pytest.mark.parametrize(
    "name", ["openstack-code1.0", "wikimedia-code1.0", "qt-code1.0", "qt-marker0.5"]
)
def test_the_committed_check_5_reads_the_way_its_rule_does(name: str) -> None:
    path = _SCRIPT.parents[1] / "datasets" / "results" / f"planted-check-{name}.json"
    report = json.loads(path.read_text())
    passed = sum(1 for run in report["by_run"] if run["passed"])
    rule = passed == report["runs"] == 10 and report["projected_pass_min"] >= 0.95
    assert report["rule_met"] is rule
    assert f"{'meets' if rule else 'fails'} item 4's rule" in reading.planted_summary(path)
    # Each organization's registered plant met the rule; Qt's code plant, a candidate, did not.
    # Read against the code, so a change to `cells.PLANT_BY_ORG` leaves no artifact stale unseen.
    assert report["registered_plant"] == list(registered_plant(report["org"]))
    registered = [report["convention"], report["fraction"]] == report["registered_plant"]
    assert registered is (name != "qt-code1.0") and (not registered or rule)
