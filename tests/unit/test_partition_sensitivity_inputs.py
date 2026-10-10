"""The H1 simulation's input guards: decoders, metrics, SESOI and K, and its continuous trial."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

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


def _module() -> ModuleType:
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("partition_sensitivity", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _likelihood(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    """The registered likelihood simulation's committed inputs, with `extra` arguments."""
    results = ROOT / "datasets/results"
    argv = ["--placebo", str(results / "likelihood-placebo-wikimedia-rp1.0.json")]
    argv += ["--corpus", "datasets/gerrit", "--org", "wikimedia"]
    argv += ["--pilot", str(results / "partition-pilot-wikimedia-likelihood.json")]
    argv += ["--projection", str(results / "project-windows-wikimedia-v3.json")]
    argv += ["--admissible", str(results / "admissible-partitions-wikimedia.json")]
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv, *extra, "--out", str(tmp_path / "o.json")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--metric", "logprob_per_token", "--redraw", "0.05"], "--redraw is binary churn"),
        (["--metric", "exact_match"], "the pilot is scored on logprob_per_token"),
        (["--metric", "logprob"], "invalid choice"),
        (["--metric", "logprob_per_token", "--runs", "45"], "outside"),
    ],
)
def test_a_likelihood_simulation_refuses_inputs_it_cannot_read(
    tmp_path: Path, extra: list[str], message: str
) -> None:
    result = _likelihood(tmp_path, *extra)
    assert result.returncode != 0
    assert message in result.stderr
    assert not (tmp_path / "o.json").exists()


def test_a_continuous_pool_needs_the_pilot(tmp_path: Path) -> None:
    partition_sensitivity = _module()
    (tmp_path / "a.json").write_text("{}")
    with pytest.raises(SystemExit, match="no pilot was given"):
        partition_sensitivity.pools(
            tmp_path / "placebo.json",
            tmp_path,
            "wikimedia",
            tmp_path / "a.json",
            80,
            100,
            "logprob_per_token",
            None,
        )


def test_each_metric_sets_its_workers_continuity_and_sesoi(monkeypatch: pytest.MonkeyPatch) -> None:
    partition_sensitivity = _module()
    from sphragis.experiment.decomposition import SESOI
    from sphragis.experiment.likelihood import metric_sesoi

    monkeypatch.chdir(ROOT)
    assert partition_sensitivity.worker_args([], "exact_match", (0.0, 0.0)) == (
        [],
        False,
        SESOI,
        (0.0, 0.0),
    )
    registered = metric_sesoi("logprob_per_token")
    assert registered is not None and registered != SESOI
    assert partition_sensitivity.worker_args([], "logprob_per_token", (0.02, 0.13)) == (
        [],
        True,
        registered,
        (0.02, 0.13),
    )
    with pytest.raises(SystemExit, match="no registered SESOI"):
        partition_sensitivity.worker_args([], "logprob", (0.0, 0.0))


def test_a_continuous_trial_shifts_by_its_lift_and_reads_against_its_sesoi() -> None:
    """With no noise and equal arms, the null estimate is 0 and a lift's is the lift itself."""
    partition_sensitivity = _module()
    from sphragis.experiment.partitions import K_MAX
    from sphragis.measure.stats import Cluster

    projects = [f"p{i}" for i in range(4)]
    pool = [(Cluster(f"c{i}", (-0.5, -0.7), (-0.5, -0.7)), projects[i % 4]) for i in range(40)]
    sides = [(0, 0, 1, 1), (0, 1, 0, 1), (0, 1, 1, 0), (1, 0, 0, 1)]
    partitions = [dict(zip(projects, sides[i % 4], strict=True)) for i in range(2 * K_MAX)]
    sesoi = 0.02
    partition_sensitivity._init(partitions, True, sesoi, (0.0, 0.0))
    try:
        levels = [0.975]
        null = partition_sensitivity.trial((pool, 40, 0.0, 0.0, 0.0, 200, 3, levels, 10, True))
        lifted = partition_sensitivity.trial((pool, 40, 0.05, 0.0, 0.0, 200, 3, levels, 10, False))
    finally:
        partition_sensitivity._init([])
    assert null["estimate"] == pytest.approx(0.0, abs=1e-12)
    assert null["absent"][0.975] and not null["supported"][0.975] and not null["disagree"]
    assert lifted["estimate"] == pytest.approx(0.05)
    assert lifted["supported"][0.975] and not lifted["absent"][0.975]


def test_a_continuous_null_reads_absent_against_the_sesoi_its_workers_were_given() -> None:
    """A noisy null whose interval is wider than exact match's SESOI and narrower than 0.5."""
    partition_sensitivity = _module()
    from sphragis.experiment.partitions import K_MAX
    from sphragis.measure.stats import Cluster

    projects = [f"p{i}" for i in range(4)]
    pool = [(Cluster(f"c{i}", (-0.5,), (-0.5,)), projects[i % 4]) for i in range(40)]
    sides = [(0, 0, 1, 1), (0, 1, 0, 1), (0, 1, 1, 0), (1, 0, 0, 1)]
    partitions = [dict(zip(projects, sides[i % 4], strict=True)) for i in range(2 * K_MAX)]
    job = (pool, 40, 0.0, 0.0, 0.0, 200, 3, [0.975], 10, False)
    read = {}
    try:
        for sesoi in (0.5, 0.001):
            partition_sensitivity._init(partitions, True, sesoi, (0.0, 0.2))
            read[sesoi] = partition_sensitivity.trial(job)
    finally:
        partition_sensitivity._init([])
    low = read[0.5]["estimate"] - (read[0.5]["high"][0.975] - read[0.5]["estimate"])
    assert read[0.5]["high"][0.975] - low > 2 * 0.01
    assert read[0.5]["absent"][0.975] and not read[0.001]["absent"][0.975]
