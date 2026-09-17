"""Run provenance: where a result was produced, not only which commit produced it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sphragis.provenance import slurm_record

_ROOT = Path(__file__).resolve().parents[2]


def test_a_slurm_job_records_its_cluster_account_and_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in {
        "SLURM_CLUSTER_NAME": "sporc",
        "SLURM_JOB_ID": "21705076",
        "SLURM_JOB_ACCOUNT": "fl-mlm",
        "SLURM_JOB_PARTITION": "sporc",
        "SLURMD_NODENAME": "skl-a-48",
    }.items():
        monkeypatch.setenv(key, value)
    assert slurm_record() == {
        "cluster": "sporc",
        "job_id": "21705076",
        "account": "fl-mlm",
        "partition": "sporc",
        "node": "skl-a-48",
    }


def test_outside_slurm_every_field_is_null_rather_than_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "SLURM_CLUSTER_NAME",
        "SLURM_JOB_ID",
        "SLURM_JOB_ACCOUNT",
        "SLURM_JOB_PARTITION",
        "SLURMD_NODENAME",
    ):
        monkeypatch.delenv(key, raising=False)
    assert slurm_record() == dict.fromkeys(("cluster", "job_id", "account", "partition", "node"))


def _launched_scripts() -> set[Path]:
    launched = set()
    for sbatch in (_ROOT / "scripts").glob("*.sbatch"):
        for name in re.findall(r"python (scripts/\w+\.py)", sbatch.read_text()):
            launched.add(_ROOT / name)
    return launched


def test_some_scripts_are_launched_by_a_job() -> None:
    assert len(_launched_scripts()) >= 4


@pytest.mark.parametrize("script", sorted(_launched_scripts()), ids=lambda p: p.name)
def test_every_script_a_job_launches_records_run_provenance(script: Path) -> None:
    # Results from different GPUs must never be pooled unnoticed, and the record is also the
    # only measurement of peak GPU memory a run leaves behind.
    assert '"provenance": run_provenance()' in script.read_text()
