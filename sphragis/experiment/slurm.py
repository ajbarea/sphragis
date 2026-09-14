"""sbatch generation for TIGRIS.

Every non-obvious flag here was verified against the live cluster on 2026-09-14 rather
than recalled: the partition is `tigris`, GH200 nodes expose `gpu:gh200:1`, and the
default account `rc-onboard` **denies** `qos_interactive`, so emitting any `--qos` line
makes the job unschedulable. Compute-node `/tmp` is node-local, so the model cache lives
in `$HOME`, which carries a 1 TB quota.

The account is stated explicitly rather than left to the Slurm default, because the choice
is not about scheduling. `rc-onboard` is AJ's own Research Computing access, predating the
lab. `fl-mlm` is the Reznik lab's. This study is a sole-authored, first-author line that is
deliberately independent of the lab, so running it on lab compute would entangle its
resource provenance with a group whose contribution the paper does not otherwise claim,
and that normally carries an acknowledgment expectation at least. Keeping it on
`rc-onboard` keeps the provenance as clean as the authorship. If the queue makes that
untenable, it becomes a conversation with Dr. Reznik with those implications on the table,
not a silent default flip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sphragis.experiment.grid import EvalRun, run_id

_TIME = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")
_MAIL_USER = "ajb6289@rit.edu"
_ACCOUNT = "rc-onboard"  # AJ's own access, not the lab's fl-mlm; see the module docstring.
# Cache only. HF_HOME would also relocate the token, and `hf auth login` writes an OAuth
# token that refreshes itself in place — a copy elsewhere goes stale and starts failing.
_HF_HUB_CACHE = "$HOME/hf-cache/hub"


@dataclass(frozen=True)
class SlurmJob:
    """One sbatch submission."""

    name: str
    command: str
    output: str
    partition: str = "tigris"
    gres: str = "gpu:gh200:1"
    cpus: int = 8
    mem: str = "64G"
    time_limit: str = "02:00:00"
    account: str = _ACCOUNT


def render(job: SlurmJob) -> str:
    """The sbatch script for one job.

    No `--qos` line is emitted, deliberately: the default account denies the interactive
    QoS and a job carrying one never starts.
    """
    if not _TIME.match(job.time_limit):
        raise ValueError(f"time_limit must be HH:MM:SS, got {job.time_limit!r}")
    return f"""#!/bin/bash
#SBATCH --job-name={job.name}
#SBATCH --account={job.account}
#SBATCH --partition={job.partition}
#SBATCH --gres={job.gres}
#SBATCH --cpus-per-task={job.cpus}
#SBATCH --mem={job.mem}
#SBATCH --time={job.time_limit}
#SBATCH --output={job.output}
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user={_MAIL_USER}

set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
export HF_HUB_CACHE={_HF_HUB_CACHE}
export TOKENIZERS_PARALLELISM=false

{job.command}"""


def job_for_grid(
    *,
    orgs: tuple[str, ...],
    seeds: tuple[int, ...],
    project_dir: str,
    time_limit: str = "12:00:00",
) -> SlurmJob:
    """One allocation that walks the whole grid.

    Per-cell jobs are the wrong shape on this cluster. The grid is 14 evaluations plus 6
    training runs, and at the fairshare measured on 2026-09-14 (0.006, against a pool with
    3.9e9 raw usage) a freshly submitted job was estimated to start thirteen days out. That
    is twenty independent waits. One allocation queues once and holds the GPU for the
    duration, which is also what RC asks for: request, run, release.

    `job_for` stays for reruns of a single cell after a failure.
    """
    command = (
        f"cd {project_dir} && uv run python -m sphragis.experiment.model --grid "
        f"--orgs {','.join(orgs)} --seeds {','.join(str(s) for s in seeds)}"
    )
    return SlurmJob(
        name="sphragis-grid",
        command=command,
        output="$HOME/logs/sphragis-grid-%j.log",
        time_limit=time_limit,
        mem="96G",
    )


def job_for(run: EvalRun, *, project_dir: str, time_limit: str = "02:00:00") -> SlurmJob:
    """The job that evaluates one grid cell."""
    name = "sphragis-" + run_id(run).replace("|", "-").replace(":", "-")
    seed = "" if run.seed is None else f" --seed {run.seed}"
    command = (
        f"cd {project_dir} && uv run python -m sphragis.experiment.model "
        f"--condition {run.condition} --eval-org {run.eval_org}{seed}"
    )
    return SlurmJob(
        name=name, command=command, output=f"$HOME/logs/{name}-%j.log", time_limit=time_limit
    )
