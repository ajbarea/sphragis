"""Each target's added lines looked up in its project's code as it stood at the checkpoint.

Registered 2026-10-20 and amended before any lookup ran (research log, "The backport lookup
registered" and the entry after it). A target's added lines are the stripped lines of `after`
left once each stripped line of `before` is removed once, at least `TYPED_TARGET_MIN_CHARS` long.
The reference is every stripped line of every text file of every branch of the example's project,
and of every branch retired to an end-of-life tag, each at its first-parent commit before the
evaluated weights were uploaded. η is the share of an example's added lines found there; the
classes are infini-gram mini's (arXiv:2506.12229, section 4.1), read on lines.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from sphragis.corpus.halves import TYPED_TARGET_MIN_CHARS
from sphragis.corpus.load import refined_examples
from sphragis.corpus.notedb import _UNSAFE_PROJECT, _isolated_env
from sphragis.corpus.pipeline import run_dedup, run_split
from sphragis.corpus.windows import WINDOWS

#: The commit that wrote the evaluated checkpoint's weights, 6ab006b (research log, 2026-10-20).
UPLOAD = "2024-09-17T16:13:45Z"
#: infini-gram mini's class bounds: clean below the first, found at or above the second.
SUSPICIOUS, FOUND = 0.2, 0.8
#: A file whose first bytes hold a NUL is binary, as git itself decides.
BINARY_PROBE = 8000
#: From the end of one clone to the start of the next on one host.
CLONE_INTERVAL = 2.0
#: How long a host is left alone after it answers a clone with a server error.
QUIET_AFTER_ERROR = 900.0
#: OpenStack retires a branch at its end of life to a tag `<series>-eol`.
EOL_TAG_SUFFIX = "-eol"
#: Symlinks and submodules are not files (`git ls-tree` modes).
NOT_FILES = ("120000", "160000")

#: The hosts the lookup clones from, each with the reading that admits it (research log,
#: 2026-10-20, item 5). A git URL with no host is a local path.
LOOKUP_PERMITTED = {
    "opendev.org": "robots.txt disallows web pages, not git's clone paths (read 2026-10-20)",
    "gerrit.wikimedia.org": "robots.txt leaves anonymous /r/<project> open (read 2026-10-20)",
    "github.com": "GitHub's research clause (research log, 2026-10-19)",
}

#: Where each organization's projects are cloned from, in the order tried.
SOURCES: dict[str, Callable[[str], list[str]]] = {
    "openstack": lambda project: [f"https://opendev.org/{project}.git"],
    "wikimedia": lambda project: [f"https://gerrit.wikimedia.org/r/{project}"],
    "qt": lambda project: [
        f"https://github.com/{project}.git",
        f"https://github.com/qtproject/{project.replace('/', '-')}.git",
        f"https://github.com/qtproject/{project.rsplit('/', 1)[-1]}.git",
    ],
}


class Claim(Protocol):
    def quiet_until(self) -> float: ...
    def hold_until(self, deadline: float) -> None: ...


def safe_project(project: str) -> str:
    """`project` if it reads only as a path; one git or a URL could read otherwise is refused."""
    if not project or project.startswith(("/", "-")) or _UNSAFE_PROJECT.search(project):
        raise ValueError(f"refusing project name {project!r}")
    return project


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


def reading(row: Mapping[str, Any], reference: set[int] | None) -> dict[str, Any]:
    """One example's lookup: its id, eligible and found lines, η and class. `reference` holds
    each line's hash (`reference_lines`)."""
    lines = added_lines(str(row.get("before") or ""), str(row.get("after") or ""))
    out: dict[str, Any] = {"id": row["id"], "eligible": len(lines)}
    if reference is None:
        return {**out, "class": "no_reference"}
    if not lines:
        return {**out, "class": "no_eligible_line"}
    found = sum(hash(line) in reference for line in lines)
    eta = found / len(lines)
    return {**out, "found": found, "eta": round(eta, 6), "class": classify(eta)}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=_isolated_env(),
    ).stdout


def branch_tips(repo: Path, before: str = UPLOAD) -> dict[str, str]:
    """Each branch's first-parent commit before `before`, and each end-of-life tag's whose branch
    still stood then (the tag created after `before`), keyed by ref; one with no commit before
    `before` is left out. First-parent, because a merged side branch's commits carry their own
    dates and were not on the branch until the merge."""
    listing = _git(
        repo,
        "for-each-ref",
        "--format=%(refname) %(creatordate:iso-strict)",
        "refs/heads",
        "refs/tags",
    )
    cutoff = datetime.fromisoformat(before.replace("Z", "+00:00"))
    tips = {}
    for line in listing.splitlines():
        ref, created = line.rsplit(" ", 1)
        if ref.startswith("refs/tags/") and (
            not ref.endswith(EOL_TAG_SUFFIX) or datetime.fromisoformat(created) <= cutoff
        ):
            continue
        commit = _git(
            repo, "rev-list", "-1", "--first-parent", f"--before={before}", f"{ref}^{{commit}}"
        ).strip()
        if commit:
            tips[ref] = commit
    return tips


def reference_lines(repo: Path, commits: Iterable[str]) -> tuple[set[int], int]:
    """The hash of every long-enough stripped line of every text file in `commits`' trees, and
    the count of distinct files read. Each distinct blob is read once."""
    blobs: set[str] = set()
    for commit in set(commits):
        for entry in _git(repo, "ls-tree", "-r", commit).splitlines():
            meta, _ = entry.split("\t", 1)
            mode, kind, oid = meta.split()
            if kind == "blob" and mode not in NOT_FILES:
                blobs.add(oid)
    lines: set[int] = set()
    with subprocess.Popen(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=_isolated_env(),
    ) as batch:
        assert batch.stdin and batch.stdout
        for oid in sorted(blobs):
            batch.stdin.write(f"{oid}\n".encode())
            batch.stdin.flush()
            header = batch.stdout.readline().split()
            if len(header) != 3:
                raise RuntimeError(f"{repo}: object {oid} could not be read: {header}")
            data = batch.stdout.read(int(header[2]))
            batch.stdout.read(1)
            if b"\0" in data[:BINARY_PROBE]:
                continue
            for line in stripped(data.decode("utf-8", errors="replace")):
                if len(line) >= TYPED_TARGET_MIN_CHARS:
                    lines.add(hash(line))
        batch.stdin.close()
    return lines, len(blobs)


class Cloner:
    """Bare clones under `root` from the permitted hosts, one host at a time: its claim held, its
    quiet deadline kept, `CLONE_INTERVAL` from the end of one clone to the start of the next."""

    def __init__(
        self,
        root: Path,
        *,
        user_agent: str,
        claim: Callable[[str], Claim | None],
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root, self.user_agent, self._claim = root, user_agent, claim
        self._sleep, self._clock = sleep, clock
        self._ready: dict[str, float] = {}

    def path(self, url: str) -> Path:
        parsed = urlparse(url)
        return self.root / parsed.netloc / parsed.path.strip("/").removesuffix(".git")

    def clone(self, url: str) -> Path | None:
        """The local clone of `url`, cloned once; None if the host has no such repository."""
        dest = self.path(url)
        if (dest / "HEAD").is_file():
            return dest
        host = urlparse(url).netloc
        if host and host not in LOOKUP_PERMITTED:
            raise PermissionError(f"{host} is not a host the lookup clones from")
        claim = self._claim(host)
        ready = max(self._ready.get(host, 0.0), claim.quiet_until() if claim else 0.0)
        if ready > self._clock():
            self._sleep(ready - self._clock())
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".partial")
        if not partial.resolve().is_relative_to(self.root.resolve()):
            raise RuntimeError(f"{partial} is outside {self.root}")
        if partial.exists():
            shutil.rmtree(partial)  # a clone a killed run left half-written
        done = subprocess.run(
            [
                "git",
                "-c",
                f"http.userAgent={self.user_agent}",
                "-c",
                "http.followRedirects=false",
                "clone",
                "--bare",
                "--quiet",
                url,
                str(partial),
            ],
            capture_output=True,
            text=True,
            env=_isolated_env({"GIT_ASKPASS": "", "SSH_ASKPASS": ""}),
        )
        self._ready[host] = self._clock() + CLONE_INTERVAL
        if done.returncode != 0:
            if _missing(done.stderr):
                return None
            if claim and _server_error(done.stderr):
                claim.hold_until(self._clock() + QUIET_AFTER_ERROR)
            raise RuntimeError(f"clone of {url} failed: {done.stderr.strip()[:300]}")
        partial.rename(dest)
        return dest


def _missing(stderr: str) -> bool:
    """Whether a failed clone means the repository does not exist, as the hosts word it (read
    2026-10-20): GitHub says "not found"; opendev.org asks for credentials, which prompts are off
    for. gerrit.wikimedia.org answers 403, which a refusal would too, so it stays an error."""
    text = stderr.lower()
    return "not found" in text or "could not read username" in text


def _server_error(stderr: str) -> bool:
    return any(f"error: {code}" in stderr for code in ("429", "500", "502", "503", "504"))


def read_windows(root: Path, org: str, windows: list[str]) -> dict[str, list[dict[str, Any]]]:
    """The examples of `windows`, deduplicated as one corpus and split as `holdout.window_split`
    reads them for the runner, with its refusals: a change straddling a boundary or outside every
    window, and any test-window row while the test window is sealed."""
    examples = refined_examples(root, org)
    if not examples:
        raise SystemExit(f"no refined examples under {root / org}")
    kept, _ = run_dedup(examples)
    split, straddling, unassigned = run_split(kept, WINDOWS)
    if straddling or unassigned:
        raise SystemExit(f"{org}: {len(straddling)} straddling and {len(unassigned)} unassigned")
    if split.get("test"):
        raise SystemExit(f"{org}: the corpus holds test-window examples; it is sealed")
    return {window: [dict(row) for row in split.get(window, [])] for window in windows}
