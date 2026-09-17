"""Run provenance: where a result was produced, not only which commit produced it."""

from __future__ import annotations

import ast
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


def _written_result_keys(script: Path) -> set[str]:
    """Top-level keys of the dict each `args.out.write_text(json.dumps({...}))` writes."""
    keys: set[str] = set()
    for node in ast.walk(ast.parse(script.read_text())):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_text"
            and ast.unparse(node.func.value) == "args.out"
            and node.args
            and isinstance(node.args[0], ast.Call)
            and ast.unparse(node.args[0].func) == "json.dumps"
            and isinstance(node.args[0].args[0], ast.Dict)
        ):
            continue
        dumped = node.args[0].args[0]
        keys.update(
            k.value for k in dumped.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
        )
    return keys


@pytest.mark.parametrize("script", sorted(_launched_scripts()), ids=lambda p: p.name)
def test_every_script_a_job_launches_records_run_provenance(script: Path) -> None:
    # Results from different GPUs must never be pooled unnoticed, and the record is also the
    # only measurement of peak GPU memory a run leaves behind.
    keys = _written_result_keys(script)
    assert keys, f"{script.name}: no args.out.write_text(json.dumps({{...}})) found"
    assert "provenance" in keys


def test_training_logs_gpu_memory_before_anything_later_can_crash() -> None:
    # A run that dies after training (out of memory, or a GH200-sized --time) writes no result
    # file, so the peak has to reach the job log as each adapter finishes.
    model = (_ROOT / "sphragis" / "experiment" / "model.py").read_text()
    body = model[
        model.index("def train_adapter(") : model.index("\ndef ", model.index("def train_adapter("))
    ]
    assert "gpu_record()" in body
