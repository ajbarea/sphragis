"""Run provenance: where a result was produced, not only which commit produced it."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from sphragis import provenance
from sphragis.provenance import provenance_header, slurm_record

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
            and node.args[0].args
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


def _defined_names(module: Path) -> set[str]:
    """Every name a module binds at top level: defs, classes, assignments and imports."""
    names: set[str] = set()
    for node in ast.parse(module.read_text()).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(t.id for t in targets if isinstance(t, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
    return names


@pytest.mark.parametrize("script", sorted(_launched_scripts()), ids=lambda p: p.name)
def test_every_name_a_job_script_imports_from_sphragis_exists(script: Path) -> None:
    # A job script that imports a name its module does not define fails at import, which on
    # the cluster is after the queue wait. Checked statically, because the model stack those
    # modules need is not installed in CI, so importing them here is not an option. Caught
    # scripts/decoding_check.py taking run_provenance from sphragis.provenance, where it is
    # not defined; it lives in sphragis.experiment.model.
    missing = []
    for node in ast.walk(ast.parse(script.read_text())):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if not node.module.startswith("sphragis"):
            continue
        parts = node.module.split(".")
        module = _ROOT.joinpath(*parts).with_suffix(".py")
        if not module.is_file():
            module = _ROOT.joinpath(*parts, "__init__.py")
        defined = _defined_names(module)
        missing.extend(f"{node.module}.{a.name}" for a in node.names if a.name not in defined)
    assert not missing, f"{script.name} imports names that do not exist: {missing}"


def _checkout_at(monkeypatch: pytest.MonkeyPatch, head: str | None) -> None:
    answers = {("rev-parse", "HEAD"): head, ("rev-parse", "--abbrev-ref", "HEAD"): "main"}
    monkeypatch.setattr(provenance, "_git", lambda *args: answers[args])


def test_a_job_records_the_commit_it_started_on_not_the_one_deployed_since(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deploy during a four-hour job must not stamp its result with code it never ran."""
    _checkout_at(monkeypatch, "b" * 40)
    monkeypatch.setenv("SPHRAGIS_GIT_COMMIT", "a" * 40)
    monkeypatch.setenv("SPHRAGIS_GIT_BRANCH", "feat/x")
    assert provenance_header()["git"] == {
        "commit": "a" * 40,
        "branch": "feat/x",
        "checkout_at_write": "b" * 40,
    }


def test_an_unmoved_checkout_reports_no_second_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    _checkout_at(monkeypatch, "a" * 40)
    monkeypatch.setenv("SPHRAGIS_GIT_COMMIT", "a" * 40)
    assert "checkout_at_write" not in provenance_header()["git"]


@pytest.mark.parametrize("started", [None, ""])
def test_outside_a_job_the_checkout_is_the_commit(
    monkeypatch: pytest.MonkeyPatch, started: str | None
) -> None:
    """cluster-env.sh exports an empty commit outside a checkout; that must read as unset."""
    _checkout_at(monkeypatch, "c" * 40)
    monkeypatch.delenv("SPHRAGIS_GIT_BRANCH", raising=False)
    if started is None:
        monkeypatch.delenv("SPHRAGIS_GIT_COMMIT", raising=False)
    else:
        monkeypatch.setenv("SPHRAGIS_GIT_COMMIT", started)
    assert provenance_header()["git"] == {"commit": "c" * 40, "branch": "main"}
