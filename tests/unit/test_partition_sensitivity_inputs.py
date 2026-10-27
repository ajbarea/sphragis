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


def test_a_likelihood_simulation_reads_no_placebo(tmp_path: Path) -> None:
    """Past every placebo check without one: the next input it fails on is the corpus."""
    results = ROOT / "datasets/results"
    argv = ["--corpus", str(tmp_path / "no-corpus"), "--org", "wikimedia"]
    argv += ["--pilot", str(results / "partition-pilot-wikimedia-likelihood.json")]
    argv += ["--projection", str(results / "project-windows-wikimedia-v3.json")]
    argv += ["--admissible", str(results / "admissible-partitions-wikimedia.json")]
    argv += ["--metric", "logprob_per_token", "--out", str(tmp_path / "o.json")]
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *argv], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "placebo" not in result.stderr
    assert "corpus not current" in result.stderr
    assert not (tmp_path / "o.json").exists()


def test_exact_match_without_a_placebo_is_refused(tmp_path: Path) -> None:
    pilot = json.loads((ROOT / "datasets/results/partition-pilot-wikimedia-rp1.0.json").read_text())
    (tmp_path / "pilot.json").write_text(json.dumps(pilot))
    argv = ["--corpus", "c", "--org", "wikimedia", "--pilot", "pilot.json"]
    argv += ["--projection", str(ROOT / "datasets/results/project-windows-wikimedia-v3.json")]
    argv += ["--admissible", str(ROOT / "datasets/results/admissible-partitions-wikimedia.json")]
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *argv, "--out", "o.json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "no placebo was given" in result.stderr


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
    from sphragis.experiment.decomposition import SESOI, metric_sesoi

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


def _h2_run(tmp_path: Path) -> Path:
    """One run of qt's halves with each half's matched foreign arm: change c0 carries a C++
    example, c1 only Python, c2 both."""
    rows = [
        {"id": "qt:c0:a.cpp:1:1", "change_id": "c0", "lp": -0.5},
        {"id": "qt:c1:b.py:1:1", "change_id": "c1", "lp": -0.5},
        {"id": "qt:c2:c.h:1:1", "change_id": "c2", "lp": -0.5},
        {"id": "qt:c2:d.py:1:1", "change_id": "c2", "lp": -0.5},
    ]
    results = {}
    for window, sibling, part in (("qt-a", "qt-b", rows[:2]), ("qt-b", "qt-a", rows[2:])):
        for trained in ("qt-a", "qt-b", f"foreign-aosp-for-{sibling}"):
            results[f"adapter:{trained}|{window}|s1"] = part
    path = tmp_path / "run.json"
    path.write_text(
        json.dumps({"seeds": [1], "corpora": {"qt-a": {}, "qt-b": {}}, "results": results})
    )
    return path


def test_an_h2_pilot_is_simulated_on_sibling_against_matched_foreign_on_cpp() -> None:
    from sphragis.experiment.decomposition import is_cpp
    from sphragis.experiment.power import cell_contrast, own_against_sibling

    assert cell_contrast({}) == (own_against_sibling, None)
    arms, keep = cell_contrast({"hypothesis": "H2", "foreign_source": "aosp"})
    assert arms("qt-a", "qt-b", 3) == (
        "adapter:qt-b|qt-a|s3",
        "adapter:foreign-aosp-for-qt-b|qt-a|s3",
    )
    assert keep is is_cpp
    for cell in ({"hypothesis": "H2"}, {"hypothesis": "H2", "foreign_source": "gitlab"}):
        with pytest.raises(ValueError, match="no cell is H2"):
            cell_contrast(cell)
    with pytest.raises(ValueError, match="no cell is H1 against 'aosp'"):
        cell_contrast({"foreign_source": "aosp"})


def test_an_h2_plan_reads_the_pilots_cpp_share_by_change(tmp_path: Path) -> None:
    partition_sensitivity = _module()
    pilot = {"run_files": [str(_h2_run(tmp_path))]}
    assert partition_sensitivity.cpp_change_share(pilot, "qt") == (2, 3)
    with pytest.raises(ValueError, match="scored no adapter:wikimedia-a"):
        partition_sensitivity.cpp_change_share(pilot, "wikimedia")


H2_PILOT = {"hypothesis": "H2", "foreign_source": "aosp"}
PLAN = {"org": "qt", "cells": None, "metric": "logprob_per_token", "placebo": None}


@pytest.mark.parametrize(
    ("pilot", "plan", "message"),
    [
        ({}, {"foreign_source": "aosp"}, "H1's cell against None, not against aosp"),
        (H2_PILOT, {"foreign_source": None}, "H2's cell against aosp, not against None"),
        (H2_PILOT, {"cells": [1, 2, 3]}, "H2 is one cell"),
        (H2_PILOT, {"cells": [2]}, "H2 is one cell"),
        (H2_PILOT, {"metric": "exact_match"}, "continuous metric"),
        (H2_PILOT, {"placebo": Path("placebo.json")}, "no placebo"),
        (H2_PILOT, {"projected": 29}, "19 planned C"),
        ({}, {"foreign_source": None, "cells": [0]}, "at least one"),
    ],
)
def test_a_plan_is_refused_for_another_cell_or_inputs_it_does_not_read(
    tmp_path: Path, pilot: dict, plan: dict, message: str
) -> None:
    partition_sensitivity = _module()
    pilot = {**pilot, "run_files": [str(_h2_run(tmp_path))]}
    args = {**PLAN, "foreign_source": "aosp", "projected": 300.0, **plan}
    with pytest.raises(ValueError, match=message):
        partition_sensitivity.plan_cell(pilot, **args)


def test_a_plan_reads_h1_at_every_cell_count_and_h2_at_one_on_its_cpp_share(
    tmp_path: Path,
) -> None:
    partition_sensitivity = _module()
    run = [str(_h2_run(tmp_path))]
    h1 = partition_sensitivity.plan_cell(
        {"run_files": run}, **{**PLAN, "foreign_source": None, "projected": 300.9}
    )
    assert h1 == ([1, 2, 3], 300, None)
    h2 = {**H2_PILOT, "run_files": run}
    plan = {**PLAN, "foreign_source": "aosp", "projected": 300.0}
    # Two of the pilot's three changes carry a C++ example: 200 of the 300 projected.
    assert partition_sensitivity.plan_cell(h2, **plan) == (
        [1],
        200,
        {"cpp_changes": 2, "changes": 3},
    )
    assert partition_sensitivity.plan_cell(h2, **{**plan, "cells": [1]})[0] == [1]


def test_the_simulation_names_the_cell_it_simulates(tmp_path) -> None:
    sbatch = (ROOT / "scripts" / "partition_sensitivity.sbatch").read_text()
    assert 'FOREIGN_TAG="${FOREIGN_SOURCE:+-foreign$FOREIGN_SOURCE}"' in sbatch
    assert 'RESULT_SUFFIX="$RESULT_SUFFIX$FOREIGN_TAG"' in sbatch
    assert "partition-pilot-$ORG$FOREIGN_TAG$PILOT_TAG.json" in sbatch


def test_chains_are_drawn_only_from_a_pilot_read_under_chains(tmp_path: Path) -> None:
    """The committed pilot was read by change: asking to draw chains from it is refused."""
    result = _likelihood(
        tmp_path, "--metric", "logprob_per_token", "--chains", str(tmp_path / "chains.json")
    )
    assert result.returncode != 0
    assert "the pilot was read by change; --chains must match it" in result.stderr


def test_a_pilot_read_under_chains_is_not_simulated_by_change(tmp_path: Path) -> None:
    pilot = json.loads(
        (ROOT / "datasets/results/partition-pilot-wikimedia-likelihood.json").read_text()
    )
    pilot["cluster_unit"] = {"unit": "chain", "file": "c.json", "sha256": "0" * 64}
    result = _from_pilot(tmp_path, pilot)
    assert result.returncode != 0
    assert "the pilot was read by chain; --chains must match it" in result.stderr


def test_chains_are_drawn_only_from_the_file_the_pilot_read(tmp_path: Path) -> None:
    """The path alone would pass a chains file regenerated since the pilot read it."""
    chains = tmp_path / "chains.json"
    train = {"clusters_by_change": 1, "clusters_by_chain": 1}
    chains.write_text(
        json.dumps({"org": "wikimedia", "change_chain": {}, "windows": {"train": train}})
    )
    pilot = json.loads(
        (ROOT / "datasets/results/partition-pilot-wikimedia-likelihood.json").read_text()
    )
    pilot["cluster_unit"] = {"unit": "chain", "file": str(chains), "sha256": "0" * 64}
    result = _from_pilot(tmp_path, pilot, "--chains", str(chains))
    assert result.returncode != 0
    assert f"{chains} is not the chains file the pilot read" in result.stderr


def _from_pilot(tmp_path: Path, pilot: dict, *extra: str) -> subprocess.CompletedProcess:
    (tmp_path / "pilot.json").write_text(json.dumps(pilot))
    results = ROOT / "datasets/results"
    argv = ["--corpus", "datasets/gerrit", "--org", "wikimedia", "--metric", "logprob_per_token"]
    argv += ["--pilot", str(tmp_path / "pilot.json")]
    argv += ["--projection", str(results / "project-windows-wikimedia-v3.json")]
    argv += ["--admissible", str(results / "admissible-partitions-wikimedia.json")]
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv, *extra, "--out", str(tmp_path / "o.json")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_chain_pool_takes_each_chains_one_project(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    rows = [
        {"change_id": "I1", "project": "p"},
        {"change_id": "I2", "project": "p"},
        {"change_id": "I3", "project": "q"},
    ]
    monkeypatch.setattr(module, "refined_examples", lambda corpus, org: rows)
    from sphragis.measure.stats import Cluster

    clusters = [Cluster("chain-1", (1.0,), (0.0,)), Cluster("chain-3", (1.0,), (0.0,))]
    chain_of = {"I1": "chain-1", "I2": "chain-1", "I3": "chain-3"}
    pool = module.with_projects(clusters, Path("c"), "openstack", chain_of)
    assert [project for _, project in pool] == ["p", "q"]
    chain_of["I3"] = "chain-1"  # a chain spanning two projects has no one project
    with pytest.raises(SystemExit, match="maps to projects"):
        module.with_projects(clusters[:1], Path("c"), "openstack", chain_of)
