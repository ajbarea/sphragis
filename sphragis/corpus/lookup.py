"""Each target's added lines looked up in its project's code as it stood at the checkpoint.

Registered 2026-10-20 (research log, "The backport lookup registered"). A target's added lines
are the stripped lines of `after` left once each stripped line of `before` is removed once, at
least `TYPED_TARGET_MIN_CHARS` long. The reference is every stripped line of every text file of
every branch of the example's project, each branch at its last commit before the evaluated
weights were uploaded. η is the share of an example's added lines found there; the classes are
infini-gram mini's (arXiv:2506.12229, section 4.1), read on lines.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sphragis.corpus.halves import TYPED_TARGET_MIN_CHARS

#: The commit that wrote the evaluated checkpoint's weights, 6ab006b (research log, 2026-10-20).
UPLOAD = "2024-09-17T16:13:45Z"
#: infini-gram mini's class bounds: clean below the first, found at or above the second.
SUSPICIOUS, FOUND = 0.2, 0.8
#: A file whose first bytes hold a NUL is binary, as git itself decides.
BINARY_PROBE = 8000
#: Between two clones from one host.
CLONE_INTERVAL = 2.0

#: Where each organization's projects are cloned from (research log, 2026-10-20, item 5).
SOURCES: dict[str, Callable[[str], list[str]]] = {
    "openstack": lambda project: [f"https://opendev.org/{project}.git"],
    "wikimedia": lambda project: [f"https://gerrit.wikimedia.org/r/{project}"],
    "qt": lambda project: [
        f"https://github.com/{project}.git",
        f"https://github.com/qtproject/{project.replace('/', '-')}.git",
        f"https://github.com/qtproject/{project.rsplit('/', 1)[-1]}.git",
    ],
}


def stripped(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()]


def added_lines(before: str, after: str) -> list[str]:
    """`after`'s stripped lines less each of `before`'s once, those long enough to count."""
    remaining = Counter(stripped(before))
    added = []
    for line in stripped(after):
        if remaining[line] > 0:
            remaining[line] -= 1
        elif len(line) >= TYPED_TARGET_MIN_CHARS:
            added.append(line)
    return added


def classify(eta: float) -> str:
    if eta >= FOUND:
        return "found"
    return "suspicious" if eta >= SUSPICIOUS else "clean"


def reading(row: Mapping[str, Any], reference: set[str] | None) -> dict[str, Any]:
    """One example's lookup: its id, eligible and found lines, η and class."""
    lines = added_lines(str(row.get("before") or ""), str(row.get("after") or ""))
    out: dict[str, Any] = {"id": row["id"], "eligible": len(lines)}
    if reference is None:
        return {**out, "class": "no_reference"}
    if not lines:
        return {**out, "class": "no_eligible_line"}
    found = sum(line in reference for line in lines)
    eta = found / len(lines)
    return {**out, "found": found, "eta": round(eta, 6), "class": classify(eta)}


def _git(repo: Path, *args: str, user_agent: str | None = None) -> str:
    config = ["-c", f"http.userAgent={user_agent}"] if user_agent else []
    return subprocess.run(
        ["git", *config, "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def branch_tips(repo: Path, before: str = UPLOAD) -> dict[str, str]:
    """Each branch's last commit before `before`; a branch with none is left out."""
    tips = {}
    for ref in _git(repo, "for-each-ref", "--format=%(refname)", "refs/heads").split():
        commit = _git(repo, "rev-list", "-1", f"--before={before}", ref).strip()
        if commit:
            tips[ref.removeprefix("refs/heads/")] = commit
    return tips


def reference_lines(repo: Path, commits: Iterable[str]) -> tuple[set[str], int]:
    """Every long-enough stripped line of every text file in `commits`' trees, and the count of
    distinct files read. Each distinct blob is read once."""
    blobs: set[str] = set()
    for commit in set(commits):
        for entry in _git(repo, "ls-tree", "-r", commit).splitlines():
            meta, _ = entry.split("\t", 1)
            _, kind, oid = meta.split()
            if kind == "blob":
                blobs.add(oid)
    lines: set[str] = set()
    with subprocess.Popen(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    ) as batch:
        assert batch.stdin and batch.stdout
        for oid in sorted(blobs):
            batch.stdin.write(f"{oid}\n".encode())
            batch.stdin.flush()
            header = batch.stdout.readline().split()
            size = int(header[2])
            data = batch.stdout.read(size)
            batch.stdout.read(1)
            if b"\0" in data[:BINARY_PROBE]:
                continue
            for line in stripped(data.decode("utf-8", errors="replace")):
                if len(line) >= TYPED_TARGET_MIN_CHARS:
                    lines.add(line)
        batch.stdin.close()
    return lines, len(blobs)


class Cloner:
    """Bare clones under `root`, one host at a time at `CLONE_INTERVAL`, the host claimed."""

    def __init__(
        self,
        root: Path,
        *,
        user_agent: str,
        claim: Callable[[str], object],
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root, self.user_agent, self._claim = root, user_agent, claim
        self._sleep, self._clock = sleep, clock
        self._last: dict[str, float] = {}

    def path(self, url: str) -> Path:
        parsed = urlparse(url)
        return self.root / parsed.netloc / parsed.path.strip("/").removesuffix(".git")

    def clone(self, url: str) -> Path | None:
        """The local clone of `url`, cloned once; None if the host has no such repository."""
        dest = self.path(url)
        if (dest / "HEAD").is_file():
            return dest
        host = urlparse(url).netloc
        self._claim(host)
        wait = self._last.get(host, -CLONE_INTERVAL) + CLONE_INTERVAL - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last[host] = self._clock()
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".partial")
        if partial.exists():
            # A clone a killed run left half-written, inside this cloner's own root.
            if not partial.resolve().is_relative_to(self.root.resolve()):
                raise RuntimeError(f"{partial} is outside {self.root}")
            shutil.rmtree(partial)
        done = subprocess.run(
            [
                "git",
                "-c",
                f"http.userAgent={self.user_agent}",
                "clone",
                "--bare",
                "--quiet",
                url,
                str(partial),
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if done.returncode != 0:
            if _missing(done.stderr):
                return None
            raise RuntimeError(f"clone of {url} failed: {done.stderr.strip()[:300]}")
        partial.rename(dest)
        return dest


def _missing(stderr: str) -> bool:
    """Whether a failed clone means the repository does not exist, as the hosts word it (read
    2026-10-20): GitHub says "not found"; opendev.org asks for credentials, which prompts are off
    for. gerrit.wikimedia.org answers 403, which a refusal would too, so it stays an error."""
    text = stderr.lower()
    return "not found" in text or "could not read username" in text
