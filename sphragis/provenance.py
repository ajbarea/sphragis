"""Provenance every artifact in this repo carries: commit, versions, platform, and Slurm job.

Copied from phalanx-fl rather than imported. Twelve lines of standard library is the
wrong thing to take a cross-repo dependency for, especially on a repo whose stated
invariant is to ride the latest Flower while this one must stay reproducible.
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

# The packages whose versions actually pin a run's behaviour.
_TRACKED_PACKAGES = ("torch", "transformers", "peft", "datasets")


def _git(*args: str) -> str | None:
    try:
        return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL, text=True).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for pkg in _TRACKED_PACKAGES:
        try:
            versions[pkg] = version(pkg)
        except PackageNotFoundError:
            continue
    return versions


def _git_record() -> dict[str, Any]:
    """The commit that produced an artifact, which in a cluster job is the one it started on.

    Read at write time alone, a result carries whatever the checkout holds when the job ends,
    and a deploy during a four-hour job would stamp it with code it never ran. The job
    environment records the commit at start (`SPHRAGIS_GIT_COMMIT`), and a checkout that moved
    since is reported rather than silently preferred.
    """
    head = _git("rev-parse", "HEAD")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    started = os.environ.get("SPHRAGIS_GIT_COMMIT") or None
    record: dict[str, Any] = {
        "commit": started or head,
        "branch": os.environ.get("SPHRAGIS_GIT_BRANCH") or branch,
    }
    if started and head and head != started:
        record["checkout_at_write"] = head
    # Code that differs from the commit it names makes the commit a false label: two committed
    # results named a commit whose code could not have produced them, because they were written
    # from a working tree with the fix not yet committed. Only the code is read, from the top of
    # the repository wherever the script ran, so a result directory full of new outputs does not
    # mark every run dirty.
    changed = _git(
        "status", "--porcelain", "--untracked-files=all", "--", ":/sphragis", ":/scripts"
    )
    if changed:
        # Split on the status field rather than slice at a column: `_git` strips its output,
        # which removes the leading space of the first line and shifted every fixed offset by one.
        entries = [line.split(None, 1) for line in changed.splitlines() if line.strip()]
        paths = sorted(entry[1] for entry in entries)
        digest = hashlib.sha256(
            (_git("diff", "HEAD", "--", ":/sphragis", ":/scripts") or "").encode()
        )
        top = Path(_git("rev-parse", "--show-toplevel") or ".")
        for status, path in sorted(entries, key=lambda entry: entry[1]):
            if status == "??" and (top / path).is_file():
                digest.update(path.encode() + b"\0" + (top / path).read_bytes())
        record["uncommitted_code"] = paths
        record["uncommitted_code_sha256"] = digest.hexdigest()
    return record


def provenance_header() -> dict[str, Any]:
    """The static provenance every manifest in this repo carries."""
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "git": _git_record(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": _package_versions(),
    }


_SLURM_FIELDS = {
    "cluster": "SLURM_CLUSTER_NAME",
    "job_id": "SLURM_JOB_ID",
    "account": "SLURM_JOB_ACCOUNT",
    "partition": "SLURM_JOB_PARTITION",
    "node": "SLURMD_NODENAME",
}


def slurm_record() -> dict[str, str | None]:
    """The Slurm job a result came from; every field is None outside a job."""
    return {field: os.environ.get(variable) for field, variable in _SLURM_FIELDS.items()}
