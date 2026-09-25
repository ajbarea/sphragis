"""The job environment every sbatch script sources, and the scripts' agreement with it.

TIGRIS is aarch64 and SPORC x86_64, and the two mount one `$HOME`. A job that picks up the
other machine's uv or venv dies with `Exec format error` after the queue wait, so these run
the real file under bash rather than reading it.
"""

from __future__ import annotations

import os
import platform
import re
import signal
import subprocess
import time
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


@pytest.mark.parametrize("script", _SCRIPTS, ids=lambda p: p.name)
def test_no_job_replaces_the_release_trap(script: Path) -> None:
    """A second `trap ... EXIT` silently replaces the claim release, and the claim then outlives
    the job that made it. Jobs claim results, so none of them may set its own. The environment
    builder does set one and is not a job: it claims nothing, and is not in this list."""
    offending = [
        line
        for line in script.read_text().splitlines()
        if re.match(r"\s*trap\b", line) and "EXIT" in line
    ]
    assert not offending, f"{script.name} replaces the release trap: {offending}"


def _name_after(home: Path, pattern: str) -> subprocess.CompletedProcess[str]:
    _fake_uv(home / ".local" / "bin" / _MACHINE)
    _fake_venv(home)
    return _source(home, home, f'name_result_after_adapters "{pattern}"; echo "[$RESULT_SUFFIX]"')


def _adapters(home: Path, directory: str, *clients: str) -> None:
    for client in clients:
        (home / "scratch" / directory / client).mkdir(parents=True, exist_ok=True)


def test_the_result_is_named_after_the_adapters_it_reads(tmp_path: Path) -> None:
    result = _name_after(
        tmp_path, "sphragis-adapters-clients-cpp-early/*-c*/adapter_model.safetensors"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("[-cpp-early]")


def test_the_default_adapters_keep_the_empty_suffix(tmp_path: Path) -> None:
    result = _name_after(tmp_path, "sphragis-adapters-clients/*-c*/adapter_model.safetensors")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("[]")


def test_the_withheld_adapters_name_themselves_apart(tmp_path: Path) -> None:
    """Both halves live in one directory, so the selector is what tells the two geometries apart."""
    _adapters(tmp_path, "sphragis-adapters-dual-t2", "a-c0", "a-c0-local")
    result = _name_after(tmp_path, "sphragis-adapters-dual-t2/*-local/adapter_model.safetensors")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("[-dual-t2-local]")


def test_a_selector_spanning_both_halves_is_refused(tmp_path: Path) -> None:
    """`*-c*` matches `a-c0-local` too, and one file cannot hold two geometries."""
    _adapters(tmp_path, "sphragis-adapters-dual-t2", "a-c0", "a-c0-local")
    result = _name_after(tmp_path, "sphragis-adapters-dual-t2/*-c*/adapter_model.safetensors")
    assert result.returncode != 0
    assert "two geometries" in result.stderr


def test_the_round_zero_personalized_adapters_name_themselves_apart(tmp_path: Path) -> None:
    """FDLoRA's round-0 average is a third shape beside the transmitted and withheld halves."""
    _adapters(tmp_path, "sphragis-adapters-fdlora-r6-k3-h3", "a-c0", "a-c0-local", "a-c0-p0")
    result = _name_after(
        tmp_path, "sphragis-adapters-fdlora-r6-k3-h3/*-p0/adapter_model.safetensors"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("[-fdlora-r6-k3-h3-p0]")


def test_a_selector_spanning_all_three_fdlora_shapes_is_refused(tmp_path: Path) -> None:
    _adapters(tmp_path, "sphragis-adapters-fdlora-r6-k3-h3", "a-c0", "a-c0-local", "a-c0-p0")
    result = _name_after(
        tmp_path, "sphragis-adapters-fdlora-r6-k3-h3/*-c*/adapter_model.safetensors"
    )
    assert result.returncode != 0
    assert "three geometries" in result.stderr


def _mixed_organization_adapters(tmp_path: Path) -> str:
    """Two organizations, each with a client's full FDLoRA triple: what a real run writes."""
    directory = "sphragis-adapters-fdlora-mixed"
    _adapters(
        tmp_path,
        directory,
        "aosp-c0",
        "aosp-c0-local",
        "aosp-c0-p0",
        "qt-c1",
        "qt-c1-local",
        "qt-c1-p0",
    )
    return directory


@pytest.mark.parametrize(
    ("selector", "suffix"),
    [
        # No hyphen before "local": a selector's own spelling must not decide the class, only
        # what it actually matches on disk. Silently kept the default (transmitted) name before
        # this was fixed to classify by the real matched directories.
        ("*local", "-local"),
        ("*-local*", "-local"),
        ("*p0", "-p0"),
        ("*-p0*", "-p0"),
    ],
)
def test_a_selector_is_classified_by_what_it_matches_not_by_its_own_spelling(
    tmp_path: Path, selector: str, suffix: str
) -> None:
    directory = _mixed_organization_adapters(tmp_path)
    result = _name_after(tmp_path, f"{directory}/{selector}/adapter_model.safetensors")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith(f"[-fdlora-mixed{suffix}]")


@pytest.mark.parametrize("selector", ["aosp-*", "qt-*", "*-c1*"])
def test_an_organization_or_client_prefix_alone_still_spans_every_shape(
    tmp_path: Path, selector: str
) -> None:
    """Scoping to one organization or one client does not disambiguate the shape by itself."""
    directory = _mixed_organization_adapters(tmp_path)
    result = _name_after(tmp_path, f"{directory}/{selector}/adapter_model.safetensors")
    assert result.returncode != 0
    assert "three geometries" in result.stderr


def test_a_selector_reaching_only_the_transmitted_fdlora_half_is_unsuffixed(tmp_path: Path) -> None:
    """Narrow enough to exclude both `-local` and `-p0`, the transmitted half keeps its name."""
    _adapters(tmp_path, "sphragis-adapters-fdlora-r6-k3-h3", "a-c0", "a-c0-local", "a-c0-p0")
    result = _name_after(
        tmp_path, "sphragis-adapters-fdlora-r6-k3-h3/*-c[0-9]/adapter_model.safetensors"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("[-fdlora-r6-k3-h3]")


def test_a_selector_that_cannot_reach_the_withheld_half_is_the_transmitted_one(
    tmp_path: Path,
) -> None:
    _adapters(tmp_path, "sphragis-adapters-dual-t2", "a-c0", "a-c0-local")
    result = _name_after(tmp_path, "sphragis-adapters-dual-t2/*-c[0-9]/adapter_model.safetensors")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("[-dual-t2]")


def test_a_directory_without_withheld_adapters_keeps_its_name(tmp_path: Path) -> None:
    """A single-adapter run's `*-c*` is not ambiguous, and must not start being refused."""
    _adapters(tmp_path, "sphragis-adapters-clients-cpp-rebuilt", "a-c0", "a-c1")
    result = _name_after(
        tmp_path, "sphragis-adapters-clients-cpp-rebuilt/*-c*/adapter_model.safetensors"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("[-cpp-rebuilt]")


def test_a_globbed_first_directory_is_refused(tmp_path: Path) -> None:
    result = _name_after(tmp_path, "sphragis-adapters-*/*-c*/adapter_model.safetensors")
    assert result.returncode != 0
    assert "must be literal" in result.stderr


def test_a_directory_that_is_not_an_adapters_directory_is_refused(tmp_path: Path) -> None:
    result = _name_after(tmp_path, "scratch/*-c*/adapter_model.safetensors")
    assert result.returncode != 0
    assert "must start with a sphragis-adapters directory" in result.stderr


def test_name_result_after_adapters_restores_the_callers_nullglob_state(tmp_path: Path) -> None:
    """The guard used to leave nullglob off unconditionally, however the caller had it set."""
    _fake_uv(tmp_path / ".local" / "bin" / _MACHINE)
    _fake_venv(tmp_path)
    _adapters(tmp_path, "sphragis-adapters-clients-cpp-early", "a-c0")
    result = _source(
        tmp_path,
        tmp_path,
        "shopt -s nullglob; "
        'name_result_after_adapters "sphragis-adapters-clients-cpp-early/*-c*/'
        'adapter_model.safetensors" >/dev/null; shopt nullglob',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("on")


@pytest.mark.parametrize("script", _SCRIPTS, ids=lambda p: p.name)
def test_no_job_derives_the_adapters_name_for_itself(script: Path) -> None:
    """One definition: a job with its own copy drifts from the other's."""
    text = script.read_text()
    if "PATTERN=" not in text:
        pytest.skip("reads no adapters")
    assert "name_result_after_adapters" in text
    assert "sphragis-adapters-clients*)" not in text, "a second copy of the derivation"


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
    """Every result path a job writes, resolved through the claim that guards it."""
    text = script.read_text()
    claimed = dict(re.findall(r'^\s*claim_result ([A-Z_]+) "([^"]+)"$', text, re.MULTILINE))
    return [
        claimed.get(path.strip("${}"), path)
        for path in re.findall(r'--(?:out|adapters) "([^"]+)"', text)
    ]


@pytest.mark.parametrize("script", _SCRIPTS, ids=lambda p: p.name)
def test_every_result_a_job_writes_is_claimed_as_a_plain_command(script: Path) -> None:
    """The claim must run in the job's own shell, or its release on exit belongs to a subshell.

    `export OUT="$(...)"`, `local`, `readonly` and `declare` all continue past a failed command
    substitution under `set -e`, which is why the claim is a command and not a substitution.
    """
    text = script.read_text()
    for variable in re.findall(r'--out "\$\{?([A-Z_]+)\}?"', text):
        assert re.search(rf'^\s*claim_result {variable} "', text, re.MULTILINE), (
            f"{script.name} writes ${variable} without claiming it"
        )
    assert "result_path" not in text, f"{script.name} still uses the check-only guard"


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

    # No env filtering here: `tests/conftest.py` strips git's exported environment once for
    # the whole run, because one test file defending itself left the next one exposed.
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=tmp_path,
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


def test_a_rerun_at_another_rank_names_its_own_result(tmp_path: Path) -> None:
    """The conditional branch reruns an arm at rank 256. Sharing a name with the registered
    rank-32 result would overwrite the very thing the branch exists to be compared against."""
    assert _suffix(tmp_path, tags="LORA_RANK", SLURM_CLUSTER_NAME="tigris", LORA_RANK="256") == (
        "[-r256]"
    )


def test_the_registered_rank_keeps_the_existing_names(tmp_path: Path) -> None:
    assert _suffix(tmp_path, tags="LORA_RANK", SLURM_CLUSTER_NAME="tigris", LORA_RANK="32") == "[]"


_GEOMETRY_JOBS = {
    "adapter_geometry.sbatch": "client-geometry{}.json",
    "adapter_projection.sbatch": "client-vectors{}.npz",
}


_FAKE_UV = """#!/bin/sh
if [ "$1" = run ]; then
  echo "${FAKE_ID:-job} $*" >> "$HOME/uv.calls"
  out=""; prev=""
  for arg in "$@"; do [ "$prev" = "--out" ] && out="$arg"; prev="$arg"; done
  [ -z "${FAKE_SLEEP:-}" ] || sleep "$FAKE_SLEEP"
  [ -z "${FAKE_FAIL:-}" ] || exit 1
  [ -z "$out" ] || echo "written by ${FAKE_ID:-job}" > "$out"
fi
echo "$@"
"""

# What each job needs from its environment to reach its claim; a fake uv never reads the values.
_JOB_DEFAULTS = {
    "MODE": "pilot",
    "CONDITION": "sym-0",
    "SLURMD_NODENAME": "node1",
    "TEMPERATURE": "1.0",
    "POST": "/unused/post.jsonl",
    "PRE": "/unused/pre.jsonl",
    "ORG": "qt",
}


def _prepare(root: Path) -> tuple[Path, Path]:
    """A checkout and a home of their own, with a uv that reports rather than runs.

    The checkout is built here rather than reused: running against the repository passed only
    because this machine happens to carry a `.venv-<machine>` directory, and CI, which does not,
    stopped in cluster-env before reaching anything the test was about. For `uv run` the fake uv
    logs the call, writes `written by $FAKE_ID` into the --out path the way a finished script
    would, and can take time (FAKE_SLEEP) or fail before writing (FAKE_FAIL); anything else, such
    as the environment's check that uv runs, passes straight through.
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
    uv.write_text(_FAKE_UV)
    uv.chmod(0o755)
    return home, checkout


def _job_env(home: Path, checkout: Path, env: dict[str, str]) -> dict[str, str]:
    return {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "SPHRAGIS_CHECKOUT": str(checkout),
        "RUN_TAG": "some-other-run",
        **_JOB_DEFAULTS,
        **env,
    }


def _run_job(script_name: str, root: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """The real sbatch body, run to completion in a checkout of its own."""
    home, checkout = _prepare(root)
    return subprocess.run(
        ["bash", str(checkout / "scripts" / script_name)],
        capture_output=True,
        text=True,
        cwd=checkout,
        env=_job_env(home, checkout, env),
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


def test_a_second_fdlora_packing_names_its_own_results_and_adapters(tmp_path: Path) -> None:
    """FDLoRA's schedule follows the same convention client_updates.sbatch already does."""
    second = _run_job(
        "fdlora_schedule.sbatch",
        tmp_path / "second",
        RUN_TAG="cpp-early",
        CLIENT_SIZE="128",
        PACKING_SEED="2",
    )
    assert second.returncode == 0, second.stderr
    assert "client-updates-fdlora-cpp-early-c128-r6-k3-h5-p2.json" in second.stdout
    assert "sphragis-adapters-fdlora-cpp-early-c128-r6-k3-h5-p2" in second.stdout
    assert "--packing-seed 2" in second.stdout
    first = _run_job(
        "fdlora_schedule.sbatch", tmp_path / "first", RUN_TAG="cpp-early", CLIENT_SIZE="128"
    )
    assert first.returncode == 0, first.stderr
    assert "client-updates-fdlora-cpp-early-c128-r6-k3-h5.json" in first.stdout
    assert "--packing-seed" not in first.stdout, "the first packing must reproduce as it ran"


def test_the_default_fdlora_packing_under_a_new_name_is_refused(tmp_path: Path) -> None:
    result = _run_job("fdlora_schedule.sbatch", tmp_path, PACKING_SEED="1")
    assert result.returncode != 0
    assert "default packing" in result.stderr


@pytest.mark.parametrize(
    ("value", "expect_flag"),
    [("1", True), ("0", False), ("yes", False), (None, False)],
)
def test_allow_final_sync_only_enables_on_the_value_one(
    value: str | None, expect_flag: bool, tmp_path: Path
) -> None:
    """The same convention as LEGACY_CORPUS: only "1" turns the check off, not any truthy value."""
    env = {"ALLOW_FINAL_SYNC": value} if value is not None else {}
    result = _run_job("fdlora_schedule.sbatch", tmp_path, **env)
    assert result.returncode == 0, result.stderr
    assert ("--allow-final-sync" in result.stdout) == expect_flag


def _results(root: Path) -> dict[str, str]:
    home = root / "home"
    found = sorted(home.glob("*.json")) + sorted(home.glob("*.npz"))
    return {path.name: path.read_text() for path in found}


@pytest.mark.parametrize("script", _SCRIPTS, ids=lambda p: p.name)
def test_a_second_run_of_any_job_refuses_before_it_does_any_work(
    script: Path, tmp_path: Path
) -> None:
    """Every job script, not two of them: a text check had let `export OUT=` through."""
    first = _run_job(script.name, tmp_path, FAKE_ID="JOB_ONE")
    assert first.returncode == 0, first.stderr
    written = _results(tmp_path)
    assert written, "the first run wrote a result"
    assert all(text == "written by JOB_ONE\n" for text in written.values()), written
    second = _run_job(script.name, tmp_path, FAKE_ID="JOB_TWO")
    assert second.returncode != 0
    assert "OVERWRITE=1" in second.stderr
    callers = [
        line.split()[0] for line in (tmp_path / "home" / "uv.calls").read_text().splitlines()
    ]
    assert "JOB_TWO" not in callers, "the refused job must not have started its work"
    assert _results(tmp_path) == written


def test_two_jobs_running_at_once_cannot_both_claim_one_result(tmp_path: Path) -> None:
    """The check-only guard let both past, and the later job's result replaced the earlier's."""
    home, checkout = _prepare(tmp_path)
    script = str(checkout / "scripts" / "client_updates.sbatch")
    slow = subprocess.Popen(
        ["bash", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=checkout,
        env=_job_env(home, checkout, {"FAKE_ID": "slow", "FAKE_SLEEP": "3"}),
    )
    time.sleep(1.0)
    fast = subprocess.run(
        ["bash", script],
        capture_output=True,
        text=True,
        cwd=checkout,
        env=_job_env(home, checkout, {"FAKE_ID": "fast"}),
    )
    slow.communicate(timeout=60)
    assert slow.returncode == 0
    assert fast.returncode != 0, "the second job to start must refuse"
    assert "another job has claimed it" in fast.stderr
    assert set(_results(tmp_path).values()) == {"written by slow\n"}


def test_a_job_that_fails_releases_its_claim_so_the_rerun_can_proceed(tmp_path: Path) -> None:
    failed = _run_job("pilot.sbatch", tmp_path, FAKE_ID="crashed", FAKE_FAIL="1")
    assert failed.returncode != 0
    assert _results(tmp_path) == {}, "an unfilled claim is removed when the job exits"
    rerun = _run_job("pilot.sbatch", tmp_path, FAKE_ID="rerun")
    assert rerun.returncode == 0, rerun.stderr
    assert set(_results(tmp_path).values()) == {"written by rerun\n"}


def test_overwrite_replaces_a_result_deliberately(tmp_path: Path) -> None:
    assert _run_job("pilot.sbatch", tmp_path, FAKE_ID="old").returncode == 0
    replaced = _run_job("pilot.sbatch", tmp_path, FAKE_ID="new", OVERWRITE="1")
    assert replaced.returncode == 0, replaced.stderr
    assert set(_results(tmp_path).values()) == {"written by new\n"}


def test_a_claim_that_cannot_be_created_is_not_reported_as_a_clash(tmp_path: Path) -> None:
    """A missing directory fails the same exclusive create; the message must say which."""
    _fake_uv(tmp_path / ".local" / "bin" / _MACHINE)
    _fake_venv(tmp_path)
    result = _source(tmp_path, tmp_path, f'claim_result OUT "{tmp_path}/no/such/dir/r.json"')
    assert result.returncode != 0
    assert "cannot claim" in result.stderr
    assert "another job" not in result.stderr


_EARLY = "sphragis-adapters-clients-cpp-early/*-c*/adapter_model.safetensors"


@pytest.mark.parametrize(
    "matrices,subtract,name",
    [
        (None, None, "client-geometry-cpp-early.json"),
        ("a", "1", "client-geometry-cpp-early-a.json"),
        ("b", None, "client-geometry-cpp-early-b.json"),
    ],
)
def test_each_meaningful_factor_reading_has_one_name(
    matrices: str | None, subtract: str | None, name: str, tmp_path: Path
) -> None:
    env = {"PATTERN": _EARLY}
    if matrices:
        env["MATRICES"] = matrices
    if subtract:
        env["SUBTRACT_INIT"] = subtract
    result = _run_job("adapter_geometry.sbatch", tmp_path, **env)
    assert result.returncode == 0, result.stderr
    assert name in result.stdout


@pytest.mark.parametrize(
    "matrices,subtract",
    [("product", "1"), ("a", None), ("a", "0"), ("b", "1"), ("b", "0"), ("product", "0")],
)
def test_a_factor_reading_with_no_single_meaning_is_refused(
    matrices: str, subtract: str | None, tmp_path: Path
) -> None:
    """Product with a subtracted A computes B (A - A0), not the update; raw A is mostly the init."""
    env = {"PATTERN": _EARLY, "MATRICES": matrices}
    if subtract is not None:
        env["SUBTRACT_INIT"] = subtract
    result = _run_job("adapter_geometry.sbatch", tmp_path, **env)
    assert result.returncode != 0
    assert "has no meaning here" in result.stderr
    assert not (tmp_path / "home" / "uv.calls").exists()


@pytest.mark.parametrize("script", ["adapter_geometry.sbatch", "adapter_projection.sbatch"])
@pytest.mark.parametrize(
    "pattern",
    ["*/*-c*/adapter_model.safetensors", "other-adapters/*-c*/adapter_model.safetensors"],
)
def test_a_pattern_that_cannot_name_its_output_is_refused(
    script: str, pattern: str, tmp_path: Path
) -> None:
    result = _run_job(script, tmp_path, PATTERN=pattern)
    assert result.returncode != 0
    assert "PATTERN" in result.stderr


def test_another_sketch_width_names_its_own_vectors(tmp_path: Path) -> None:
    result = _run_job("adapter_projection.sbatch", tmp_path, PATTERN=_EARLY, SKETCH="32")
    assert result.returncode == 0, result.stderr
    assert "client-vectors-cpp-early-w32.npz" in result.stdout


def test_the_default_packing_under_a_new_name_is_refused(tmp_path: Path) -> None:
    result = _run_job("client_updates.sbatch", tmp_path, PACKING_SEED="1")
    assert result.returncode != 0
    assert "default packing" in result.stderr


def test_a_job_is_named_after_the_result_it_claims(tmp_path: Path) -> None:
    """The portal lists job names, and one name per script says nothing about which run it is.

    Renaming runs through `scontrol`, so the test stands one on PATH that records its arguments.
    """
    _fake_uv(tmp_path / ".local" / "bin" / _MACHINE)
    _fake_venv(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    recorded = tmp_path / "scontrol-args"
    fake = bin_dir / "scontrol"
    fake.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" >> {recorded}\n')
    fake.chmod(0o755)

    script = (
        f'export PATH="{bin_dir}:$PATH"\n'
        "export SLURM_JOB_ID=4242\n"
        f'claim_result OUT "{tmp_path}/client-updates-cpp-256-c256.json"\n'
    )
    _source(tmp_path, tmp_path, script)

    assert recorded.exists(), "the job was not renamed"
    arguments = recorded.read_text().split()
    assert "JobId=4242" in arguments
    assert "JobName=client-updates-cpp-256-c256" in arguments


def test_a_job_outside_slurm_is_not_renamed(tmp_path: Path) -> None:
    """A local run has no job to rename, and must not fail for trying."""
    _fake_uv(tmp_path / ".local" / "bin" / _MACHINE)
    _fake_venv(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    recorded = tmp_path / "scontrol-args"
    fake = bin_dir / "scontrol"
    fake.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" >> {recorded}\n')
    fake.chmod(0o755)

    script = (
        f'export PATH="{bin_dir}:$PATH"\n'
        "unset SLURM_JOB_ID\n"
        f'claim_result OUT "{tmp_path}/client-updates-local.json"\n'
        'echo "claimed $OUT"\n'
    )
    result = _source(tmp_path, tmp_path, script)

    assert "claimed" in result.stdout
    assert not recorded.exists(), "a local run tried to rename a job"


def _kill_mid_job(root: Path, signal_number: int, **env: str) -> Path:
    """Start pilot.sbatch, stop it while its work is running, and return the claim it left.

    The whole process group goes, so the fake uv cannot finish writing after its job is gone,
    which is what a SIGKILLed Slurm job looks like from the filesystem.
    """
    home, checkout = _prepare(root)
    job = subprocess.Popen(
        ["bash", str(checkout / "scripts" / "pilot.sbatch")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=checkout,
        env=_job_env(
            home,
            checkout,
            {"FAKE_ID": "killed", "FAKE_SLEEP": "30", "SLURM_CLUSTER_NAME": "tigris", **env},
        ),
        start_new_session=True,
    )
    # The result is created a line before its owner record; a job with an id is killed only
    # once both exist, or the kill can land between them and leave a claim with no owner.
    recorded = "SLURM_JOB_ID" in env

    def reached() -> bool:
        claims = sorted(home.glob("*.json"))
        return bool(claims) and (
            not recorded or all(c.with_suffix(".json.claim").exists() for c in claims)
        )

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not reached():
        time.sleep(0.05)
    claims = sorted(home.glob("*.json"))
    os.killpg(os.getpgid(job.pid), signal_number)
    job.communicate(timeout=30)
    assert len(claims) == 1, f"the job did not reach its claim: {claims}"
    return claims[0]


def test_a_killed_job_leaves_an_empty_claim_that_records_who_made_it(tmp_path: Path) -> None:
    claim = _kill_mid_job(tmp_path, signal.SIGKILL, SLURM_JOB_ID="1001")
    assert claim.read_text() == "", "a killed job's claim holds no result"
    assert claim.with_suffix(".json.claim").read_text().strip() == "tigris:1001"


def test_a_requeued_job_reclaims_the_empty_claim_its_kill_left(tmp_path: Path) -> None:
    """Slurm keeps the job id across a requeue, so the leftover is this job's own."""
    _kill_mid_job(tmp_path, signal.SIGKILL, SLURM_JOB_ID="1001")
    requeued = _run_job(
        "pilot.sbatch",
        tmp_path,
        FAKE_ID="requeued",
        SLURM_JOB_ID="1001",
        SLURM_CLUSTER_NAME="tigris",
    )
    assert requeued.returncode == 0, requeued.stderr
    assert "reclaiming it" in requeued.stderr
    assert set(_results(tmp_path).values()) == {"written by requeued\n"}


def test_another_job_does_not_take_over_a_claim_it_cannot_prove_is_dead(tmp_path: Path) -> None:
    """Without Slurm to ask, an empty claim owned by someone else may still be live work."""
    _kill_mid_job(tmp_path, signal.SIGKILL, SLURM_JOB_ID="1001")
    other = _run_job(
        "pilot.sbatch", tmp_path, FAKE_ID="other", SLURM_JOB_ID="2002", SLURM_CLUSTER_NAME="tigris"
    )
    assert other.returncode != 0
    assert "another job has claimed it" in other.stderr
    assert set(_results(tmp_path).values()) == {""}, "the claim is intact and still unfilled"


def test_a_claim_whose_owner_has_left_the_queue_is_reclaimed(tmp_path: Path) -> None:
    claim = _kill_mid_job(tmp_path, signal.SIGKILL, SLURM_JOB_ID="1001")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    squeue = bin_dir / "squeue"
    squeue.write_text("#!/bin/sh\nexit 0\n")  # the job is gone, so the listing is empty
    squeue.chmod(0o755)
    later = _run_job(
        "pilot.sbatch",
        tmp_path,
        FAKE_ID="later",
        SLURM_JOB_ID="2002",
        SLURM_CLUSTER_NAME="tigris",
        PATH=f"{bin_dir}:/usr/bin:/bin",
    )
    assert later.returncode == 0, later.stderr
    assert "no longer queued" in later.stderr
    assert claim.read_text() == "written by later\n"


def test_one_job_claiming_a_path_twice_is_still_refused(tmp_path: Path) -> None:
    """The second call is not a requeue of the first; it is two outputs sharing one name."""
    _fake_uv(tmp_path / ".local" / "bin" / _MACHINE)
    _fake_venv(tmp_path)
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'set -euo pipefail; source "{_ENV}"; '
            f'claim_result FIRST "{tmp_path}/r.json"; claim_result SECOND "{tmp_path}/r.json"; '
            "echo reached",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "SLURM_JOB_ID": "1001",
            "SLURM_CLUSTER_NAME": "tigris",
        },
    )
    assert result.returncode != 0
    assert "reached" not in result.stdout
    assert "another job has claimed it" in result.stderr


def test_an_empty_claim_with_no_owner_recorded_is_left_alone(tmp_path: Path) -> None:
    """Claims made before the record existed, and by runs outside Slurm, name nobody."""
    home, _ = _prepare(tmp_path)
    (home / "pilot-outcomes-some-other-run.json").touch()
    job = _run_job(
        "pilot.sbatch", tmp_path, FAKE_ID="job", SLURM_JOB_ID="1001", SLURM_CLUSTER_NAME="tigris"
    )
    assert job.returncode != 0
    assert "another job has claimed it" in job.stderr
    assert set(_results(tmp_path).values()) == {""}


def test_a_claim_whose_owner_is_still_queued_is_left_alone(tmp_path: Path) -> None:
    """An empty claim is what a running job's claim looks like before it writes."""
    _kill_mid_job(tmp_path, signal.SIGKILL, SLURM_JOB_ID="1001")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    squeue = bin_dir / "squeue"
    squeue.write_text(
        '#!/bin/sh\necho "        1001 tigris sphragis ajb6289  R  0:42      1 node1"\n'
    )
    squeue.chmod(0o755)
    later = _run_job(
        "pilot.sbatch",
        tmp_path,
        FAKE_ID="later",
        SLURM_JOB_ID="2002",
        SLURM_CLUSTER_NAME="tigris",
        PATH=f"{bin_dir}:/usr/bin:/bin",
    )
    assert later.returncode != 0
    assert "another job has claimed it" in later.stderr
    assert set(_results(tmp_path).values()) == {""}


def test_a_result_already_written_is_never_reclaimed_by_its_own_job(tmp_path: Path) -> None:
    """Only an empty claim is abandoned; a finished measurement still needs OVERWRITE."""
    assert (
        _run_job(
            "pilot.sbatch",
            tmp_path,
            FAKE_ID="first",
            SLURM_JOB_ID="1001",
            SLURM_CLUSTER_NAME="tigris",
        ).returncode
        == 0
    )
    again = _run_job(
        "pilot.sbatch", tmp_path, FAKE_ID="again", SLURM_JOB_ID="1001", SLURM_CLUSTER_NAME="tigris"
    )
    assert again.returncode != 0
    assert "OVERWRITE=1" in again.stderr
    assert set(_results(tmp_path).values()) == {"written by first\n"}


def test_a_job_on_the_other_cluster_does_not_take_a_live_claim(tmp_path: Path) -> None:
    """`squeue` answers for its own cluster, and reports the other's live job as unknown.

    TIGRIS and SPORC are separate installations over one $HOME, so an empty listing for a foreign
    id says nothing about whether that job is running.
    """
    _kill_mid_job(tmp_path, signal.SIGKILL, SLURM_JOB_ID="1001")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    squeue = bin_dir / "squeue"
    squeue.write_text("#!/bin/sh\nexit 0\n")  # the local cluster has never heard of job 1001
    squeue.chmod(0o755)
    elsewhere = _run_job(
        "pilot.sbatch",
        tmp_path,
        FAKE_ID="elsewhere",
        SLURM_JOB_ID="4004",
        SLURM_CLUSTER_NAME="sporc",
        PATH=f"{bin_dir}:/usr/bin:/bin",
    )
    assert elsewhere.returncode != 0
    assert "was claimed on tigris" in elsewhere.stderr
    assert set(_results(tmp_path).values()) == {""}


def test_a_same_numbered_job_on_the_other_cluster_does_not_take_a_live_claim(
    tmp_path: Path,
) -> None:
    """The two clusters run independent id counters, so an id alone is not an identity."""
    _kill_mid_job(tmp_path, signal.SIGKILL, SLURM_JOB_ID="1001")
    twin = _run_job(
        "pilot.sbatch",
        tmp_path,
        FAKE_ID="twin",
        SLURM_JOB_ID="1001",
        SLURM_CLUSTER_NAME="sporc",
    )
    assert twin.returncode != 0
    assert "was claimed on tigris" in twin.stderr
    assert set(_results(tmp_path).values()) == {""}


def test_a_claim_naming_no_cluster_is_left_alone(tmp_path: Path) -> None:
    """Records written before the cluster was part of them identify nobody."""
    home, _ = _prepare(tmp_path)
    result = home / "pilot-outcomes-some-other-run.json"
    result.touch()
    result.with_suffix(".json.claim").write_text("1001\n")
    job = _run_job(
        "pilot.sbatch", tmp_path, FAKE_ID="job", SLURM_JOB_ID="1001", SLURM_CLUSTER_NAME="tigris"
    )
    assert job.returncode != 0
    assert "another job has claimed it" in job.stderr


def test_sourcing_the_environment_twice_keeps_the_claims_already_made(tmp_path: Path) -> None:
    """A second source used to reset the list, and the first claim then outlived its job."""
    _fake_uv(tmp_path / ".local" / "bin" / _MACHINE)
    _fake_venv(tmp_path)
    claim = tmp_path / "r.json"
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'set -euo pipefail; source "{_ENV}"; claim_result OUT "{claim}"; source "{_ENV}"',
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert not claim.exists(), "the claim survived an exit that should have released it"


def test_a_result_written_before_the_kill_is_not_reclaimed(tmp_path: Path) -> None:
    """A job can be killed after writing its result; what it left is a measurement, not a claim."""
    home, _ = _prepare(tmp_path)
    result = home / "pilot-outcomes-some-other-run.json"
    result.write_text("written by killed\n")
    result.with_suffix(".json.claim").write_text("tigris:1001\n")
    requeued = _run_job(
        "pilot.sbatch",
        tmp_path,
        FAKE_ID="requeued",
        SLURM_JOB_ID="1001",
        SLURM_CLUSTER_NAME="tigris",
    )
    assert requeued.returncode != 0
    assert "OVERWRITE=1" in requeued.stderr
    assert result.read_text() == "written by killed\n"


def test_a_cancelled_job_releases_its_claim_before_slurm_kills_it(tmp_path: Path) -> None:
    """scancel sends SIGTERM first; the claim goes with it, leaving nothing to reclaim."""
    claim = _kill_mid_job(tmp_path, signal.SIGTERM, SLURM_JOB_ID="1001")
    assert not claim.exists(), "a cancelled job left its claim behind"
    assert not claim.with_suffix(".json.claim").exists()


def test_a_term_between_commands_still_releases_the_claim(tmp_path: Path) -> None:
    """scancel's SIGTERM releases the claim even when no child is running to carry it.

    The shell busy-waits in bash itself rather than in a child, so the signal reaches the shell
    directly rather than failing a command that `set -e` then acts on. Bash runs the EXIT trap
    on a fatal signal, and this holds it to that.
    """
    home = tmp_path / "home"
    home.mkdir()
    claim = home / "result.json"
    shell = subprocess.Popen(
        [
            "bash",
            "-c",
            f'set -euo pipefail; SPHRAGIS_BUILDING_ENV=1 source "{_ENV}"; '
            f'claim_result OUT "{claim}"; while :; do :; done',
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=tmp_path,
        env={
            "HOME": str(home),
            "PATH": "/usr/bin:/bin",
            "SLURM_JOB_ID": "1001",
            "SLURM_CLUSTER_NAME": "tigris",
        },
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not claim.exists():
        time.sleep(0.05)
    assert claim.exists(), "the shell never reached its claim"
    shell.terminate()
    shell.communicate(timeout=30)
    assert not claim.exists(), "a shell signalled between commands kept its claim"


def test_a_finished_job_leaves_no_claim_record_beside_its_result(tmp_path: Path) -> None:
    home, _ = _prepare(tmp_path)
    assert (
        _run_job(
            "pilot.sbatch",
            tmp_path,
            FAKE_ID="done",
            SLURM_JOB_ID="1001",
            SLURM_CLUSTER_NAME="tigris",
        ).returncode
        == 0
    )
    assert sorted(home.glob("*.claim")) == []
