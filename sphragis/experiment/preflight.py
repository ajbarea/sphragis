"""Checks that run before a job is queued.

Three pilot jobs died on things a login node could have caught for free: a stale checkout
on the cluster, a keyword argument removed in transformers 5, and a timing artefact. GH200
queue waits run to hours, so the cost of finding these after submission is not the job, it
is the day.

Every check returns a list of problems rather than raising, so one invocation reports
everything wrong at once instead of one thing per queue cycle.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


def check_kwargs_supported(target: Callable[..., Any], kwargs: Mapping[str, Any]) -> list[str]:
    """Names any keyword the callable will not accept."""
    signature = inspect.signature(target)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
        return []
    accepted = set(signature.parameters)
    name = getattr(target, "__name__", str(target))
    return [f"{key} is not accepted by {name}" for key in kwargs if key not in accepted]


def check_paths_exist(paths: Mapping[str, Path]) -> list[str]:
    """Names any input that is missing or empty."""
    problems: list[str] = []
    for label, path in paths.items():
        if not Path(path).exists():
            problems.append(f"{label} does not exist: {path}")
        elif Path(path).is_file() and Path(path).stat().st_size == 0:
            problems.append(f"{label} is empty: {path}")
    return problems


def check_repo_matches(*, local: str, remote: str | None) -> list[str]:
    """Names a drift between the local checkout and the one the job will run."""
    if remote is None:
        return ["could not determine the remote revision; deploy before submitting"]
    if local != remote:
        return [f"remote checkout is {remote}, local is {local}; deploy before submitting"]
    return []
