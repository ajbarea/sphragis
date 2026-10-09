"""The H1 simulation refuses a pilot and a placebo run that came from different decoders."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "partition_sensitivity.py"
RUN = {"model_id": "m", "max_new_tokens": 256, "inference_dtype": ["float32"]}


ROOT = Path(__file__).resolve().parents[2]


def _simulate(tmp_path: Path, pilot: dict, placebo: dict) -> subprocess.CompletedProcess[str]:
    (tmp_path / "pilot.json").write_text(json.dumps(pilot))
    (tmp_path / "placebo.json").write_text(json.dumps(placebo))
    argv = ["--placebo", "placebo.json", "--corpus", "c", "--org", "openstack"]
    argv += ["--pilot", "pilot.json", "--projection", "p.json", "--admissible", "a.json"]
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv, "--out", "o.json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_penalty_one_pilot_with_an_unrecorded_penalty_placebo_is_refused(tmp_path: Path) -> None:
    """A run that records no penalty decoded at the checkpoint's 1.1."""
    (tmp_path / "run1.json").write_text(json.dumps({**RUN, "repetition_penalty": [1.0]}))
    result = _simulate(tmp_path, {"run_files": ["run1.json"]}, RUN)
    assert result.returncode != 0
    assert "decoded as" in result.stderr


def test_a_pilot_recording_its_decoder_is_read_without_its_runs(tmp_path: Path) -> None:
    """Past the decoder check, the next input the test leaves out is the admissible list."""
    # As partition_pilot.py writes it: keys sorted.
    pilot = json.loads((ROOT / "datasets/results/partition-pilot-wikimedia-rp1.0.json").read_text())
    result = _simulate(tmp_path, pilot, {**RUN, "repetition_penalty": [1.0]})
    assert result.returncode != 0
    assert "decoded as" not in result.stderr
    assert "a.json" in result.stderr
