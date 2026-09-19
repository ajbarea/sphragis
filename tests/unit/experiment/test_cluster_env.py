"""The job environment every sbatch script sources, and the scripts' agreement with it.

TIGRIS is aarch64 and SPORC x86_64, and the two mount one `$HOME`. A job that picks up the
other machine's uv or venv dies with `Exec format error` after the queue wait, so these run
the real file under bash rather than reading it.
"""

from __future__ import annotations

import os
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
    # A GPU job must request the target's device. An analysis job that needs none says so in
    # words, so that a missing --gres is always a decision rather than an omission.
    if f"#SBATCH --gres={target.gres}\n" not in text:
        assert "CPU only" in text, f"{script.name}: no --gres and no statement that it needs none"
    assert "#SBATCH --account=fl-mlm\n" in text


@pytest.mark.parametrize("script", _SCRIPTS, ids=lambda p: p.name)
def test_every_script_sources_the_environment_from_the_checkout(script: Path) -> None:
    text = script.read_text()
    cd = text.index('cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"')
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
    assert "--sbatch-args='-A rc-onboard'" in dry
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


def _suffix(tmp_path: Path, tags: str = "", **env: str) -> str:
    _fake_uv(tmp_path / ".local" / "bin" / _MACHINE)
    _fake_venv(tmp_path)
    tag = f"tag_result_suffix {tags}; " if tags else ""
    result = subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{_ENV}"; {tag}echo "[$RESULT_SUFFIX]"'],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", **env},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_results_on_tigris_keep_their_existing_names(tmp_path: Path) -> None:
    assert _suffix(tmp_path, SLURM_CLUSTER_NAME="tigris") == "[]"


def test_results_on_another_cluster_are_named_for_it_unless_tagged(tmp_path: Path) -> None:
    # Forgetting a tag on SPORC must not overwrite a GH200 result or its scratch-only adapters.
    assert _suffix(tmp_path, SLURM_CLUSTER_NAME="sporc") == "[-sporc]"


def test_a_run_tag_names_the_results(tmp_path: Path) -> None:
    assert _suffix(tmp_path, SLURM_CLUSTER_NAME="sporc", RUN_TAG="sporc-a100") == "[-sporc-a100]"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"SEEDS": "2"}, "[-s2]"),
        ({"SEEDS": "1,2,3"}, "[-s1-2-3]"),
        ({"SEEDS": "1"}, "[]"),
        ({"TRAIN_SIZE": "788"}, "[-n788]"),
        ({"RUN_TAG": "qtfull", "SEEDS": "3", "TRAIN_SIZE": "788"}, "[-qtfull-s3-n788]"),
        ({"RUN_TAG": "", "SEEDS": "2"}, "[-s2]"),
    ],
)
def test_another_seed_or_training_size_never_takes_the_default_name(
    tmp_path: Path, env: dict[str, str], expected: str
) -> None:
    """Untagged, SEEDS=2 wrote over the seed-1 result and adapters on TIGRIS."""
    tagged = _suffix(tmp_path, tags="SEEDS TRAIN_SIZE", SLURM_CLUSTER_NAME="tigris", **env)
    assert tagged == expected


def test_a_variable_the_script_does_not_pass_on_never_renames_it(tmp_path: Path) -> None:
    """A stray exported TRAIN_SIZE must not name an rq1 result that nothing cut."""
    one, two = tmp_path / "rq1", tmp_path / "pilot"
    one.mkdir()
    two.mkdir()
    assert _suffix(one, tags="SEEDS", SLURM_CLUSTER_NAME="tigris", TRAIN_SIZE="788") == "[]"
    assert _suffix(two, SLURM_CLUSTER_NAME="tigris", SEEDS="2") == "[]"


def test_tagging_an_unknown_variable_stops_the_job(tmp_path: Path) -> None:
    _fake_uv(tmp_path / ".local" / "bin" / _MACHINE)
    _fake_venv(tmp_path)
    result = _source(tmp_path, tmp_path, "tag_result_suffix SEED")
    assert result.returncode != 0
    assert "no tag for SEED" in result.stderr


@pytest.mark.parametrize("script", _SCRIPTS, ids=lambda p: p.name)
def test_every_script_names_its_result_by_the_seeds_and_size_it_passes(script: Path) -> None:
    text = script.read_text()
    tagged = re.findall(r"^tag_result_suffix (.*)$", text, flags=re.MULTILINE)
    names = set(tagged[0].split()) if tagged else set()
    for variable, flag in (("SEEDS", "--seeds"), ("TRAIN_SIZE", "--train-size")):
        passes = f"${{{variable}" in text and flag in text
        assert (variable in names) == passes, f"{script.name}: {variable}"


def test_an_explicitly_empty_run_tag_is_a_deliberate_overwrite(tmp_path: Path) -> None:
    assert _suffix(tmp_path, SLURM_CLUSTER_NAME="sporc", RUN_TAG="") == "[]"


def _written_paths(script: Path) -> list[str]:
    """Every result path a job writes, resolved through the variable that guards it.

    A guarded job names its output in an assignment and passes the variable, so reading the
    `--out` argument alone would see only `$OUT`.
    """
    text = script.read_text()
    guarded = dict(re.findall(r'^\s*([A-Z_]+)="\$\(result_path "([^"]+)"\)"$', text, re.MULTILINE))
    return [
        guarded.get(path.strip("${}"), path)
        for path in re.findall(r'--(?:out|adapters) "([^"]+)"', text)
    ]


@pytest.mark.parametrize("script", _SCRIPTS, ids=lambda p: p.name)
def test_every_result_a_job_writes_is_guarded_against_replacing_one(script: Path) -> None:
    """A job that would overwrite a recorded measurement must stop before it spends queue time.

    The guard is an assignment rather than a substitution in the argument list, because a failed
    command substitution inside arguments does not stop a script under `set -u -e`: the job would
    run on and write to an empty path.
    """
    text = script.read_text()
    for path in re.findall(r'--out "([^"]+)"', text):
        assert path == "$OUT", f"{script.name} writes {path} without result_path"
    assert text.count('OUT="$(result_path "') == text.count('--out "$OUT"')


@pytest.mark.parametrize("script", _SCRIPTS, ids=lambda p: p.name)
def test_every_result_and_adapter_path_carries_the_suffix(script: Path) -> None:
    paths = _written_paths(script)
    assert paths, f"{script.name} writes no --out or --adapters path"
    for path in paths:
        assert "$RESULT_SUFFIX" in path, f"{script.name}: {path}"


def test_every_machine_builds_on_one_pinned_interpreter(tmp_path: Path) -> None:
    # Unpinned, uv takes whatever interpreter each machine already has or downloads first:
    # TIGRIS built on 3.13.15 and SPORC on 3.14.7, a second variable beside the GPU. Pinned in
    # the job environment rather than .python-version, which CI's older uv cannot resolve.
    _fake_uv(tmp_path / ".local" / "bin" / _MACHINE)
    _fake_venv(tmp_path)
    result = _source(tmp_path, tmp_path, 'echo "$UV_PYTHON"')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "3.13.15", "the interpreter every GH200 result so far ran on"


def test_a_job_records_the_commit_its_checkout_holds_at_start(tmp_path: Path) -> None:
    _fake_uv(tmp_path / ".local" / "bin" / _MACHINE)
    _fake_venv(tmp_path)
    # Hooks export GIT_INDEX_FILE and friends; inherited, they point these commands at the
    # repository being committed to instead of the scratch one.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "x")
    head = git("rev-parse", "HEAD")
    result = _source(tmp_path, tmp_path, 'echo "[$SPHRAGIS_GIT_COMMIT]"')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"[{head}]"


def test_outside_a_checkout_the_start_commit_is_empty_not_an_error(tmp_path: Path) -> None:
    _fake_uv(tmp_path / ".local" / "bin" / _MACHINE)
    _fake_venv(tmp_path)
    result = _source(tmp_path, tmp_path, 'echo "[$SPHRAGIS_GIT_COMMIT]"')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_a_job_imports_the_checkout_it_entered_first(tmp_path: Path) -> None:
    """A pinned worktree's job must run the worktree's code, not the editable main checkout's."""
    _fake_uv(tmp_path / ".local" / "bin" / _MACHINE)
    _fake_venv(tmp_path)
    result = _source(tmp_path, tmp_path, 'echo "[$PYTHONPATH]"')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"[{tmp_path}]"


def test_submit_pinned_runs_the_job_from_a_worktree_at_this_commit() -> None:
    dry = _dry_run("submit-pinned", "JOB=pilot")
    assert "git worktree add" in dry
    assert "SPHRAGIS_CHECKOUT=" in dry
    assert "sbatch $flags scripts/pilot.sbatch" in dry
    assert "--sbatch-args=" in dry, "free-form options still pass through slurm.py's checks"


def test_a_different_client_size_names_its_own_results(tmp_path: Path) -> None:
    """Two local-training lengths must not write into one adapter directory, which the geometry
    and projection jobs then glob together."""
    assert _suffix(
        tmp_path, tags="CLIENT_SIZE", SLURM_CLUSTER_NAME="tigris", CLIENT_SIZE="128"
    ) == ("[-c128]")


_GEOMETRY_JOBS = {
    "adapter_geometry.sbatch": "client-geometry{}.json",
    "adapter_projection.sbatch": "client-vectors{}.npz",
}


def _run_job(script_name: str, root: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """The real sbatch body, in a checkout of its own, with a uv that reports its arguments.

    The checkout is built here rather than reused: running against the repository passed only
    because this machine happens to carry a `.venv-<machine>` directory, and CI, which does not,
    stopped in cluster-env before reaching anything the test was about.
    """
    home, checkout = root / "home", root / "checkout"
    for directory in (home, checkout, home / "scratch"):
        directory.mkdir(parents=True, exist_ok=True)
    for shared in ("scripts", "sphragis"):
        link = checkout / shared
        if not link.exists():
            link.symlink_to(_ROOT / shared)
    if not (checkout / f".venv-{_MACHINE}").exists():
        _fake_venv(checkout)
    uv = home / ".local" / "bin" / _MACHINE / "uv"
    uv.parent.mkdir(parents=True, exist_ok=True)
    uv.write_text('#!/bin/sh\necho "$@"\n')
    uv.chmod(0o755)
    return subprocess.run(
        ["bash", str(checkout / "scripts" / script_name)],
        capture_output=True,
        text=True,
        cwd=checkout,
        env={
            "HOME": str(home),
            "PATH": "/usr/bin:/bin",
            "SPHRAGIS_CHECKOUT": str(checkout),
            "RUN_TAG": "some-other-run",
            **env,
        },
    )


@pytest.mark.parametrize("script_name,output", sorted(_GEOMETRY_JOBS.items()))
@pytest.mark.parametrize(
    "pattern,suffix",
    [
        ("", ""),
        ("sphragis-adapters-clients-cpp-early/*-c*/adapter_model.safetensors", "-cpp-early"),
        (
            "sphragis-adapters-clients-cpp-early-c128/*-c*/adapter_model.safetensors",
            "-cpp-early-c128",
        ),
    ],
)
def test_geometry_is_named_for_the_adapters_it_reads_not_for_the_run_tag(
    script_name: str, output: str, pattern: str, suffix: str, tmp_path: Path
) -> None:
    """RUN_TAG is deliberately wrong here: only PATTERN may decide the name.

    These two jobs read a directory of adapters that another job chose. Taking the name from
    RUN_TAG would let a run over one client size overwrite another's geometry, which is the file
    every committed RQ2 number was computed from.
    """
    result = _run_job(script_name, tmp_path, **({"PATTERN": pattern} if pattern else {}))
    assert result.returncode == 0, result.stderr
    assert str(tmp_path / "home" / output.format(suffix)) in result.stdout
    assert "some-other-run" not in result.stdout


@pytest.mark.parametrize("script_name,output", sorted(_GEOMETRY_JOBS.items()))
def test_a_recorded_measurement_is_not_replaced_without_saying_so(
    script_name: str, output: str, tmp_path: Path
) -> None:
    (tmp_path / "home").mkdir(exist_ok=True)
    existing = tmp_path / "home" / output.format("")
    existing.write_text("a measurement the study cites")
    result = _run_job(script_name, tmp_path)
    assert result.returncode != 0
    assert "OVERWRITE=1" in result.stderr
    assert existing.read_text() == "a measurement the study cites"
    deliberate = _run_job(script_name, tmp_path, OVERWRITE="1")
    assert deliberate.returncode == 0, deliberate.stderr


def test_a_second_packing_names_its_own_results_and_adapters(tmp_path: Path) -> None:
    """Another draw of which examples go to which client must never land on the first draw."""
    second = _run_job(
        "client_updates.sbatch",
        tmp_path / "second",
        RUN_TAG="cpp-early",
        CLIENT_SIZE="128",
        PACKING_SEED="2",
    )
    assert second.returncode == 0, second.stderr
    assert "client-updates-cpp-early-c128-p2.json" in second.stdout
    assert "sphragis-adapters-clients-cpp-early-c128-p2" in second.stdout
    assert "--packing-seed 2" in second.stdout
    first = _run_job(
        "client_updates.sbatch", tmp_path / "first", RUN_TAG="cpp-early", CLIENT_SIZE="128"
    )
    assert first.returncode == 0, first.stderr
    assert "client-updates-cpp-early-c128.json" in first.stdout
    assert "--packing-seed" not in first.stdout, "the first packing must reproduce as it ran"
