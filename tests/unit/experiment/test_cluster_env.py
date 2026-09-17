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


# --- Building the environment ------------------------------------------------------------

_BUILDER = _ROOT / "scripts" / "cluster_env.sh"
_ARCHIVE = f"uv-{_MACHINE}-unknown-linux-gnu.tar.gz"


def _release(tmp_path: Path, *, tamper: bool = False) -> Path:
    """A fake uv release plus a curl that serves it, so the builder runs without a network."""
    import hashlib
    import tarfile

    release = tmp_path / "release"
    staged = release / f"uv-{_MACHINE}-unknown-linux-gnu"
    staged.mkdir(parents=True)
    fake_uv = (
        "#!/bin/sh\n"
        'case "$1" in\n'
        "  --version) echo uv 0.0.0-fake ;;\n"
        "  run) echo torch fake ;;\n"
        "esac\n"
    )
    for name in ("uv", "uvx"):
        (staged / name).write_text(fake_uv)
        (staged / name).chmod(0o755)
    with tarfile.open(release / _ARCHIVE, "w:gz") as archive:
        archive.add(staged, arcname=staged.name)
    digest = hashlib.sha256((release / _ARCHIVE).read_bytes()).hexdigest()
    if tamper:
        digest = "0" * 64
    (release / f"{_ARCHIVE}.sha256").write_text(f"{digest}  {_ARCHIVE}\n")

    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/bin/bash\n"
        'while [ $# -gt 0 ]; do case "$1" in\n'
        '  -o) out="$2"; shift 2;; -*) shift;; *) url="$1"; shift;;\n'
        "esac; done\n"
        f'cp "{release}/$(basename "$url")" "$out"\n'
    )
    curl.chmod(0o755)
    return bin_dir


def _checkout(home: Path) -> None:
    experiment = home / "ajsoftworks" / "sphragis" / "sphragis" / "experiment"
    experiment.mkdir(parents=True)
    (experiment / "cluster-env.sh").write_text(_ENV.read_text())


def _run_builder(home: Path, bin_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_BUILDER)],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin", "UV_VERSION": "0.0.0"},
    )


def _foreign_shared_uv(home: Path) -> None:
    # What SPORC sees in ~/.local/bin: TIGRIS's aarch64 uv, which cannot execute here.
    shared = home / ".local" / "bin" / "uv"
    shared.parent.mkdir(parents=True)
    shared.write_bytes(b"\x7fELF not this machine")
    shared.chmod(0o755)


def test_the_builder_installs_uv_past_a_foreign_one_and_uses_it(tmp_path: Path) -> None:
    # bash caches the path of the uv that failed; without forgetting it, the freshly installed
    # uv is never run and the first build dies with Exec format error.
    home = tmp_path / "home"
    _checkout(home)
    _foreign_shared_uv(home)
    result = _run_builder(home, _release(tmp_path))
    assert result.returncode == 0, result.stderr
    assert (home / ".local" / "bin" / _MACHINE / "uv").is_file()
    assert "uv 0.0.0-fake" in result.stdout
    assert f"CLUSTER_ENV_OK .venv-{_MACHINE}" in result.stdout


def test_the_builder_refuses_a_uv_whose_checksum_does_not_match(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _checkout(home)
    _foreign_shared_uv(home)
    result = _run_builder(home, _release(tmp_path, tamper=True))
    assert result.returncode != 0
    assert not (home / ".local" / "bin" / _MACHINE / "uv").exists()
    assert "CLUSTER_ENV_OK" not in result.stdout


# --- make targets, read as the shell will receive them -------------------------------------


def _dry_run(*args: str) -> str:
    return subprocess.run(
        ["make", "-n", *args], capture_output=True, text=True, cwd=_ROOT, check=True
    ).stdout


def test_submit_sends_free_form_options_through_the_checks() -> None:
    # Placed on the sbatch line directly, a bare word in SBATCH_ARGS ends option parsing and the
    # checked account and cluster are never read.
    dry = _dry_run("submit", "JOB=pilot", "SBATCH_ARGS=-A rc-onboard")
    assert "--sbatch-args '-A rc-onboard'" in dry
    assert "sbatch $flags scripts/pilot.sbatch" in dry
    assert "rc-onboard scripts/" not in dry


def test_submit_checks_the_venv_by_its_config_not_its_interpreter() -> None:
    # bin/python is a symlink that may resolve only on the compute node's machine.
    assert "[ -f .venv-$machine/pyvenv.cfg ]" in _dry_run("submit", "JOB=pilot", "CLUSTER=sporc")


def test_cluster_env_judges_success_by_the_builders_marker() -> None:
    # sbatch --wait --clusters=sporc exits 0 for a job cancelled while pending.
    assert "grep -q CLUSTER_ENV_OK \\$log" in _dry_run("cluster-env", "CLUSTER=sporc")


def test_cluster_env_logs_are_named_for_their_cluster() -> None:
    # Job ids are per cluster and both clusters write to one logs/, so an old log from the other
    # cluster could carry the success marker for a job that never ran.
    assert "logs/sphragis-cluster-env-sporc-%j.log" in _dry_run("cluster-env", "CLUSTER=sporc")


def test_a_tagged_rq1_run_writes_nothing_an_untagged_run_owns() -> None:
    # A pilot on another GPU must not overwrite the GH200 run's result or its adapters, which
    # live only in cluster scratch.
    text = (_ROOT / "scripts" / "rq1.sbatch").read_text()
    assert '--adapters "$HOME/scratch/sphragis-adapters$SUFFIX"' in text
    assert '--out "$HOME/rq1-$MODE$SUFFIX.json"' in text
    assert 'SUFFIX="${TAG:+-$TAG}"' in text


def test_every_machine_builds_on_one_pinned_interpreter(tmp_path: Path) -> None:
    # Unpinned, uv takes whatever interpreter each machine already has or downloads first:
    # TIGRIS built on 3.13.15 and SPORC on 3.14.7, a second variable beside the GPU. Pinned in
    # the job environment rather than .python-version, which CI's older uv cannot resolve.
    _fake_uv(tmp_path / ".local" / "bin" / _MACHINE)
    _fake_venv(tmp_path)
    result = _source(tmp_path, tmp_path, 'echo "$UV_PYTHON"')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "3.13.15", "the interpreter every GH200 result so far ran on"
