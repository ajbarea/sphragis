"""Provenance every artifact in this repo carries: commit, versions, platform, and Slurm job.

Copied from phalanx-fl rather than imported: phalanx-fl rides the latest Flower, and this repo
must stay reproducible, so a cross-repo dependency for a short standard-library module is the
wrong trade.
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

# Packages whose versions pin a run's behaviour.
_TRACKED_PACKAGES = ("torch", "transformers", "peft", "datasets", "pygments")


#: The imported package's own directory, whose checkout is the code that writes an artifact.
_PACKAGE = Path(__file__).resolve().parent


def _git_bytes(*args: str) -> bytes | None:
    try:
        return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL)
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def _git(*args: str) -> str | None:
    try:
        return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL, text=True).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def _code_checkout() -> Path | None:
    """The top of the checkout the imported `sphragis` package belongs to, or None.

    None unless the package is that checkout's own tracked `sphragis/`: a copy installed under a
    venv inside some checkout sits in that checkout's tree too, and git there would name a
    commit whose code was never imported.
    """
    top = _git("-C", str(_PACKAGE), "rev-parse", "--show-toplevel")
    if not top or Path(top).resolve() / "sphragis" != _PACKAGE:
        return None
    if _git("-C", top, "ls-files", "--error-unmatch", "sphragis/provenance.py") is None:
        return None
    return Path(top)


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for pkg in _TRACKED_PACKAGES:
        try:
            versions[pkg] = version(pkg)
        except PackageNotFoundError:
            continue
    return versions


def _changed_code(top: Path) -> list[tuple[bytes, bytes]]:
    """(status, path) for every code path under `top` that differs from its HEAD, a rename giving
    both paths.

    Read with `-z`, whose paths are raw bytes: the line format C-quotes a non-ASCII name and
    writes a rename as `old -> new`, neither of which names a file on disk. A copy's source is
    unchanged, so only its new path counts.
    """
    raw = _git_bytes(
        "-C",
        str(top),
        "status",
        "--porcelain",
        "-z",
        "--untracked-files=all",
        "--",
        ":/sphragis",
        ":/scripts",
    )
    if raw is None:
        return []
    fields = iter(raw.split(b"\0"))
    entries: list[tuple[bytes, bytes]] = []
    for field in fields:
        if not field:
            continue
        status, path = field[:2], field[3:]
        entries.append((status, path))
        if b"R" in status or b"C" in status:
            source = next(fields, b"")
            if b"R" in status and source:
                entries.append((status, source))
    return entries


def _git_record() -> dict[str, Any]:
    """The commit that produced an artifact, which in a cluster job is the one it started on.

    Read at write time alone, a result carries whatever the checkout holds when the job ends,
    and a deploy during a four-hour job would stamp it with code it never ran. The job
    environment records the commit at start (`SPHRAGIS_GIT_COMMIT`), and a checkout that moved
    since is reported rather than silently preferred.

    Git is asked in the checkout the package was imported from (`_code_checkout`), never the
    current directory: three pilots run from the main checkout with a worktree's code first on
    `PYTHONPATH` named main's commit, whose code could not have written them. A current directory
    in another checkout is recorded beside the commit as `cwd_commit`, since its files may be the
    script's inputs; a package outside any checkout records no commit of its own. A job's start
    commit names the code only when the job started in the code's checkout (`SPHRAGIS_GIT_TOP`);
    otherwise it is recorded beside as `job_commit`.
    """
    top = _code_checkout()
    head = _git("-C", str(top), "rev-parse", "HEAD") if top else None
    branch = _git("-C", str(top), "rev-parse", "--abbrev-ref", "HEAD") if top else None
    started = os.environ.get("SPHRAGIS_GIT_COMMIT") or None
    started_branch = os.environ.get("SPHRAGIS_GIT_BRANCH") or None
    job_top = os.environ.get("SPHRAGIS_GIT_TOP") or None
    elsewhere = bool(
        started and job_top and (top is None or Path(job_top).resolve() != top.resolve())
    )
    if elsewhere:
        record: dict[str, Any] = {"commit": head, "branch": branch, "job_commit": started}
    else:
        record = {"commit": started or head, "branch": started_branch or branch}
        if started and head and head != started:
            record["checkout_at_write"] = head
    cwd_top = _git("rev-parse", "--show-toplevel")
    if cwd_top and (top is None or Path(cwd_top).resolve() != top.resolve()):
        cwd_commit = _git("rev-parse", "HEAD")
        if cwd_commit and cwd_commit != record["commit"]:
            record["cwd_commit"] = cwd_commit
    if top is None:
        return record
    # Code that differs from the commit it names makes the commit a false label: two committed
    # results named a commit whose code could not have produced them, because they were written
    # from a working tree with the fix not yet committed. Only the code is read, from the top of
    # the code's checkout, so a result directory full of new outputs does not mark every run
    # dirty.
    entries = _changed_code(top)
    if entries:
        # A name that is not UTF-8 is shown escaped: a lone surrogate would stop a writer that
        # dumps JSON with ensure_ascii=False. The file is read and digested by its bytes.
        paths = sorted({path.decode("utf-8", "backslashreplace") for _, path in entries})
        digest = hashlib.sha256(
            (_git("-C", str(top), "diff", "HEAD", "--", ":/sphragis", ":/scripts") or "").encode()
        )
        for status, path in sorted(entries, key=lambda entry: entry[1]):
            if status == b"??" and (top / os.fsdecode(path)).is_file():
                digest.update(path + b"\0" + (top / os.fsdecode(path)).read_bytes())
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
