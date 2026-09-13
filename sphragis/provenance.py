"""Static provenance every artifact in this repo carries: commit, versions, platform.

Copied from phalanx-fl rather than imported. Twelve lines of standard library is the
wrong thing to take a cross-repo dependency for, especially on a repo whose stated
invariant is to ride the latest Flower while this one must stay reproducible.
"""

from __future__ import annotations

import platform
import subprocess
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
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


def provenance_header() -> dict[str, Any]:
    """The static provenance every manifest in this repo carries."""
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "git": {
            "commit": _git("rev-parse", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        },
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": _package_versions(),
    }
