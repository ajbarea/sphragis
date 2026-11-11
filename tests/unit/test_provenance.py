"""Run provenance: where a result was produced, not only which commit produced it."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
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


def _dict_literals(tree: ast.AST) -> dict[str, ast.Dict]:
    """Every name assigned a dict literal, so `json.dumps(report)` can be read like a literal.

    Without this the key check sees nothing whenever a script builds its result in a variable,
    which is the readable way to write one, and the test then passes on a script that records no
    provenance at all.
    """
    found: dict[str, ast.Dict] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Dict)
        ):
            found[node.target.id] = node.value
    return found


def _writes_json(tree: ast.AST) -> list[ast.Call]:
    """Every `<path>.write_text(json.dumps(...))`, whoever the path is.

    Matched on structure rather than on the text `args.out.write_text(json.dumps(`, which the
    formatter wraps in six of the scripts already: a substring rule passes them by accident and
    would pass a new script that records nothing by the same accident.
    """
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_text"
            and node.args
            and isinstance(node.args[0], ast.Call)
            and ast.unparse(node.args[0].func) == "json.dumps"
            and node.args[0].args
        ):
            calls.append(node.args[0])
    return calls


def _written_result_keys(script: Path) -> set[str]:
    """Top-level keys of the dict each `args.out.write_text(json.dumps(...))` writes."""
    keys: set[str] = set()
    tree = ast.parse(script.read_text())
    literals = _dict_literals(tree)
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_text"
            and ast.unparse(node.func.value) == "args.out"
            and node.args
            and isinstance(node.args[0], ast.Call)
            and ast.unparse(node.args[0].func) == "json.dumps"
            and node.args[0].args
        ):
            continue
        written = node.args[0].args[0]
        if isinstance(written, ast.Name):
            written = literals.get(written.id)
        if not isinstance(written, ast.Dict):
            continue
        dumped = written
        keys.update(
            k.value for k in dumped.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
        )
    return keys


@pytest.mark.parametrize("script", sorted(_launched_scripts()), ids=lambda p: p.name)
def test_every_script_a_job_launches_records_run_provenance(script: Path) -> None:
    # Results from different GPUs must never be pooled unnoticed, and the record is also the
    # only measurement of peak GPU memory a run leaves behind.
    keys = _written_result_keys(script)
    if not keys:
        # A script that writes something other than a JSON object, an array file say, still has
        # to record where it ran; the key check cannot read inside it, so the call is the test.
        # But a JSON writer whose keys could not be read is a hole, not an exemption: it has to
        # be written in a form this can read, or the check would pass on anything.
        assert not _writes_json(ast.parse(script.read_text())), (
            f"{script.name}: writes a JSON object whose keys this check cannot read; build the "
            "result as a dict literal, or assign one to a name and dump that name"
        )
        text = script.read_text()
        assert "run_provenance(" in text or "provenance_header(" in text, (
            f"{script.name}: writes no JSON object and records no provenance"
        )
        return
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
    monkeypatch.delenv("SPHRAGIS_GIT_TOP", raising=False)
    top = Path("/checkout")
    answers = {
        ("-C", str(top), "rev-parse", "HEAD"): head,
        ("-C", str(top), "rev-parse", "--abbrev-ref", "HEAD"): "main",
        # The current directory is the code's own checkout.
        ("rev-parse", "--show-toplevel"): str(top),
    }
    monkeypatch.setattr(provenance, "_code_checkout", lambda: top)
    monkeypatch.setattr(provenance, "_git", lambda *args: answers[args])
    # A clean tree: the status call that looks for uncommitted code finds none.
    monkeypatch.setattr(provenance, "_changed_code", lambda top: [])


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


def test_a_json_writer_whose_keys_cannot_be_read_is_a_failure_not_an_exemption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The earlier guard tested a substring the formatter wraps, so a wrapped writer that
    recorded nothing would have passed through the loose fallback."""
    script = tmp_path / "sneaky.py"
    script.write_text(
        "import json\n"
        "def build():\n"
        "    return {'results': []}\n"
        "args.out.write_text(\n"
        "    json.dumps(build(), indent=2)\n"
        ")\n"
        "def unrelated():\n"
        "    return provenance_header()\n"
    )
    with pytest.raises(AssertionError, match="keys this check cannot read"):
        test_every_script_a_job_launches_records_run_provenance(script)


def test_a_script_writing_no_json_object_passes_on_its_provenance_call(tmp_path: Path) -> None:
    script = tmp_path / "vectors.py"
    script.write_text("import numpy as np\nnp.savez(args.out, meta=provenance_header())\n")
    test_every_script_a_job_launches_records_run_provenance(script)


def _repo(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(command, cwd=tmp_path, check=True)
    for relative in ("scripts/attack.py", "sphragis/stats.py", "sphragis/provenance.py"):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    return tmp_path


def _code_in(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    """Record as if the `sphragis` package had been imported from `repo`."""
    monkeypatch.setattr(provenance, "_PACKAGE", (repo / "sphragis").resolve())


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_uncommitted_code_is_named_exactly_and_results_do_not_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first status line loses its leading space to a strip; the path must not lose a letter."""
    repo = _repo(tmp_path)
    _code_in(monkeypatch, repo)
    (repo / "scripts" / "attack.py").write_text("x = 2\n")
    (repo / "scripts" / "new_probe.py").write_text("y = 1\n")
    (repo / "datasets" / "results").mkdir(parents=True)
    (repo / "datasets" / "results" / "run.json").write_text("{}")
    monkeypatch.chdir(repo / "datasets")
    monkeypatch.delenv("SPHRAGIS_GIT_COMMIT", raising=False)
    record = provenance._git_record()
    assert record["uncommitted_code"] == ["scripts/attack.py", "scripts/new_probe.py"]
    assert len(record["uncommitted_code_sha256"]) == 64


def test_an_untracked_file_git_would_quote_still_reaches_the_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under `core.quotePath` git C-quotes a non-ASCII name, which named no file on disk."""
    repo = _repo(tmp_path)
    _code_in(monkeypatch, repo)
    subprocess.run(["git", "config", "core.quotePath", "true"], cwd=repo, check=True)
    untracked = repo / "scripts" / "análise.py"
    monkeypatch.chdir(repo)
    monkeypatch.delenv("SPHRAGIS_GIT_COMMIT", raising=False)
    untracked.write_text("y = 1\n")
    first = provenance._git_record()
    untracked.write_text("y = 2\n")
    second = provenance._git_record()
    assert first["uncommitted_code"] == ["scripts/análise.py"]
    assert first["uncommitted_code_sha256"] != second["uncommitted_code_sha256"]


def test_a_staged_rename_records_both_paths_not_an_arrow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _code_in(monkeypatch, repo)
    subprocess.run(["git", "mv", "scripts/attack.py", "scripts/probe.py"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("SPHRAGIS_GIT_COMMIT", raising=False)
    assert provenance._git_record()["uncommitted_code"] == ["scripts/attack.py", "scripts/probe.py"]


def test_a_name_that_is_not_utf8_is_shown_escaped_and_still_digested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writers dump the record with ensure_ascii=False, which a lone surrogate would stop."""
    repo = _repo(tmp_path)
    _code_in(monkeypatch, repo)
    untracked = os.fsencode(repo / "scripts") + b"/bad\xff.py"
    monkeypatch.chdir(repo)
    monkeypatch.delenv("SPHRAGIS_GIT_COMMIT", raising=False)
    Path(os.fsdecode(untracked)).write_text("y = 1\n")
    first = provenance._git_record()
    Path(os.fsdecode(untracked)).write_text("y = 2\n")
    second = provenance._git_record()
    assert first["uncommitted_code"] == ["scripts/bad\\xff.py"]
    json.dumps(first, ensure_ascii=False).encode()
    assert first["uncommitted_code_sha256"] != second["uncommitted_code_sha256"]


def test_a_copy_records_its_new_path_and_reads_its_source_field_as_no_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _code_in(monkeypatch, repo)
    body = "".join(f"x{i} = {i}\n" for i in range(40))
    (repo / "scripts" / "attack.py").write_text(body)
    subprocess.run(["git", "commit", "-qam", "longer"], cwd=repo, check=True)
    subprocess.run(["git", "config", "status.renames", "copies"], cwd=repo, check=True)
    # Git looks for a copy's source among the changed files only.
    (repo / "scripts" / "attack.py").write_text(body + "y = 2\n")
    (repo / "scripts" / "attack2.py").write_text(body)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("SPHRAGIS_GIT_COMMIT", raising=False)
    assert [status for status, _ in provenance._changed_code(repo)] == [b"M ", b"C "]
    assert provenance._git_record()["uncommitted_code"] == [
        "scripts/attack.py",
        "scripts/attack2.py",
    ]


def test_a_clean_tree_carries_no_uncommitted_code_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _code_in(monkeypatch, repo)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("SPHRAGIS_GIT_COMMIT", raising=False)
    assert "uncommitted_code" not in provenance._git_record()


def test_the_commit_is_the_code_checkouts_when_run_from_another_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sphragis#264: a script run from the main checkout with a worktree's code first on
    PYTHONPATH named main's commit. The code's checkout is named; the other is recorded beside."""
    code = _repo(tmp_path / "worktree")
    (code / "sphragis" / "stats.py").write_text("x = 2\n")
    subprocess.run(["git", "commit", "-qam", "fix"], cwd=code, check=True)
    main = _repo(tmp_path / "main")
    _code_in(monkeypatch, code)
    monkeypatch.chdir(main)
    monkeypatch.delenv("SPHRAGIS_GIT_COMMIT", raising=False)
    monkeypatch.delenv("SPHRAGIS_GIT_BRANCH", raising=False)
    record = provenance._git_record()
    assert record["commit"] == _head(code) != _head(main)
    assert record["cwd_commit"] == _head(main)
    # Uncommitted code is read in the code's checkout, not the current directory's.
    (main / "scripts" / "attack.py").write_text("x = 3\n")
    assert "uncommitted_code" not in provenance._git_record()
    (code / "scripts" / "attack.py").write_text("x = 3\n")
    assert provenance._git_record()["uncommitted_code"] == ["scripts/attack.py"]


def test_the_code_checkout_run_from_records_no_second_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _code_in(monkeypatch, repo)
    monkeypatch.chdir(repo / "scripts")
    monkeypatch.delenv("SPHRAGIS_GIT_COMMIT", raising=False)
    record = provenance._git_record()
    assert record["commit"] == _head(repo)
    assert "cwd_commit" not in record


def test_a_package_installed_inside_a_checkout_names_no_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy under a venv in the checkout sits in its tree, and is not the code it tracks."""
    repo = _repo(tmp_path)
    installed = repo / ".venv" / "lib" / "site-packages" / "sphragis"
    installed.mkdir(parents=True)
    (installed / "provenance.py").write_text("x = 1\n")
    monkeypatch.setattr(provenance, "_PACKAGE", installed.resolve())
    monkeypatch.chdir(tmp_path.parent)
    monkeypatch.delenv("SPHRAGIS_GIT_COMMIT", raising=False)
    monkeypatch.delenv("SPHRAGIS_GIT_BRANCH", raising=False)
    assert provenance._code_checkout() is None
    assert provenance._git_record() == {"commit": None, "branch": None}


def test_an_untracked_package_at_a_checkouts_top_names_no_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    subprocess.run(["git", "rm", "-q", "--cached", "sphragis/provenance.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "untrack"], cwd=repo, check=True)
    _code_in(monkeypatch, repo)
    assert provenance._code_checkout() is None


def test_a_job_outside_any_checkout_keeps_the_commit_it_started_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provenance, "_code_checkout", lambda: None)
    monkeypatch.chdir(tmp_path)
    # A job queued before cluster-env.sh recorded its checkout.
    monkeypatch.delenv("SPHRAGIS_GIT_TOP", raising=False)
    monkeypatch.setenv("SPHRAGIS_GIT_COMMIT", "a" * 40)
    monkeypatch.setenv("SPHRAGIS_GIT_BRANCH", "main")
    assert provenance._git_record() == {"commit": "a" * 40, "branch": "main"}


def test_a_job_started_in_another_checkout_names_the_codes_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cluster-env.sh records the job's checkout; code imported from another one is that one's."""
    code = _repo(tmp_path / "code")
    job = _repo(tmp_path / "job")
    (job / "scripts" / "attack.py").write_text("x = 9\n")
    subprocess.run(["git", "commit", "-qam", "job"], cwd=job, check=True)
    _code_in(monkeypatch, code)
    monkeypatch.chdir(job)
    monkeypatch.setenv("SPHRAGIS_GIT_COMMIT", _head(job))
    monkeypatch.setenv("SPHRAGIS_GIT_BRANCH", "job-branch")
    monkeypatch.setenv("SPHRAGIS_GIT_TOP", str(job))
    record = provenance._git_record()
    assert record["commit"] == _head(code)
    assert record["job_commit"] == _head(job)
    assert "checkout_at_write" not in record
    # A job in the code's own checkout keeps the commit it started on.
    monkeypatch.setenv("SPHRAGIS_GIT_TOP", str(code))
    monkeypatch.setenv("SPHRAGIS_GIT_COMMIT", "a" * 40)
    record = provenance._git_record()
    assert record["commit"] == "a" * 40
    assert record["checkout_at_write"] == _head(code)
    assert "job_commit" not in record


def test_a_job_whose_code_is_in_no_checkout_does_not_borrow_the_jobs_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _repo(tmp_path)
    monkeypatch.setattr(provenance, "_code_checkout", lambda: None)
    monkeypatch.chdir(job)
    monkeypatch.setenv("SPHRAGIS_GIT_COMMIT", _head(job))
    monkeypatch.setenv("SPHRAGIS_GIT_TOP", str(job))
    record = provenance._git_record()
    assert record["commit"] is None
    assert record["job_commit"] == _head(job)


def test_cluster_env_records_the_checkout_a_job_starts_in() -> None:
    env = (_ROOT / "sphragis" / "experiment" / "cluster-env.sh").read_text()
    assert 'SPHRAGIS_GIT_TOP="$(git rev-parse --show-toplevel' in env
    assert re.search(r"^export .*\bSPHRAGIS_GIT_TOP\b", env, re.MULTILINE)


def test_the_real_package_is_read_from_this_checkout() -> None:
    assert provenance._code_checkout() == _ROOT


#: The convention this guards: the provenance key belongs in the payload where the payload is
#: built, not beside one `write_text` call, because several scripts write the same report more
#: than once as it fills and every one of those writes has to carry it.
#:
#: Scripts that write into `datasets/results` without a provenance header, each for a stated
#: reason. Anything else that writes a result and does not record where it came from is the
#: defect this test exists for: a committed artifact whose producing commit is unknown cannot
#: be re-derived, and the repository's deploy rule assumes every stage records its SHA.
PROVENANCE_EXEMPT = {
    # Corpus stages, whose manifests carry their own window and hash records.
    "calibration.py": "writes corpus halves and a manifest, not a measurement",
    "project_corpora.py": "writes per-project corpora and a manifest, not a measurement",
    "contamination_windows.py": "writes window slices of the corpus, not a measurement",
    "project_windows.py": "writes window slices of the corpus, not a measurement",
    "label_audit_sample.py": "writes a local annotation sheet of corpus text, never committed",
    "label_audit_blind.py": "writes blinded batches of corpus text and their key, never committed",
    "label_audit_page.py": "writes the local label-audit pages, corpus text, never committed",
    # Tools that print rather than record.
    "estimands.py": "prints both estimands for a committed run; writes nothing",
    "preflight_pilot.py": "checks the cluster before a job; writes nothing",
    "bench_throughput.py": "a timing probe, not a study result",
    "power_rq1.py": "prints; the sensitivity analysis that replaced it writes its own artifact",
    "jgit_fixtures.py": "writes a test fixture from JGit, recording the jar and its sha256",
    "prune_site.py": "edits the built docs site in place, not a study result",
    "log_to_blog.py": "writes the gitignored docs journal from the research log, not a result",
    # Rewrites artifacts in place rather than producing one. Stamping it would overwrite the
    # provenance of the run being redacted, which is the record that has to survive.
    "redact_identities.py": "redacts committed artifacts; produces no measurement of its own",
}


def test_every_script_that_writes_a_result_records_its_provenance() -> None:
    missing = []
    for path in sorted((_ROOT / "scripts").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "write_text" not in source:
            continue
        if "provenance_header" in source or "run_provenance" in source:
            continue
        if path.name in PROVENANCE_EXEMPT:
            continue
        missing.append(path.name)
    assert not missing, (
        f"{missing} write files and record no provenance; add provenance_header() to the "
        "payload, or name the script in PROVENANCE_EXEMPT with the reason it writes no result"
    )


def test_every_provenance_exemption_names_a_script_that_exists() -> None:
    names = {path.name for path in (_ROOT / "scripts").glob("*.py")}
    assert set(PROVENANCE_EXEMPT) <= names, sorted(set(PROVENANCE_EXEMPT) - names)
