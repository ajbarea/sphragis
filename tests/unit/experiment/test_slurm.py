"""sbatch generation for TIGRIS, including the flags that are wrong by default."""

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


def test_render_pins_the_account_to_ajs_own_access_not_the_lab() -> None:
    # rc-onboard is AJ's own Research Computing access; fl-mlm is the Reznik lab's.
    # This is a sole-authored line, so its compute provenance stays independent.
    script = render(JOB)
    assert "#SBATCH --account=rc-onboard" in script
    assert "fl-mlm" not in script


def test_render_never_emits_a_qos_line() -> None:
    # rc-onboard denies qos_interactive; a --qos line makes the job unschedulable.
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
    job = job_for(EvalRun("adapter:qt", "openstack", 2), project_dir="~/ajsoftworks/sphragis")
    assert job.name == "sphragis-adapter-qt-openstack-s2"
    assert "--condition adapter:qt" in job.command
    assert "--eval-org openstack" in job.command
    assert "--seed 2" in job.command


def test_job_for_omits_the_seed_flag_for_the_base_arm() -> None:
    job = job_for(EvalRun("base", "qt", None), project_dir="~/ajsoftworks/sphragis")
    assert "--seed" not in job.command
    assert job.name == "sphragis-base-qt"


def test_job_names_are_unique_across_the_whole_grid() -> None:
    from sphragis.experiment.grid import eval_runs

    names = [job_for(r, project_dir="~/x").name for r in eval_runs(("openstack", "qt"), (1, 2, 3))]
    assert len(set(names)) == len(names) == 14


def test_render_rejects_a_time_limit_slurm_cannot_parse() -> None:
    with pytest.raises(ValueError, match="HH:MM:SS"):
        render(SlurmJob(name="x", command="true", output="x.log", time_limit="2 hours"))


def test_one_allocation_covers_the_whole_grid() -> None:
    from sphragis.experiment.slurm import job_for_grid

    job = job_for_grid(
        orgs=("openstack", "qt"), seeds=(1, 2, 3), project_dir="~/ajsoftworks/sphragis"
    )
    assert job.name == "sphragis-grid"
    assert "--grid" in job.command
    assert "--orgs openstack,qt" in job.command
    assert "--seeds 1,2,3" in job.command


def test_the_grid_job_asks_for_one_gpu_not_one_per_cell() -> None:
    from sphragis.experiment.slurm import job_for_grid, render

    job = job_for_grid(orgs=("openstack", "qt"), seeds=(1,), project_dir="~/x")
    assert render(job).count("--gres=") == 1
    assert job.gres == "gpu:gh200:1"


def test_the_grid_job_takes_a_longer_default_than_a_single_cell() -> None:
    from sphragis.experiment.slurm import job_for_grid

    single = job_for(EvalRun("base", "qt", None), project_dir="~/x")
    grid = job_for_grid(orgs=("openstack", "qt"), seeds=(1, 2, 3), project_dir="~/x")
    assert grid.time_limit > single.time_limit


def test_the_grid_job_still_refuses_an_unparsable_time_limit() -> None:
    from sphragis.experiment.slurm import job_for_grid, render

    with pytest.raises(ValueError, match="HH:MM:SS"):
        render(job_for_grid(orgs=("qt",), seeds=(1,), project_dir="~/x", time_limit="overnight"))
