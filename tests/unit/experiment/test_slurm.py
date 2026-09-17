"""sbatch generation for TIGRIS and SPORC, including the flags that are wrong by default."""

from __future__ import annotations

import pytest

from sphragis.experiment.grid import EvalRun
from sphragis.experiment.slurm import SlurmJob, job_for, render

JOB = SlurmJob(
    name="sphragis-base-qt",
    command="uv run python -m sphragis.experiment.model --condition base --eval-org qt",
    output="logs/sphragis-base-qt-%j.log",
)


def test_render_emits_the_tigris_partition_and_a_gh200() -> None:
    script = render(JOB)
    assert "#SBATCH --partition=tigris" in script
    assert "#SBATCH --gres=gpu:gh200:1" in script


def test_render_pins_the_account_to_the_lab_project() -> None:
    # fl-mlm is the Reznik lab's project account. rc-onboard, used until 2026-09-16, is for
    # training only, which Research Computing wrote to say; see slurm.py's docstring for
    # what running on lab compute implies for a sole-authored paper.
    script = render(JOB)
    assert "#SBATCH --account=fl-mlm" in script
    assert "rc-onboard" not in script


def test_render_never_emits_a_qos_line() -> None:
    # The account denies qos_interactive; a --qos line makes the job unschedulable.
    assert "--qos" not in render(JOB)


def test_render_writes_to_a_log_so_nothing_depends_on_a_live_ssh() -> None:
    assert "#SBATCH --output=logs/sphragis-base-qt-%j.log" in render(JOB)


def test_render_requests_mail_on_end_and_failure_with_a_full_address() -> None:
    script = render(JOB)
    assert "#SBATCH --mail-type=END,FAIL" in script
    assert "#SBATCH --mail-user=ajb6289@rit.edu" in script


def test_render_starts_with_a_shebang_and_ends_with_the_command() -> None:
    script = render(JOB)
    assert script.startswith("#!/bin/bash")
    assert script.rstrip().endswith(JOB.command)


def test_render_pins_cc_past_the_blindfolded_gcc() -> None:
    # RIT hides the system gcc; triton JIT-compiles on first generation and would find the
    # wrapper via `which gcc`, killing the job after the model has already loaded.
    assert "export CC=/usr/bin/gcc" in render(JOB)


def test_output_path_has_no_shell_variable() -> None:
    # Slurm does not expand variables in #SBATCH directives; "$HOME/logs/x" makes a
    # directory literally named '$HOME'.
    assert "$" not in JOB.output
    with pytest.raises(ValueError, match="shell variable"):
        render(SlurmJob(name="x", command="true", output="$HOME/logs/x.log"))


def test_render_points_the_model_cache_at_home_not_tmp() -> None:
    # Compute-node /tmp is node-local and wiped; the cache has to live in $HOME.
    script = render(JOB)
    assert "HF_HUB_CACHE=$HOME/hf-cache/hub" in script
    assert "/tmp" not in script


def test_render_does_not_relocate_the_token_with_hf_home() -> None:
    # `hf auth login` stores an OAuth token that refreshes in place. Moving HF_HOME moves
    # the token path too, and the relocated copy silently goes stale.
    assert "HF_HOME=" not in render(JOB)


def test_job_for_names_the_job_after_the_run_id() -> None:
    job = job_for(EvalRun("adapter:qt", "openstack", 2), project_dir="/home/u/ajsoftworks/sphragis")
    assert job.name == "sphragis-adapter-qt-openstack-s2"
    assert "--condition adapter:qt" in job.command
    assert "--eval-org openstack" in job.command
    assert "--seed 2" in job.command


def test_job_for_omits_the_seed_flag_for_the_base_arm() -> None:
    job = job_for(EvalRun("base", "qt", None), project_dir="/home/u/ajsoftworks/sphragis")
    assert "--seed" not in job.command
    assert job.name == "sphragis-base-qt"


def test_job_names_are_unique_across_the_whole_grid() -> None:
    from sphragis.experiment.grid import eval_runs

    names = [
        job_for(r, project_dir="/home/u/x").name for r in eval_runs(("openstack", "qt"), (1, 2, 3))
    ]
    assert len(set(names)) == len(names) == 14


def test_render_rejects_a_time_limit_slurm_cannot_parse() -> None:
    with pytest.raises(ValueError, match="HH:MM:SS"):
        render(SlurmJob(name="x", command="true", output="x.log", time_limit="2 hours"))


def test_one_allocation_covers_the_whole_grid() -> None:
    from sphragis.experiment.slurm import job_for_grid

    job = job_for_grid(
        orgs=("openstack", "qt"), seeds=(1, 2, 3), project_dir="/home/u/ajsoftworks/sphragis"
    )
    assert job.name == "sphragis-grid"
    assert "--grid" in job.command
    assert "--orgs openstack,qt" in job.command
    assert "--seeds 1,2,3" in job.command


def test_the_grid_job_asks_for_one_gpu_not_one_per_cell() -> None:
    from sphragis.experiment.slurm import job_for_grid, render

    job = job_for_grid(orgs=("openstack", "qt"), seeds=(1,), project_dir="/home/u/x")
    assert render(job).count("--gres=") == 1
    assert job.target == "tigris"


def test_the_grid_job_takes_a_longer_default_than_a_single_cell() -> None:
    from sphragis.experiment.slurm import job_for_grid

    single = job_for(EvalRun("base", "qt", None), project_dir="/home/u/x")
    grid = job_for_grid(orgs=("openstack", "qt"), seeds=(1, 2, 3), project_dir="/home/u/x")
    assert grid.time_limit > single.time_limit


def test_the_grid_job_still_refuses_an_unparsable_time_limit() -> None:
    from sphragis.experiment.slurm import job_for_grid, render

    with pytest.raises(ValueError, match="HH:MM:SS"):
        render(
            job_for_grid(orgs=("qt",), seeds=(1,), project_dir="/home/u/x", time_limit="overnight")
        )


def test_job_builders_run_from_the_project_without_resyncing_the_venv() -> None:
    """Plain `uv run` drops the experiment extra; `cd` runs after --output is opened."""
    from sphragis.experiment.slurm import job_for_grid, render

    for job in (
        job_for_grid(orgs=("openstack", "qt"), seeds=(1, 2, 3), project_dir="/home/u/sphragis"),
        job_for(EvalRun("base", "qt", None), project_dir="/home/u/sphragis"),
    ):
        script = render(job)
        assert "uv run --no-sync " in job.command
        assert "cd " not in job.command
        assert "#SBATCH --chdir=/home/u/sphragis" in script


@pytest.mark.parametrize("workdir", ["~/sphragis", "$HOME/sphragis", "sphragis"])
def test_a_workdir_slurm_cannot_resolve_is_refused(workdir: str) -> None:
    with pytest.raises(ValueError, match="absolute"):
        render(SlurmJob(name="x", command="true", output="x.log", workdir=workdir))


# --- Cluster targets ---------------------------------------------------------------------


def test_a_target_names_the_cluster_partition_and_gpu_together() -> None:
    from sphragis.experiment.slurm import TARGETS

    assert TARGETS["tigris"].cluster == "tigris"
    assert TARGETS["tigris"].gres == "gpu:gh200:1"
    assert TARGETS["sporc"].cluster == "sporc"
    assert TARGETS["sporc"].partition == "sporc"
    assert TARGETS["sporc"].gres == "gpu:a100:1"
    assert TARGETS["sporc-h100"].gres == "gpu:h100:1"


def test_flags_route_a_job_to_the_target_cluster() -> None:
    from sphragis.experiment.slurm import sbatch_flags

    assert sbatch_flags("sporc") == [
        "--clusters=sporc",
        "--account=fl-mlm",
        "--partition=sporc",
        "--gres=gpu:a100:1",
    ]


def test_flags_carry_a_time_override_only_when_given() -> None:
    from sphragis.experiment.slurm import sbatch_flags

    assert not any(f.startswith("--time") for f in sbatch_flags("tigris"))
    assert "--time=12:00:00" in sbatch_flags("sporc", time_limit="12:00:00")


def test_flags_refuse_a_time_limit_slurm_cannot_parse() -> None:
    from sphragis.experiment.slurm import sbatch_flags

    with pytest.raises(ValueError, match="HH:MM:SS"):
        sbatch_flags("sporc", time_limit="12h")


def test_flags_refuse_an_unknown_target() -> None:
    from sphragis.experiment.slurm import sbatch_flags

    with pytest.raises(ValueError, match="tigris"):
        sbatch_flags("spork")


def test_flags_refuse_the_training_only_account() -> None:
    # Research Computing, 2026-09-16: rc-onboard is for training, not research jobs.
    from sphragis.experiment.slurm import sbatch_flags

    with pytest.raises(ValueError, match="training"):
        sbatch_flags("tigris", account="rc-onboard")


def test_flags_cli_prints_one_line_of_options(capsys: pytest.CaptureFixture[str]) -> None:
    from sphragis.experiment.slurm import main

    assert main(["flags", "--target", "sporc-h100", "--time", "01:00:00"]) == 0
    assert capsys.readouterr().out == (
        "--clusters=sporc --account=fl-mlm --partition=sporc --gres=gpu:h100:1 --time=01:00:00\n"
    )


def test_flags_cli_reports_a_refusal_without_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from sphragis.experiment.slurm import main

    assert main(["flags", "--target", "spork"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "tigris" in captured.err


def test_render_routes_the_job_to_its_target() -> None:
    script = render(SlurmJob(name="x", command="true", output="x.log", target="sporc"))
    assert "#SBATCH --clusters=sporc" in script
    assert "#SBATCH --partition=sporc" in script
    assert "#SBATCH --gres=gpu:a100:1" in script


def test_a_target_knows_the_machine_its_venv_is_built_for() -> None:
    # Both clusters mount one $HOME, so the venv name is what keeps their builds apart.
    from sphragis.experiment.slurm import TARGETS

    assert TARGETS["tigris"].machine == "aarch64"
    assert TARGETS["sporc"].machine == "x86_64"
    assert TARGETS["sporc-h100"].machine == "x86_64"


def test_cpu_only_flags_request_no_gpu() -> None:
    from sphragis.experiment.slurm import sbatch_flags

    flags = sbatch_flags("sporc", cpu_only=True)
    assert "--clusters=sporc" in flags
    assert "--partition=sporc" in flags
    assert not any(f.startswith("--gres") for f in flags)


def test_machine_cli_prints_the_targets_machine(capsys: pytest.CaptureFixture[str]) -> None:
    from sphragis.experiment.slurm import main

    assert main(["machine", "--target", "sporc"]) == 0
    assert capsys.readouterr().out == "x86_64\n"


@pytest.mark.parametrize("account", ["rc-onboard", "RC-Onboard", " rc-onboard", "rc-onboard\n"])
def test_the_training_only_account_is_refused_however_it_is_written(account: str) -> None:
    from sphragis.experiment.slurm import sbatch_flags

    with pytest.raises(ValueError, match="training"):
        sbatch_flags("sporc", account=account)


def test_an_empty_account_is_refused_because_slurm_would_use_the_default() -> None:
    # The default account on TIGRIS is rc-onboard.
    from sphragis.experiment.slurm import sbatch_flags

    with pytest.raises(ValueError, match="account"):
        sbatch_flags("sporc", account=" ")


def test_render_refuses_the_training_only_account_too() -> None:
    with pytest.raises(ValueError, match="training"):
        render(SlurmJob(name="x", command="true", output="x.log", account="rc-onboard"))


@pytest.mark.parametrize(
    "time_limit", ["99:99:99", "01:60:00", "01:00:60", "00:00:00", "01:00:00\n"]
)
def test_a_time_limit_slurm_would_misread_is_refused(time_limit: str) -> None:
    from sphragis.experiment.slurm import sbatch_flags

    with pytest.raises(ValueError, match="HH:MM:SS"):
        sbatch_flags("sporc", time_limit=time_limit)
