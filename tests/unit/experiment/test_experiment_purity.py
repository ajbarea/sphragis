"""The orchestration modules must stay runnable without a GPU stack."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

_HEAVY = {"torch", "transformers", "peft", "datasets"}
_ORCHESTRATION = (
    "grid.py",
    "power.py",
    "preflight.py",
    "runner.py",
    "slurm.py",
    "training.py",
)
_ROOT = Path(__file__).resolve().parents[3]
_EXPERIMENT = _ROOT / "sphragis" / "experiment"


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("module", _ORCHESTRATION)
def test_orchestration_modules_import_no_gpu_stack(module: str) -> None:
    path = _EXPERIMENT / module
    assert path.is_file(), f"{path} should exist"
    offenders = _imported_roots(path) & _HEAVY
    assert not offenders, (
        f"{module} imports {sorted(offenders)}; only model.py may import a GPU stack"
    )


def test_importing_the_orchestration_modules_pulls_in_no_gpu_stack() -> None:
    # A fresh interpreter, because sys.modules in this one is already polluted by the
    # rest of the suite. Checking it in-process would pass or fail on test ordering.
    probe = (
        "import sys\n"
        "import sphragis.experiment.grid, sphragis.experiment.power, sphragis.experiment.runner\n"
        f"heavy = sorted({sorted(_HEAVY)!r})\n"
        "print(','.join(m for m in heavy if m in sys.modules))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=_ROOT,
        check=True,
    )
    assert result.stdout.strip() == "", f"orchestration imports pulled in {result.stdout.strip()}"


def test_model_is_the_only_exemption() -> None:
    # If model.py ever exists it is allowed the GPU stack; nothing else in the package is.
    exempt = {"model.py", "__init__.py"}
    modules = {p.name for p in _EXPERIMENT.glob("*.py")}
    unchecked = modules - set(_ORCHESTRATION) - exempt
    assert not unchecked, f"new experiment modules are unguarded: {sorted(unchecked)}"
