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
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

# Packages whose versions pin a run's behaviour.
_TRACKED_PACKAGES = ("torch", "transformers", "peft", "datasets", "pygments")


#: The imported package's own directory, whose checkout is the code that writes an artifact.
_PACKAGE = Path(__file__).resolve().parent


#: The variables that point git at one repository (`git rev-parse --local-env-vars`, git 2.43).
#: A git hook exports some (`GIT_INDEX_FILE`), and `-C` into another checkout would still obey
#: them, so they are dropped from every call here.
_REPOSITORY_ENV = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_OBJECT_DIRECTORY",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_GRAFT_FILE",
        "GIT_INDEX_FILE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_REPLACE_REF_BASE",
        "GIT_PREFIX",
        "GIT_SHALLOW_FILE",
        "GIT_COMMON_DIR",
        # Not repository-locating, but read as a literal path it turns `:/sphragis` into no file,
        # and a dirty tree reads clean.
        "GIT_LITERAL_PATHSPECS",
    }
)


def _git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _REPOSITORY_ENV}


def _git_bytes(*args: str) -> bytes | None:
    try:
        return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL, env=_git_env())
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def _git(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args], stderr=subprocess.DEVNULL, text=True, env=_git_env()
        ).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


#: Checkouts found, by directory: every artifact asks, and a found checkout does not move. A
#: miss is asked again, so one failed git call does not hold for a whole job.
_FOUND: dict[tuple[str, Path], Path] = {}


def _checkout_of(package: Path) -> Path | None:
    """`_code_checkout` for a package directory."""
    if ("package", package) in _FOUND:
        return _FOUND["package", package]
    top = _git("-C", str(package), "rev-parse", "--show-toplevel")
    if not top or (Path(top) / "sphragis").resolve() != package:
        return None
    if _git("-C", top, "ls-files", "--error-unmatch", "sphragis/provenance.py") is None:
        return None
    _FOUND["package", package] = Path(top).resolve()
    return _FOUND["package", package]


def _code_checkout() -> Path | None:
    """The top of the checkout the imported `sphragis` package belongs to, or None.

    None unless the package is that checkout's own tracked `sphragis/`: a copy installed under a
    venv inside some checkout sits in that checkout's tree too, and git there would name a
    commit whose code was never imported.
    """
    return _checkout_of(_PACKAGE)


def _script_top(script: Path) -> Path | None:
    if ("script", script) in _FOUND:
        return _FOUND["script", script]
    top = _git("-C", str(script.parent), "rev-parse", "--show-toplevel")
    # A tool run from a git-ignored venv in a checkout (pytest, a console script) is not that
    # checkout's code; a new script not yet tracked is.
    if not top or _git("-C", top, "check-ignore", "-q", str(script)) is not None:
        return None
    _FOUND["script", script] = Path(top).resolve()
    return _FOUND["script", script]


def _script_file() -> Path | None:
    script = getattr(sys.modules.get("__main__"), "__file__", None)
    return Path(script).resolve() if script else None


def _script_checkout() -> Path | None:
    """The top of the checkout the running script (`__main__`) is in, or None without one: run
    from one checkout with another's package on `PYTHONPATH`, the script is the first's code."""
    script = _script_file()
    return _script_top(script) if script else None


#: The code a checkout's changes are read in: the package and the scripts.
_CODE_PATHS = (":/sphragis", ":/scripts")


def _code_paths(top: Path) -> tuple[str, ...]:
    """`_CODE_PATHS`, and the running script where it lies in `top`, so a script outside them (at
    the checkout's top, tracked or new) is read for changes too."""
    script = _script_file()
    if script is None or not script.is_relative_to(top):
        return _CODE_PATHS
    return (*_CODE_PATHS, f":(top,literal){script.relative_to(top).as_posix()}")


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for pkg in _TRACKED_PACKAGES:
        try:
            versions[pkg] = version(pkg)
        except PackageNotFoundError:
            continue
    return versions


def _changed_code(top: Path, paths: tuple[str, ...] = _CODE_PATHS) -> list[tuple[bytes, bytes]]:
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
        *paths,
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
    script's inputs, unless it is the script's checkout or names a commit already recorded (the
    code's or the job's start); a package outside any checkout records no commit of its own. A
    job's start commit names the code only when the job started in the code's checkout
    (`SPHRAGIS_GIT_TOP`);
    otherwise it is recorded beside as `job_commit`. A script run from another checkout than the
    package's is that checkout's code: its commit is recorded as `script_commit`, its changed
    code as `script_uncommitted_code`.
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
    script = _script_checkout()
    if script is not None and script != top:
        record["script_commit"] = _git("-C", str(script), "rev-parse", "HEAD")
    cwd_top = _git("rev-parse", "--show-toplevel")
    if cwd_top and Path(cwd_top).resolve() not in {top, script}:
        cwd_commit = _git("rev-parse", "HEAD")
        if cwd_commit and cwd_commit not in {record["commit"], record.get("job_commit")}:
            record["cwd_commit"] = cwd_commit
    # Code that differs from the commit it names makes the commit a false label: two committed
    # results named a commit whose code could not have produced them, because they were written
    # from a working tree with the fix not yet committed. Only the code is read, from the top of
    # the code's checkout and of the script's, so a result directory full of new outputs does not
    # mark every run dirty. One digest covers both.
    digest = hashlib.sha256()
    dirty = False
    for checkout, field in ((top, "uncommitted_code"), (script, "script_uncommitted_code")):
        if checkout is None or (field == "script_uncommitted_code" and checkout == top):
            continue
        paths = _digest_changed(checkout, digest)
        if paths:
            record[field] = paths
            dirty = True
    if dirty:
        record["uncommitted_code_sha256"] = digest.hexdigest()
    return record


def _digest_changed(top: Path, digest: Any) -> list[str]:
    """The code paths under `top` that differ from its HEAD, each folded into `digest`."""
    paths = _code_paths(top)
    entries = _changed_code(top, paths)
    if not entries:
        return []
    # A name that is not UTF-8 is shown escaped: a lone surrogate would stop a writer that
    # dumps JSON with ensure_ascii=False. The file is read and digested by its bytes.
    digest.update((_git("-C", str(top), "diff", "HEAD", "--", *paths) or "").encode())
    for status, path in sorted(entries, key=lambda entry: entry[1]):
        if status == b"??" and (top / os.fsdecode(path)).is_file():
            digest.update(path + b"\0" + (top / os.fsdecode(path)).read_bytes())
    return sorted({path.decode("utf-8", "backslashreplace") for _, path in entries})


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
