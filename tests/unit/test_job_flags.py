"""Every flag a cluster job passes to a script is one that script's parser defines.

A job is only run on the cluster, so a flag renamed or removed in a script leaves the job failing
at argparse hours after submission, where no local test sees it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JOBS = sorted((ROOT / "scripts").glob("*.sbatch"))


def _invocations(job: Path) -> list[tuple[str, set[str]]]:
    """Each `python scripts/<name>.py` call in a job, with the flags on its continued lines."""
    calls: list[tuple[str, set[str]]] = []
    lines = job.read_text().splitlines()
    i = 0
    while i < len(lines):
        match = re.search(r"python (?:-m \S+ )?scripts/(\w+)\.py", lines[i])
        if not match:
            i += 1
            continue
        text = lines[i]
        while text.rstrip().endswith("\\") and i + 1 < len(lines):
            i += 1
            text += "\n" + lines[i]
        calls.append((match.group(1), set(re.findall(r"(?<![\w-])(--[a-z][\w-]*)", text))))
        i += 1
    return calls


def test_there_are_jobs_calling_scripts() -> None:
    assert any(_invocations(job) for job in JOBS)


@pytest.mark.parametrize("job", JOBS, ids=lambda p: p.name)
def test_every_flag_a_job_passes_is_defined_by_its_script(job: Path) -> None:
    for name, flags in _invocations(job):
        source = (ROOT / "scripts" / f"{name}.py").read_text()
        defined = set(re.findall(r"""add_argument\(\s*["'](--[\w-]+)["']""", source))
        missing = flags - defined - {"--no-sync"}
        assert not missing, f"{job.name} passes {sorted(missing)} to scripts/{name}.py"
