"""sbatch generation for RIT Research Computing: TIGRIS, and SPORC as the fallback.

Every non-obvious flag here was verified against the live clusters rather than recalled.
TIGRIS (2026-09-14): the partition is `tigris`, GH200 nodes expose `gpu:gh200:1`, and
`rc-onboard` **denies** `qos_interactive`, so emitting any `--qos` line makes the job
unschedulable. SPORC (2026-09-17): x86_64 A100 40 GB nodes plus one node of H100 80 GB, both in
the `sporc` partition, on driver 610 (CUDA 13.3) despite a stale `cuda11` node feature tag.
Both clusters mount the same `$HOME`, and the TIGRIS login node submits to SPORC with
`--clusters=sporc`, so one checkout and one ssh session serve both. Compute-node `/tmp` is
node-local, so the model cache lives in `$HOME`, which carries a 1 TB quota.

The job scripts under `scripts/` carry the TIGRIS target in their `#SBATCH` lines. Retargeting
passes `sbatch_flags` on the command line, which Slurm ranks above both `SBATCH_*` variables and
`#SBATCH` directives, so a script never needs editing to move clusters.

The account is stated explicitly rather than left to the Slurm default, because the choice
is not about scheduling.

It used to be `rc-onboard`, AJ's own Research Computing access, on the reasoning that this
study is a sole-authored first-author line and running it on the lab's `fl-mlm` would
entangle its resource provenance with a group whose contribution the paper does not
otherwise claim. Research Computing wrote on 2026-09-16 that `rc-onboard` is for training
only, so that reasoning rested on an option that was never available: the account is now
`fl-mlm`.

The implication the old note anticipated is therefore live rather than hypothetical. Lab
compute normally carries an acknowledgment expectation, and this paper's author list does
not currently reflect one. That is a conversation with Dr. Reznik, not something to settle
in a docstring, and it is recorded here so the next person to read this file knows the
question is open rather than answered.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.resources import files

from sphragis.experiment.grid import EvalRun, run_id

_TIME = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")
_MAIL_USER = "ajb6289@rit.edu"
_ACCOUNT = "fl-mlm"  # the Reznik lab's; see the module docstring on what that implies.
_TRAINING_ONLY_ACCOUNTS = frozenset({"rc-onboard"})
# One copy of the job environment, shared with scripts/*.sbatch.
_JOB_ENV = files("sphragis.experiment").joinpath("cluster-env.sh").read_text().strip()


@dataclass(frozen=True)
class Target:
    """Where a job runs. The GPU is part of the target: a partition alone does not pick one."""

    cluster: str
    partition: str
    gres: str
    machine: str


TARGETS: dict[str, Target] = {
    "tigris": Target(cluster="tigris", partition="tigris", gres="gpu:gh200:1", machine="aarch64"),
    "sporc": Target(cluster="sporc", partition="sporc", gres="gpu:a100:1", machine="x86_64"),
    "sporc-h100": Target(cluster="sporc", partition="sporc", gres="gpu:h100:1", machine="x86_64"),
}
DEFAULT_TARGET = "tigris"


def _require_time(time_limit: str) -> None:
    if not _TIME.match(time_limit):
        raise ValueError(f"time_limit must be HH:MM:SS, got {time_limit!r}")


def _require_target(name: str) -> Target:
    if name not in TARGETS:
        raise ValueError(f"unknown target {name!r}; expected one of {', '.join(TARGETS)}")
    return TARGETS[name]


def sbatch_flags(
    target: str,
    *,
    account: str = _ACCOUNT,
    time_limit: str | None = None,
    cpu_only: bool = False,
) -> list[str]:
    """Command-line options that send an unmodified job script to `target`.

    `cpu_only` drops the GPU, for work such as building the venv that should not queue for one.
    """
    where = _require_target(target)
    if account in _TRAINING_ONLY_ACCOUNTS:
        raise ValueError(
            f"{account} is for training only (Research Computing, 2026-09-16); "
            "research jobs run under a project account"
        )
    flags = [
        f"--clusters={where.cluster}",
        f"--account={account}",
        f"--partition={where.partition}",
    ]
    if not cpu_only:
        flags.append(f"--gres={where.gres}")
    if time_limit is not None:
        _require_time(time_limit)
        flags.append(f"--time={time_limit}")
    return flags


@dataclass(frozen=True)
class SlurmJob:
    """One sbatch submission."""

    name: str
    command: str
    output: str
    target: str = DEFAULT_TARGET
    cpus: int = 8
    mem: str = "64G"
    time_limit: str = "02:00:00"
    account: str = _ACCOUNT
    workdir: str | None = None


def render(job: SlurmJob) -> str:
    """The sbatch script for one job.

    No `--qos` line is emitted, deliberately: the default account denies the interactive
    QoS and a job carrying one never starts.
    """
    _require_time(job.time_limit)
    where = _require_target(job.target)
    if "$" in job.output:
        # Slurm does not expand shell variables in #SBATCH directives. An --output of
        # "$HOME/logs/x.log" silently creates a directory literally named '$HOME'.
        raise ValueError(f"--output cannot contain a shell variable, got {job.output!r}")
    if job.workdir is not None and not job.workdir.startswith("/"):
        # Neither $HOME nor ~ is expanded in a directive, so a non-absolute --chdir names a
        # directory relative to wherever sbatch happened to run.
        raise ValueError(f"workdir must be an absolute path, got {job.workdir!r}")
    chdir = "" if job.workdir is None else f"#SBATCH --chdir={job.workdir}\n"
    return f"""#!/bin/bash
#SBATCH --job-name={job.name}
#SBATCH --clusters={where.cluster}
#SBATCH --account={job.account}
#SBATCH --partition={where.partition}
#SBATCH --gres={where.gres}
#SBATCH --cpus-per-task={job.cpus}
#SBATCH --mem={job.mem}
#SBATCH --time={job.time_limit}
#SBATCH --output={job.output}
{chdir}#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user={_MAIL_USER}

set -euo pipefail
{_JOB_ENV}

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
    # --no-sync: a plain `uv run` re-syncs the venv to the base dependencies, which removes
    # the experiment extra and fails on `import peft` only after the job has left the queue.
    # --chdir, not `cd`: --output is opened before the script body runs, so a relative log
    # path would resolve against the submission directory rather than the project.
    command = (
        "uv run --no-sync python -m sphragis.experiment.model --grid "
        f"--orgs {','.join(orgs)} --seeds {','.join(str(s) for s in seeds)}"
    )
    return SlurmJob(
        name="sphragis-grid",
        command=command,
        output="logs/sphragis-grid-%j.log",
        time_limit=time_limit,
        mem="96G",
        workdir=project_dir,
    )


def job_for(run: EvalRun, *, project_dir: str, time_limit: str = "02:00:00") -> SlurmJob:
    """The job that evaluates one grid cell."""
    name = "sphragis-" + run_id(run).replace("|", "-").replace(":", "-")
    seed = "" if run.seed is None else f" --seed {run.seed}"
    command = (
        "uv run --no-sync python -m sphragis.experiment.model "
        f"--condition {run.condition} --eval-org {run.eval_org}{seed}"
    )
    return SlurmJob(
        name=name,
        command=command,
        output=f"logs/{name}-%j.log",
        time_limit=time_limit,
        workdir=project_dir,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sphragis.experiment.slurm")
    commands = parser.add_subparsers(dest="command", required=True)
    flags = commands.add_parser("flags", help="sbatch options that send a job script to a target")
    flags.add_argument("--target", default=DEFAULT_TARGET, help=", ".join(TARGETS))
    flags.add_argument("--account", default=_ACCOUNT)
    flags.add_argument("--time", dest="time_limit", help="HH:MM:SS, overriding the script's")
    flags.add_argument("--cpu-only", action="store_true", help="request no GPU")
    machine = commands.add_parser("machine", help="the machine type a target's venv is built for")
    machine.add_argument("--target", default=DEFAULT_TARGET, help=", ".join(TARGETS))
    args = parser.parse_args(argv)
    try:
        if args.command == "machine":
            print(_require_target(args.target).machine)
            return 0
        options = sbatch_flags(
            args.target,
            account=args.account,
            time_limit=args.time_limit,
            cpu_only=args.cpu_only,
        )
    except ValueError as refusal:
        print(refusal, file=sys.stderr)
        return 2
    print(" ".join(options))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
