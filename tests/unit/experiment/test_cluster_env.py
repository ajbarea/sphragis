"""The job environment every sbatch script sources, and the scripts' agreement with it.

TIGRIS is aarch64 and SPORC x86_64, and the two mount one `$HOME`. A job that picks up the
other machine's uv or venv dies with `Exec format error` after the queue wait, so these run
the real file under bash rather than reading it.
"""

from __future__ import annotations

import platform
import re
import subprocess
from pathlib import Path

import pytest

from sphragis.experiment.slurm import DEFAULT_TARGET, TARGETS, SlurmJob, render

_ROOT = Path(__file__).resolve().parents[3]
_ENV = _ROOT / "sphragis" / "experiment" / "cluster-env.sh"
_SCRIPTS = sorted((_ROOT / "scripts").glob("*.sbatch"))
_MACHINE = platform.machine()


def _source(home: Path, workdir: Path, probe: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{_ENV}"; {probe}'],
        capture_output=True,
        text=True,
        cwd=workdir,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
    )


def _fake_uv(directory: Path) -> None:
    directory.mkdir(parents=True)
    uv = directory / "uv"
    uv.write_text("#!/bin/sh\necho uv-for-this-machine\n")
    uv.chmod(0o755)


def _fake_venv(workdir: Path) -> None:
    python = workdir / f".venv-{_MACHINE}" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)


def test_the_venv_is_named_for_the_machine_the_job_landed_on(tmp_path: Path) -> None:
    _fake_uv(tmp_path / ".local" / "bin" / _MACHINE)
    _fake_venv(tmp_path)
    result = _source(tmp_path, tmp_path, 'echo "$UV_PROJECT_ENVIRONMENT"')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f".venv-{_MACHINE}"


def test_the_machine_specific_uv_wins_over_the_shared_one(tmp_path: Path) -> None:
    _fake_uv(tmp_path / ".local" / "bin" / _MACHINE)
    shared = tmp_path / ".local" / "bin" / "uv"
    shared.write_text("#!/bin/sh\necho shared-uv\n")
    shared.chmod(0o755)
    _fake_venv(tmp_path)
    result = _source(tmp_path, tmp_path, "uv")
    assert result.stdout.strip() == "uv-for-this-machine"


def test_the_shared_uv_still_serves_when_no_machine_directory_exists(tmp_path: Path) -> None:
    # TIGRIS predates the per-machine layout: its uv lives in ~/.local/bin directly.
    _fake_uv(tmp_path / ".local" / "bin")
    _fake_venv(tmp_path)
    result = _source(tmp_path, tmp_path, "uv")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "uv-for-this-machine"


def test_a_uv_that_cannot_run_here_stops_the_job_with_the_fix(tmp_path: Path) -> None:
    broken = tmp_path / ".local" / "bin" / "uv"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"\x7fELF not this machine")
    broken.chmod(0o755)
    _fake_venv(tmp_path)
    result = _source(tmp_path, tmp_path, "echo reached")
    assert result.returncode != 0
    assert "reached" not in result.stdout
    assert "make cluster-env" in result.stderr


def test_a_missing_venv_stops_the_job_before_the_model_loads(tmp_path: Path) -> None:
    _fake_uv(tmp_path / ".local" / "bin" / _MACHINE)
    result = _source(tmp_path, tmp_path, "echo reached")
    assert result.returncode != 0
    assert "reached" not in result.stdout
    assert f".venv-{_MACHINE}" in result.stderr
    assert "make cluster-env" in result.stderr


def test_rendered_jobs_carry_the_same_environment_as_the_scripts() -> None:
    script = render(SlurmJob(name="x", command="true", output="x.log"))
    assert _ENV.read_text().strip() in script


@pytest.mark.parametrize("script", _SCRIPTS, ids=lambda p: p.name)
def test_every_script_defaults_to_the_default_target(script: Path) -> None:
    text = script.read_text()
    target = TARGETS[DEFAULT_TARGET]
    assert f"#SBATCH --partition={target.partition}\n" in text
    assert f"#SBATCH --gres={target.gres}\n" in text
    assert "#SBATCH --account=fl-mlm\n" in text


@pytest.mark.parametrize("script", _SCRIPTS, ids=lambda p: p.name)
def test_every_script_sources_the_environment_from_the_checkout(script: Path) -> None:
    text = script.read_text()
    cd = text.index('cd "$HOME/ajsoftworks/sphragis"')
    source = text.index("source sphragis/experiment/cluster-env.sh")
    assert cd < source, "the environment file is found relative to the checkout"
    # One definition: a script that exports its own copy drifts from the file.
    assert not re.search(r"^export (PATH|HF_HUB_CACHE|CC|TOKENIZERS_PARALLELISM)=", text, re.M)


def test_there_are_scripts_to_check() -> None:
    assert len(_SCRIPTS) >= 5


def test_building_the_environment_skips_the_checks_it_exists_to_satisfy(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{_ENV}"; echo "$UV_PROJECT_ENVIRONMENT"'],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "SPHRAGIS_BUILDING_ENV": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f".venv-{_MACHINE}"


def test_the_environment_builder_shares_the_job_environment_and_verifies_uv() -> None:
    builder = (_ROOT / "scripts" / "cluster_env.sh").read_text()
    assert "SPHRAGIS_BUILDING_ENV=1 source sphragis/experiment/cluster-env.sh" in builder
    assert "sha256sum -c" in builder
    assert "uv sync --frozen --extra experiment" in builder
