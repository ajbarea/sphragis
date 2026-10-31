"""`k_coverage.py --registered-runs`: who may take a K the registration fixes."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from sphragis.experiment.decomposition import COPRIMARY_METRIC, H2_RUNS
from sphragis.experiment.partitions import COVERAGE_NULL_TRIALS, spread_targets

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "k_coverage.py"

PILOT = {
    "org": "qt",
    "window": "development",
    "metric": COPRIMARY_METRIC,
    "levels": [0.975, 0.95],
    "per_run": [0.004, -0.003, 0.006, 0.001, -0.002, 0.005],
    "runs_left_out": [],
    "sizing": {"runs": 10, "sd_upper": 0.008},
}


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pilot: dict, runs: int) -> Path:
    simulation = {
        "org": "qt",
        "metric": pilot["metric"],
        "runs": runs,
        "null_trials": COVERAGE_NULL_TRIALS,
        "planned_changes": 1_000,
        "spread_targets": spread_targets(pilot),
        "by_target": {"sizing_bound_90": {"null_false_positive": {"0.975": 0.01, "0.95": 0.02}}},
        **{k: pilot[k] for k in ("hypothesis", "foreign_source") if k in pilot},
    }
    (tmp_path / "pilot.json").write_text(json.dumps(pilot))
    (tmp_path / "sim.json").write_text(json.dumps(simulation))
    out = tmp_path / "k-coverage-qt.json"
    spec = importlib.util.spec_from_file_location("k_coverage", SCRIPT)
    assert spec and spec.loader
    k_coverage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(k_coverage)
    argv = ["k_coverage.py", "--pilot", str(tmp_path / "pilot.json"), "--org", "qt"]
    argv += ["--grid", str(tmp_path / "sim.json"), "--registered-runs", str(runs)]
    monkeypatch.setattr(sys, "argv", [*argv, "--out", str(out)])
    k_coverage.main()
    return out


def test_the_coprimary_score_takes_its_cells_k(tmp_path: Path, monkeypatch) -> None:
    out = json.loads(_run(tmp_path, monkeypatch, PILOT, 20).read_text())
    assert (out["runs"], out["registered_runs"], out["calibrated"]) == (20, 20, False)
    assert out["metric"] == COPRIMARY_METRIC


def test_the_whole_score_searches_the_grid(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(SystemExit, match="registered only for H2"):
        _run(tmp_path, monkeypatch, {**PILOT, "metric": "logprob_per_token"}, 20)


def test_h2_takes_its_registered_k_alone(tmp_path: Path, monkeypatch) -> None:
    h2 = {**PILOT, "metric": "logprob_per_token", "hypothesis": "H2"}
    h2 |= {"foreign_source": "chromium"}
    with pytest.raises(SystemExit, match=f"registered only for H2, at {H2_RUNS}"):
        _run(tmp_path, monkeypatch, h2, 35)


def test_h2_at_its_registered_k_is_read(tmp_path: Path, monkeypatch) -> None:
    h2 = {**PILOT, "metric": "logprob_per_token", "hypothesis": "H2"}
    h2 |= {"foreign_source": "chromium"}
    out = json.loads(_run(tmp_path, monkeypatch, h2, H2_RUNS).read_text())
    assert (out["runs"], out["hypothesis"], out["foreign_source"]) == (H2_RUNS, "H2", "chromium")
