"""Gerrit review records read from NoteDb over git, in the shape the REST route stores.

The review UIs of android-review, chromium-review and codereview.qt-project.org publish
`Disallow: /` in robots.txt, so their REST APIs are closed to automated collection. The git
hosts behind the first two (android.googlesource.com, chromium.googlesource.com) disallow only
a few gitiles web views; the git fetch paths are allowed. Gerrit 3.x keeps every change's
review record in the project repository itself, as NoteDb:

- `refs/changes/NN/<change>/meta` is a chain of commits whose footers carry the change's
  state (`Patch-set`, `Commit`, `Status`, `Change-id`, `Branch`, `Subject`, `Submission-id`)
  and whose author identifies the acting account as `Gerrit User <id> <<id>@<server id>>`;
- the tree of its tip is a notes map keyed by patch-set commit, each note a JSON document
  whose `comments` are the published inline comments.

This module reads those records and emits one row per change in the REST `ChangeInfo` shape
that `build` and `scripts/censoring.py` read, so the stages after fetch are shared. Each row
also carries, under `notedb`, what the REST route fetches at build time: the change's
comments in the `/comments` shape and the per-file diffs `build` will ask for. The snapshot
is then complete on its own, and a NoteDb build makes no network request.

Two fields exist for the component mapping, which is its own stage: `merged_commit`, the
commit on the target branch that carries the change, and `files`, the paths it changed with
`lines_inserted` / `lines_deleted` as `git diff --numstat --histogram` counts them against its
first parent. Renames count as a deletion and an addition (`--no-renames`), a binary file
counts `None` lines, and `files` is None when the change's own commit is a merge, or its
parent lies outside the fetched history (`collect` counts each cause).

What NoteDb cannot supply is listed in `GAPS`, which every snapshot record carries.

Identities are pseudonymised at ingestion by the same `scrub` and salt as the REST route. The
git objects themselves carry raw identities (patch-set authors, NoteDb idents), so they are
fetched into a scratch repository that the caller deletes; only scrubbed rows reach disk.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from sphragis.corpus import gerrit_diff
from sphragis.corpus.build import CommentFetcher, DiffFetcher
from sphragis.corpus.examples import is_code_file
from sphragis.corpus.pacing import Pacer
from sphragis.corpus.scrub import scrub

#: Git hosts serving each organization's repositories, with their NoteDb refs.
GIT_HOSTS = {
    "aosp": "https://android.googlesource.com",
    "chromium": "https://chromium.googlesource.com",
}

#: The review host a merged commit's `Reviewed-on:` trailer names. Parsed, never contacted.
REVIEW_HOSTS = {
    "aosp": "android-review.googlesource.com",
    "chromium": "chromium-review.googlesource.com",
}

#: The branch whose history is enumerated unless the caller names another.
BRANCH_DEFAULT = "main"

#: Organizations whose merged commits carry no `Reviewed-on:` trailer, so changes are found by
#: joining commit ids against the patch-set refs instead. Checked 2026-09-23: AOSP's
#: platform/hardware/interfaces had no such trailer on any commit of 2024-11.
TRAILERLESS = frozenset({"aosp"})

#: Where a row keeps the comments and diffs the REST route fetches at build time.
NOTEDB_KEY = "notedb"

#: Refspecs or object ids per `git fetch`. Large enough that a project-month is a handful of
#: fetches, small enough that one refused batch does not cost a whole month.
BATCH = 1000

#: Days before a month's first day from which merged commits are candidates for it. A change
#: whose commit is dated before this is missed. AOSP merges the uploaded commit unchanged, so
#: its commit dates the upload: over the 9,645 `main` changes in the REST corpus, 1.98% merged
#: more than 14 days after their final upload and 0.27% more than 90. Hosts that rewrite on
#: submit (Chromium's cherry-pick) date the commit at the merge, so the default is a margin for
#: clock skew only, not a measured figure.
SLACK_DAYS = {"aosp": 90}
DEFAULT_SLACK_DAYS = 14


def candidates_since(org: str, month_start: str) -> str:
    """The first commit date that can be a candidate for the month starting `month_start`."""
    days = SLACK_DAYS.get(org, DEFAULT_SLACK_DAYS)
    return (datetime.fromisoformat(month_start) - timedelta(days=days)).date().isoformat()


#: How the diffs are computed, recorded in every snapshot record.
DIFF_METHOD = (
    "gerrit_diff: JGit HistogramDiff with MyersDiff fallback under WS_IGNORE_CHANGE, "
    "Gerrit's newline-at-end correction and content blocks"
)

#: REST fields a NoteDb row does not carry, and why each is safe to leave out. Nothing under
#: `sphragis/` or `scripts/` reads any of them (checked by grep, 2026-09-23); `build` reads
#: `_number`, `change_id`, `owner`, `revisions` (its length), `project` and `created`, and
#: `scripts/censoring.py` reads `id`, `project`, `change_id`, `created` and `updated`.
GAPS = {
    "revisions.*.kind": "computed by the server's change-kind cache, not stored in NoteDb",
    "revisions.*.fetch": "a server URL template; `ref` is kept",
    "insertions/deletions": "server-computed against the final patch set's parent; "
    "`files` counts lines against the merged commit's parent instead",
    "attention_set/removed_from_attention_set": "stored as `Attention` footers, not mapped",
    "has_review_started/total_comment_count/unresolved_comment_count": "derivable, not mapped",
    "comments.*.change_message_id": "not in the note JSON",
    "diff.meta_a/meta_b/change_type/diff_header": "not reproduced; `build` reads `content` only",
    "diff.content": "recomputed by `gerrit_diff`, a port of what Gerrit runs; the charset "
    "is UTF-8 else ISO-8859-1 where Gerrit detects one",
}

_TIMESTAMP = "%Y-%m-%d %H:%M:%S.000000000"
_FOOTER = re.compile(r"^([A-Za-z][A-Za-z0-9-]*): ?(.*)$")
_ACCOUNT_EMAIL = re.compile(r"^(\d+)@")
_REVIEWED_ON = re.compile(
    r"^Reviewed-on:\s*https?://(?P<host>[^/\s]+)/(?:c/(?P<project>\S+?)/\+/)?(?P<number>\d+)/?\s*$",
    re.M,
)
_MISSING_REF = re.compile(r"couldn't find remote ref (\S+)")
_CURL_REQUEST = re.compile(rb"=> Send header: (?:GET|POST) ")


class GitError(RuntimeError):
    """A git command failed."""


def change_ref(number: int, suffix: str | int) -> str:
    """`refs/changes/NN/<number>/<suffix>`, NN being the change number's last two digits."""
    return f"refs/changes/{number % 100:02d}/{number}/{suffix}"


def gerrit_timestamp(epoch: int | float) -> str:
    """A git timestamp in Gerrit's REST format, UTC."""
    return datetime.fromtimestamp(int(epoch), UTC).strftime(_TIMESTAMP)


def _note_timestamp(value: Any) -> str | None:
    """A note JSON `writtenOn` (ISO 8601, e.g. `2024-12-18T22:50:22Z`) in Gerrit's REST format."""
    if not isinstance(value, str) or not value:
        return None
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    return moment.strftime("%Y-%m-%d %H:%M:%S.") + f"{moment.microsecond * 1000:09d}"


def _isolated_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """An environment that reads no user or system git config and never prompts.

    A user's config can rewrite URLs, attach credentials or change the diff algorithm, any
    of which would make a run depend on the machine. Inherited `GIT_*` variables are dropped
    for the reason `tests/conftest.py` records: a hook's `GIT_DIR` redirects every command.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_LAZY_FETCH": "1",
        }
    )
    env.update(extra or {})
    return env


# ---------------------------------------------------------------------------
# The repository: the only place a git process runs against a remote
# ---------------------------------------------------------------------------


@dataclass
class Repo:
    """A bare scratch repository with one partial-clone remote.

    No default filter is kept: git applies a remote's `partialclonefilter` to every fetch
    that names none, which silently dropped the note blobs from the meta fetch, so `fetch`
    clears the one git records and each fetch states its own.

    Every network operation goes through `fetch` or `list_remote`, which pace against the
    host, count the HTTP requests git actually made (from its curl trace) and append one line
    per operation to `ledger`. Every other command runs with lazy fetching disabled, so a
    missing object fails loudly instead of becoming an unpaced request nobody counted.
    """

    path: Path
    url: str
    pacer: Pacer
    ledger: Path | None = None
    operations: int = 0
    http_requests: int = 0
    log: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def open(cls, path: Path, url: str, pacer: Pacer, ledger: Path | None = None) -> Repo:
        repo = cls(Path(path), url, pacer, ledger)
        if not (repo.path / "HEAD").is_file():
            repo.path.mkdir(parents=True, exist_ok=True)
            repo._run(["init", "--quiet", "--bare", str(repo.path)], cwd=False)
            for key, value in (
                ("core.repositoryformatversion", "1"),
                ("extensions.partialClone", "origin"),
                ("remote.origin.url", url),
                ("remote.origin.promisor", "true"),
                ("protocol.version", "2"),
                ("gc.auto", "0"),
                ("fetch.writeCommitGraph", "false"),
                ("maintenance.auto", "false"),
            ):
                repo.git("config", key, value)
        return repo

    @property
    def host(self) -> str:
        return urlsplit(self.url).netloc or "local"

    def _run(
        self,
        args: Sequence[str],
        *,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
        cwd: bool = True,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        command = ["git", *(["--git-dir", str(self.path)] if cwd else []), *args]
        done = subprocess.run(command, input=stdin, capture_output=True, env=_isolated_env(env))
        if check and done.returncode != 0:
            raise GitError(
                f"git {' '.join(args[:3])} exited {done.returncode}: "
                f"{done.stderr.decode(errors='replace').strip()[-2000:]}"
            )
        return done

    def git(self, *args: str, stdin: bytes | None = None) -> bytes:
        """A local command. A lazy fetch fails rather than reaching the network."""
        local = ["-c", "remote.origin.url=/nonexistent/lazy-fetch-disabled"]
        return self._run([*local, *args], stdin=stdin).stdout

    def _network(self, purpose: str, args: Sequence[str], stdin: bytes | None, items: int) -> bytes:
        self.pacer.wait(self.host)
        with tempfile.NamedTemporaryFile(prefix="sphragis-curl-", suffix=".trace") as trace:
            started = time.monotonic()
            done = self._run(
                ["-c", "http.cookieFile=", "-c", "credential.helper=", *args],
                stdin=stdin,
                env={"GIT_TRACE_CURL": trace.name, "GIT_TRACE_CURL_NO_DATA": "1"},
                check=False,
            )
            requests = len(_CURL_REQUEST.findall(Path(trace.name).read_bytes()))
        self.pacer.charge(self.host, requests - 1)
        self.operations += 1
        self.http_requests += requests
        entry = {
            "at": datetime.now(UTC).isoformat(),
            "host": self.host,
            "purpose": purpose,
            "items": items,
            "http_requests": requests,
            "seconds": round(time.monotonic() - started, 2),
            "returncode": done.returncode,
        }
        self.log.append(entry)
        if self.ledger is not None:
            with self.ledger.open("a") as handle:
                handle.write(json.dumps(entry) + "\n")
        if done.returncode != 0:
            raise GitError(done.stderr.decode(errors="replace").strip()[-2000:])
        return done.stdout

    def fetch(self, purpose: str, refspecs: Sequence[str], *options: str) -> None:
        """One paced `git fetch` of `refspecs`, read from stdin so a batch has no length limit."""
        if not refspecs:
            return
        try:
            self._network(
                purpose,
                [
                    "fetch",
                    "--quiet",
                    "--no-tags",
                    "--no-write-fetch-head",
                    *options,
                    "--stdin",
                    "origin",
                ],
                "".join(f"{r}\n" for r in refspecs).encode(),
                len(refspecs),
            )
        finally:
            # A filtered fetch records its filter as the remote's default, and every later
            # fetch that names none inherits it: the history fetch's `tree:0` then stripped the
            # meta refs of their notes trees. Clearing it keeps each fetch to its own filter.
            self._run(["config", "--unset-all", "remote.origin.partialclonefilter"], check=False)
            if any(option.startswith("--depth") for option in options):
                self._repair_shallow()

    def _repair_shallow(self) -> None:
        """Keep a commit shallow only while one of its parents is actually missing.

        A `--depth` fetch marks the fetched commits' parents as history boundaries, including
        parents the branch history already holds, and a walk of the branch then stops there.
        On the AOSP parity sample that silently cut a rerun's candidates from 536 changes to
        430. A boundary whose parents are all present is no boundary, so it is removed.
        """
        shallow = self.path / "shallow"
        if not shallow.is_file():
            return
        marked = shallow.read_text().split()
        gone = set(self.missing(marked))
        headers = self.read_objects(oid for oid in marked if oid not in gone)
        present_parents = {
            parent
            for raw in headers.values()
            for line in raw.split(b"\n\n", 1)[0].decode(errors="replace").splitlines()
            if line.startswith("parent ")
            for parent in [line[7:]]
        }
        absent = set(self.missing(present_parents))
        keep = []
        for oid in marked:
            header = headers.get(oid, b"").split(b"\n\n", 1)[0].decode(errors="replace")
            parents = [line[7:] for line in header.splitlines() if line.startswith("parent ")]
            if oid not in headers or not parents or any(p in absent for p in parents):
                keep.append(oid)
        shallow.write_text("".join(f"{oid}\n" for oid in keep))

    def fetch_refs(self, purpose: str, refs: Sequence[str], *options: str) -> list[str]:
        """Fetch exact refs to the same names locally; return the ones the server lacks.

        A missing ref fails the whole fetch, and a change can vanish upstream (deleted, made
        private). The missing ref is dropped and the batch retried, one extra fetch per
        vanished change rather than one request per change. Refs already held are not
        fetched again, so a rerun over the same scratch repository costs no requests.
        """
        missing: list[str] = []
        held = set(
            self.git("for-each-ref", "--format=%(refname)", "refs/changes/").decode().split()
        )
        pending = [r for r in refs if r not in held]
        while pending:
            try:
                self.fetch(purpose, [f"+{r}:{r}" for r in pending], *options)
                return missing
            except GitError as error:
                gone = _MISSING_REF.search(str(error))
                if gone is None or gone.group(1) not in pending:
                    raise
                pending.remove(gone.group(1))
                missing.append(gone.group(1))
        return missing

    def fetch_objects(self, purpose: str, oids: Iterable[str], *options: str) -> None:
        """Fetch objects by id, in batches, skipping any already present."""
        wanted = self.missing(oids)
        for start in range(0, len(wanted), BATCH):
            self.fetch(purpose, wanted[start : start + BATCH], *options)

    def list_remote(self, purpose: str, pattern: str) -> list[tuple[str, str]]:
        """`git ls-remote` for one pattern: (object id, ref name) pairs.

        Git sends no ref prefix for an ls-remote pattern, so the server lists every ref and
        the client filters. That is one request, but a large one: a wildcard listing over
        chromium/src timed out. Use it only on repositories of moderate size.
        """
        out = self._network(purpose, ["ls-remote", "origin", pattern], None, 1)
        pairs = []
        for line in out.decode().splitlines():
            oid, _, ref = line.partition("\t")
            pairs.append((oid, ref))
        return pairs

    def missing(self, oids: Iterable[str]) -> list[str]:
        """The object ids in `oids` this repository does not hold.

        Read from the listing of every object held rather than by asking for each one: with
        lazy fetching disabled, git treats a single missing promised object as fatal.
        """
        unique = sorted(set(oids))
        if not unique:
            return []
        held = set(
            self.git(
                "cat-file", "--batch-all-objects", "--unordered", "--batch-check=%(objectname)"
            )
            .decode()
            .split()
        )
        return [oid for oid in unique if oid not in held]

    def read_objects(self, oids: Iterable[str]) -> dict[str, bytes]:
        """Raw contents of objects already present (blobs, or commits' headers), by id."""
        unique = sorted(set(oids))
        if not unique:
            return {}
        out = self.git("cat-file", "--batch", stdin="".join(f"{o}\n" for o in unique).encode())
        blobs: dict[str, bytes] = {}
        at = 0
        while at < len(out):
            header_end = out.index(b"\n", at)
            header = out[at:header_end].decode().split()
            if header[-1] == "missing":
                # Reported rather than skipped: a skipped blob surfaced later as a KeyError, or
                # as a diff of an empty file, far from the fetch that failed to bring it.
                raise GitError(f"object {header[0]} is not in the repository")
            size = int(header[2])
            blobs[header[0]] = out[header_end + 1 : header_end + 1 + size]
            at = header_end + 1 + size + 1
        return blobs


# ---------------------------------------------------------------------------
# Enumeration: which changes merged in a month
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MergedCommit:
    """One commit on the target branch, and the change it carries when that is known."""

    commit: str
    parents: tuple[str, ...]
    committed: int
    number: int | None
    via: str


def fetch_history(repo: Repo, branch: str, since: str) -> None:
    """The branch's commits since `since`, without trees or blobs.

    Commits alone are what enumeration reads, and they are small; the trees `files` needs
    are fetched afterwards for the merged commits only, which on a repository the size of
    chromium/src is the difference between a month's trees and every tree since `since`.

    Git has no upper date bound on a fetch, so this transfers every commit up to the branch
    tip, including merges after the month being collected. They are filtered out in
    `merged_commits` before anything is recorded, and the scratch repository holding them is
    deleted with the run.
    """
    repo.fetch(
        "history",
        [f"+refs/heads/{branch}:refs/heads/{branch}"],
        "--filter=tree:0",
        f"--shallow-since={since}",
    )


def reviewed_on(message: str, review_host: str, project: str) -> int | None:
    """The change number in a merged commit's `Reviewed-on:` trailer for this host and project.

    The last matching trailer wins, since trailers accumulate at the end. A trailer naming a
    different host or project is a cherry-pick from elsewhere and is ignored.
    """
    number = None
    for match in _REVIEWED_ON.finditer(message):
        if match.group("host") != review_host:
            continue
        if match.group("project") not in (None, project):
            continue
        number = int(match.group("number"))
    return number


def patch_set_commits(repo: Repo, project_refs: Sequence[tuple[str, str]]) -> dict[str, int]:
    """Commit id to change number, from a `refs/changes/*` listing (patch-set refs only)."""
    by_commit: dict[str, int] = {}
    for oid, ref in project_refs:
        parts = ref.split("/")
        if len(parts) == 5 and parts[4].isdigit():
            by_commit.setdefault(oid, int(parts[3]))
    return by_commit


def merged_commits(
    repo: Repo,
    branch: str,
    since: str,
    end: str,
    *,
    review_host: str,
    project: str,
    by_commit: Mapping[str, int] | None = None,
) -> tuple[list[MergedCommit], dict[str, int]]:
    """Candidate changes: commits on `branch` committed in [since, end), mapped to a change.

    A commit's date is a candidate filter, never the merge date. Under the rewriting submit
    strategies (Chromium's cherry-pick) the committer date is the merge; AOSP merges the
    uploaded commit unchanged, often through a merge commit, so its committer date is the
    upload, days before the merge. The month a change belongs to is therefore decided later,
    from NoteDb's own submission time (`collect`'s `submitted_between`), and `since` sits
    `SLACK_DAYS` before the month so a change uploaded before the month and merged
    inside it is still a candidate. One uploaded more than that before its merge is missed.

    A change is found by its `Reviewed-on:` trailer, or else by its commit id among the
    patch-set refs (`by_commit`), for hosts that stamp no trailer.
    """
    start = since
    lower = datetime.fromisoformat(start).replace(tzinfo=UTC).timestamp()
    upper = datetime.fromisoformat(end).replace(tzinfo=UTC).timestamp()
    raw = repo.git("log", "--format=%H%x00%P%x00%ct%x00%B%x1e", f"refs/heads/{branch}")
    found: list[MergedCommit] = []
    counts: Counter[str] = Counter()
    seen: set[int] = set()
    for record in raw.decode(errors="replace").split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        commit, parents, stamp, message = record.split("\x00", 3)
        committed = int(stamp)
        if not lower <= committed < upper:
            continue
        counts["commits"] += 1
        number = reviewed_on(message, review_host, project)
        via = "trailer"
        if number is None and by_commit is not None:
            number, via = by_commit.get(commit), "patch_set_ref"
        if number is None:
            counts["merge_commit" if len(parents.split()) > 1 else "no_change"] += 1
            continue
        if number in seen:
            counts["duplicate_change"] += 1
            continue
        seen.add(number)
        counts[f"by_{via}"] += 1
        found.append(MergedCommit(commit, tuple(parents.split()), committed, number, via))
    return found, dict(counts)


# ---------------------------------------------------------------------------
# NoteDb: one change's record
# ---------------------------------------------------------------------------


def _footers(message: str) -> dict[str, list[str]]:
    """Footer lines of a NoteDb commit message: the `Key: value` lines of its last paragraph."""
    paragraphs = [p for p in message.strip("\n").split("\n\n") if p.strip()]
    footers: dict[str, list[str]] = {}
    if not paragraphs:
        return footers
    for line in paragraphs[-1].splitlines():
        match = _FOOTER.match(line)
        if match:
            footers.setdefault(match.group(1).lower(), []).append(match.group(2).strip())
    return footers


def _account(name: str, email: str, committer: tuple[str, str]) -> int | None:
    """The account a NoteDb commit acts for, or None when the server itself wrote it.

    Mirrors ChangeNotesParser.parseIdent: an author identical to the committer is the server.
    """
    if (name, email) == committer:
        return None
    match = _ACCOUNT_EMAIL.match(email)
    return int(match.group(1)) if match else None


@dataclass
class ChangeRecord:
    """A change as NoteDb states it, before scrubbing. Account ids are Gerrit's own integers."""

    number: int
    meta: str
    change_id: str | None = None
    branch: str | None = None
    subject: str | None = None
    status: str | None = None
    owner: int | None = None
    submitter: int | None = None
    created: int | None = None
    updated: int | None = None
    submitted: int | None = None
    submission_id: str | None = None
    hashtags: list[str] = field(default_factory=list)
    patch_sets: dict[int, dict[str, Any]] = field(default_factory=dict)
    comments: list[dict[str, Any]] = field(default_factory=list)


def read_change(repo: Repo, number: int) -> ChangeRecord:
    """Parse one fetched meta ref, following ChangeNotesParser's rules for each field.

    `created` is the first meta commit's committer time and `updated` the latest; the owner
    is the first commit's account; `submitted` is the newest commit carrying `Submission-id`,
    and its account is the submitter. A patch set exists from its `Commit` footer until a
    `Patch-set: N (DELETED)` footer removes it.
    """
    ref = change_ref(number, "meta")
    tip = repo.git("rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
    raw = repo.git(
        "log", "--reverse", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%ct%x00%B%x1e", tip
    )
    record = ChangeRecord(number=number, meta=tip)
    deleted: set[int] = set()
    for entry in raw.decode(errors="replace").split("\x1e"):
        entry = entry.strip("\n")
        if not entry:
            continue
        _, an, ae, cn, ce, stamp, message = entry.split("\x00", 6)
        when = int(stamp)
        account = _account(an, ae, (cn, ce))
        footers = _footers(message)
        if record.created is None:
            record.created = when
        record.updated = max(record.updated or when, when)
        if record.owner is None and account is not None:
            record.owner = account
        for key, attribute in (
            ("change-id", "change_id"),
            ("branch", "branch"),
            ("subject", "subject"),
        ):
            if key in footers:
                setattr(record, attribute, footers[key][-1])
        if "status" in footers:
            record.status = footers["status"][-1].upper()
        if "hashtags" in footers:
            # A set. REST returns it in hash-set order, which differs between changes whose
            # footers are identical, so the row stores the one order that is reproducible.
            record.hashtags = sorted(
                t.strip() for t in footers["hashtags"][-1].split(",") if t.strip()
            )
        if "submission-id" in footers:
            record.submission_id = footers["submission-id"][-1]
            record.submitted = when
            record.submitter = account
        if "patch-set" in footers:
            number_text, _, state = footers["patch-set"][-1].partition(" ")
            ps = int(number_text)
            if state.strip("()").upper() == "DELETED":
                deleted.add(ps)
            elif "commit" in footers and ps not in record.patch_sets:
                record.patch_sets[ps] = {
                    "commit": footers["commit"][-1],
                    "created": when,
                    "uploader": account,
                }
    for ps in deleted:
        record.patch_sets.pop(ps, None)
    record.comments = _note_comments(repo, tip)
    return record


def _note_blobs(repo: Repo, tip: str) -> list[str]:
    """Blob ids of every note in the notes tree at `tip`."""
    listing = repo.git("ls-tree", "-r", "-z", tip).decode()
    return [entry.split()[2] for entry in listing.split("\0") if entry and " blob " in entry]


def _note_comments(repo: Repo, tip: str) -> list[dict[str, Any]]:
    """Every published inline comment in the notes tree at `tip`."""
    comments: list[dict[str, Any]] = []
    for blob in repo.read_objects(_note_blobs(repo, tip)).values():
        try:
            note = json.loads(blob)
        except ValueError:
            continue
        comments.extend(note.get("comments") or [])
    return comments


def rest_comment(comment: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """One note comment as (path, REST `CommentInfo`), mirroring CommentJson.

    `side` 1 is the revision; 0 is the parent, and a negative side names a merge parent.
    `line` is omitted for a file-level comment, as REST omits it.
    """
    key = comment["key"]
    info: dict[str, Any] = {"id": key["uuid"], "patch_set": int(key["patchSetId"])}
    side = int(comment.get("side", 1))
    if side <= 0:
        info["side"] = "PARENT"
        if side < 0:
            info["parent"] = -side
    if int(comment.get("lineNbr") or 0) > 0:
        info["line"] = int(comment["lineNbr"])
    if comment.get("parentUuid"):
        info["in_reply_to"] = comment["parentUuid"]
    span = comment.get("range")
    if isinstance(span, Mapping):
        info["range"] = {
            "start_line": span.get("startLine"),
            "start_character": span.get("startChar"),
            "end_line": span.get("endLine"),
            "end_character": span.get("endChar"),
        }
    info["message"] = comment.get("message", "")
    author = comment.get("author") or {}
    if "id" in author:
        info["author"] = {"_account_id": int(author["id"])}
    updated = _note_timestamp(comment.get("writtenOn"))
    if updated is not None:
        info["updated"] = updated
    if "unresolved" in comment:
        info["unresolved"] = bool(comment["unresolved"])
    if comment.get("revId"):
        info["commit_id"] = comment["revId"]
    if comment.get("tag"):
        info["tag"] = comment["tag"]
    return str(key["filename"]), info


def _comment_order(info: Mapping[str, Any]) -> tuple[Any, ...]:
    """CommentsUtil.COMMENT_INFO_ORDER: patch set, side, line, reply, message, id."""
    line, reply = info.get("line"), info.get("in_reply_to")
    return (
        info["patch_set"],
        0 if info.get("side") == "PARENT" else 1,
        (line is not None, line or 0),
        (reply is not None, reply or ""),
        info.get("message", ""),
        info["id"],
    )


def rest_comments(comments: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The REST `/changes/<n>/comments` payload: comments by path, in Gerrit's order."""
    by_path: dict[str, list[dict[str, Any]]] = {}
    for comment in comments:
        path, info = rest_comment(comment)
        by_path.setdefault(path, []).append(info)
    return {path: sorted(items, key=_comment_order) for path, items in sorted(by_path.items())}


# ---------------------------------------------------------------------------
# Diffs, in the REST `/diff` content shape
# ---------------------------------------------------------------------------


def diff_key(base: int, path: str) -> str:
    """Where a row keeps the diff of `path` from patch set `base` to `base + 1`."""
    return f"{base}:{path}"


def embedded_fetchers(change: Mapping[str, Any]) -> tuple[CommentFetcher, DiffFetcher]:
    """`build`'s two fetchers, answered from what a NoteDb row carries.

    A diff the row lacks raises, which `build_from_change` counts as `diff_error`, the same
    drop a failed REST request produces.
    """
    carried = change[NOTEDB_KEY]

    def comments(number: int) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        return carried["comments"]

    def diff(number: int, revision: int, path: str, base: int) -> Mapping[str, Any]:
        entry = carried["diffs"].get(diff_key(base, path)) if revision == base + 1 else None
        if entry is None:
            raise KeyError(f"no diff of {path} from patch set {base} to {revision}")
        return entry

    return comments, diff


# ---------------------------------------------------------------------------
# One project-month into rows
# ---------------------------------------------------------------------------


def _placement(submitted: int | None, start: str, end: str) -> str:
    """Where a submission time falls against [start, end): before, inside, after, or never."""
    if submitted is None:
        return "never"
    stamp = gerrit_timestamp(submitted)
    if stamp < start:
        return "before"
    return "inside" if stamp < end else "after"


def _diff_pairs(record: ChangeRecord) -> set[tuple[int, str]]:
    """(patch set, path) pairs `build` can ask a diff for: anchored code comments with a successor.

    Deliberately wider than what survives `build`'s author and acknowledgement filters, which
    need the owner comparison and are applied there, once, for both routes.
    """
    pairs = set()
    for comment in record.comments:
        path, info = rest_comment(comment)
        ps = info["patch_set"]
        if is_code_file(path) and "line" in info and ps + 1 in record.patch_sets:
            pairs.add((ps, path))
    return pairs


def _commit_parents(repo: Repo, commits: Iterable[str]) -> dict[str, list[str]]:
    """Parent ids from each commit's own header, which a shallow graft does not rewrite."""
    parents: dict[str, list[str]] = {}
    for oid, raw in repo.read_objects(commits).items():
        header = raw.split(b"\n\n", 1)[0].decode(errors="replace")
        parents[oid] = [line[7:] for line in header.splitlines() if line.startswith("parent ")]
    return parents


def tree_entries(repo: Repo, commit: str, paths: Sequence[str]) -> dict[str, str]:
    """Path to blob id in `commit`, for the paths that exist there."""
    if not paths:
        return {}
    out = repo.git("ls-tree", "-z", "--full-tree", commit, "--", *paths).decode()
    entries = {}
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        mode, kind, oid = meta.split()
        if kind == "blob":
            entries[path] = oid
    return entries


def _changed_files(
    repo: Repo, commit: str, parent: str
) -> list[tuple[str, str | None, str | None]]:
    """(path, old blob, new blob) for every file `commit` changed against `parent`."""
    out = repo.git("diff-tree", "-r", "-z", "--no-renames", parent, commit).decode()
    fields = out.split("\0")
    changed = []
    for index in range(0, len(fields) - 1, 2):
        meta, path = fields[index], fields[index + 1]
        if not meta.startswith(":"):
            continue
        old_mode, new_mode, old, new, _ = meta[1:].split()
        null = "0" * len(old)
        if old_mode.startswith("16") or new_mode.startswith("16"):
            continue  # a submodule pointer carries no lines
        changed.append((path, None if old == null else old, None if new == null else new))
    return changed


def _numstat(repo: Repo, commit: str, parent: str) -> dict[str, dict[str, int | None]]:
    """Lines inserted and deleted per path, as `git diff --numstat --histogram` counts them.

    Whitespace included, against the first parent. Histogram, because that is the algorithm
    Gerrit counts a change's `insertions` and `deletions` with: on the AOSP parity sample git's
    default Myers counts disagreed with REST and histogram's did not (research log,
    2026-09-23). A
    binary file counts None, as numstat reports `-`. Needs the blobs present; `collect`
    fetches them first.
    """
    out = repo.git(
        "diff",
        "--numstat",
        "--histogram",
        "-z",
        "--no-renames",
        "--ignore-submodules",
        parent,
        commit,
    ).decode(errors="surrogateescape")
    counts: dict[str, dict[str, int | None]] = {}
    for entry in out.split("\0"):
        if not entry:
            continue
        inserted, deleted, path = entry.split("\t", 2)
        counts[path] = {
            "lines_inserted": None if inserted == "-" else int(inserted),
            "lines_deleted": None if deleted == "-" else int(deleted),
        }
    return counts


def _row(
    record: ChangeRecord,
    project: str,
    merged: MergedCommit | None,
    parents: Mapping[str, list[str]],
) -> dict[str, Any]:
    """A change in the REST `ChangeInfo` shape, unscrubbed.

    Each revision also carries `parents`, its commit's parent ids, which REST returns only
    with the CURRENT_COMMIT option the REST route did not request: two patch sets on
    different parents were separated by a rebase.
    """
    full_branch = record.branch or ""
    branch = full_branch.removeprefix("refs/heads/")
    encoded = quote(project, safe="")
    current = max(record.patch_sets) if record.patch_sets else None
    revisions = {
        ps_data["commit"]: {
            "_number": ps,
            "branch": full_branch,
            "created": gerrit_timestamp(ps_data["created"]),
            "ref": change_ref(record.number, ps),
            "uploader": {"_account_id": ps_data["uploader"]},
            "parents": parents.get(ps_data["commit"], []),
        }
        for ps, ps_data in sorted(record.patch_sets.items())
    }
    row: dict[str, Any] = {
        "id": f"{encoded}~{record.number}",
        "triplet_id": f"{encoded}~{quote(branch, safe='')}~{record.change_id}",
        "project": project,
        "branch": branch,
        "full_branch": full_branch,
        "change_id": record.change_id,
        "subject": record.subject,
        "status": record.status,
        "_number": record.number,
        "virtual_id_number": record.number,
        "owner": {"_account_id": record.owner},
        "created": gerrit_timestamp(record.created or 0),
        "updated": gerrit_timestamp(record.updated or 0),
        "hashtags": record.hashtags,
        "meta_rev_id": record.meta,
        "revisions": revisions,
        "current_revision": record.patch_sets[current]["commit"] if current else None,
        "current_revision_number": current,
        "merged_commit": merged.commit if merged else None,
        "files": None,
    }
    if record.submitted is not None:
        row["submitted"] = gerrit_timestamp(record.submitted)
        row["submission_id"] = record.submission_id
        if record.submitter is not None:
            row["submitter"] = {"_account_id": record.submitter}
    return row


def collect(
    repo: Repo,
    numbers: Sequence[int],
    *,
    project: str,
    merged: Mapping[int, MergedCommit] | None = None,
    submitted_between: tuple[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch and read `numbers` from one project: unscrubbed rows with their comments and diffs.

    A fixed number of batched fetches, whatever the number of changes: the meta refs and
    their note blobs; the patch-set commits the comments need, by id and without blobs or
    history; the trees of the merged commits and their parents; then exactly the blobs those
    diffs and the `files` counts read. A change NoteDb does not record as submitted
    inside `submitted_between` is counted and dropped after the first, before it costs
    anything more.
    """
    merged = merged or {}
    counts: Counter[str] = Counter()
    missing = repo.fetch_refs("meta", [change_ref(n, "meta") for n in numbers])
    counts["meta_missing"] = len(missing)
    held = [n for n in numbers if change_ref(n, "meta") not in missing]
    repo.fetch_objects(
        "notes", [oid for n in held for oid in _note_blobs(repo, change_ref(n, "meta"))]
    )
    records = {}
    for number in held:
        record = read_change(repo, number)
        if submitted_between is not None:
            placed = _placement(record.submitted, *submitted_between)
            if placed != "inside":
                counts[f"submitted_{placed}"] += 1
                continue
        records[number] = record

    pairs = {number: _diff_pairs(record) for number, record in records.items()}
    commits = {
        records[number].patch_sets[ps + step]["commit"]
        for number, wanted in pairs.items()
        for ps, _ in wanted
        for step in (0, 1)
    }
    # Every patch set's commit, so each revision records its parents, and at depth 2 so the
    # parents themselves arrive: a rebase step's diff reads the file in both of them.
    every = {ps["commit"] for record in records.values() for ps in record.patch_sets.values()}
    repo.fetch_objects("patch_sets", every, "--depth=2", "--filter=tree:0")
    parents = _commit_parents(repo, every)
    rebased: dict[tuple[int, int], tuple[str, str]] = {}
    for number, wanted in pairs.items():
        for ps in {ps for ps, _ in wanted}:
            a = records[number].patch_sets[ps]["commit"]
            b = records[number].patch_sets[ps + 1]["commit"]
            if not gerrit_diff.related(parents[a], parents[b], a, b):
                rebased[(number, ps)] = (parents[a][0], parents[b][0])
    counts["rebase_steps"] = len(rebased)
    upstream = {oid for pair in rebased.values() for oid in pair}
    repo.fetch_objects("parents", repo.missing(upstream), "--depth=1", "--filter=tree:0")
    # Parents from the commits' own headers: `git log %P` shows a commit at the shallow
    # boundary as parentless, which filed it as a merge.
    landed = {n: m for n in records if (m := merged.get(n)) is not None}
    true_parents = _commit_parents(repo, (m.commit for m in landed.values()))
    single = {
        n: MergedCommit(m.commit, tuple(true_parents[m.commit]), m.committed, m.number, m.via)
        for n, m in landed.items()
        if len(true_parents.get(m.commit, [])) == 1
    }
    absent = set(repo.missing(m.parents[0] for m in single.values()))
    counted = {n: m for n, m in single.items() if m.parents[0] not in absent}
    roots = sorted(
        commits | upstream | {oid for m in counted.values() for oid in (m.commit, m.parents[0])}
    )
    if roots:
        # Read from the commit objects: `rev-parse <commit>^{tree}` loads the tree itself.
        listed = repo.git(
            "log", "--no-walk", "--stdin", "--format=%T", stdin="\n".join(roots).encode()
        )
        root_trees = listed.decode().split()
        repo.fetch_objects("trees", root_trees, "--filter=blob:none")

    blobs_needed: set[str] = set()
    trees: dict[tuple[int, int], dict[str, str]] = {}
    for number, wanted in pairs.items():
        for ps in {ps for ps, _ in wanted} | {ps + 1 for ps, _ in wanted}:
            paths = sorted({path for p, path in wanted if p in (ps, ps - 1)})
            trees[(number, ps)] = tree_entries(
                repo, records[number].patch_sets[ps]["commit"], paths
            )
            blobs_needed.update(trees[(number, ps)].values())
    upstream_trees: dict[tuple[int, int], tuple[dict[str, str], dict[str, str]]] = {}
    for (number, ps), (parent_a, parent_b) in rebased.items():
        paths = sorted(path for p, path in pairs[number] if p == ps)
        upstream_trees[(number, ps)] = (
            tree_entries(repo, parent_a, paths),
            tree_entries(repo, parent_b, paths),
        )
        blobs_needed.update(oid for side in upstream_trees[(number, ps)] for oid in side.values())
    for number in records:
        commit = counted.get(number)
        if commit is None:
            reason = (
                "parent_outside_history"
                if number in single
                else "merge_commit"
                if number in landed
                else "not_on_branch"
            )
            counts[f"files_unavailable_{reason}"] += 1
            continue
        changed = _changed_files(repo, commit.commit, commit.parents[0])
        blobs_needed.update(oid for _, old, new in changed for oid in (old, new) if oid)
    repo.fetch_objects("blobs", blobs_needed, "--filter=blob:none")
    contents = repo.read_objects(blobs_needed)

    rows = []
    for number, record in records.items():
        row = _row(record, project, merged.get(number), parents)
        diffs: dict[str, Any] = {}
        for ps, path in sorted(pairs[number]):
            before_oid = trees[(number, ps)].get(path)
            after_oid = trees[(number, ps + 1)].get(path)
            if before_oid is None and after_oid is None:
                counts["diff_path_absent"] += 1
                continue
            sides = upstream_trees.get((number, ps))
            computed = gerrit_diff.diff(
                contents[before_oid] if before_oid else None,
                contents[after_oid] if after_oid else None,
                parents=None
                if sides is None
                else (
                    contents[sides[0][path]] if path in sides[0] else None,
                    contents[sides[1][path]] if path in sides[1] else None,
                ),
            )
            counts["diff_binary"] += bool(computed.get("binary"))
            diffs[diff_key(ps, path)] = computed
        if number in counted:
            row["files"] = _numstat(repo, counted[number].commit, counted[number].parents[0])
        row[NOTEDB_KEY] = {"comments": rest_comments(record.comments), "diffs": diffs}
        counts["changes"] += 1
        rows.append(row)
    return rows, dict(counts)


def pseudonymise(row: Mapping[str, Any], salt: str) -> dict[str, Any]:
    """A row as it may reach disk: identities scrubbed, diffs left as the code they are.

    The change and its comments go through `scrub` with the run's salt, as the REST route's
    change payload and comment fetcher do, so owner and author pseudonyms compare. Diffs are
    not scrubbed, for the reason `fetchers.scrubbed_diff_fetcher` records.
    """
    carried = row[NOTEDB_KEY]
    change = scrub({k: v for k, v in row.items() if k != NOTEDB_KEY}, salt)
    change[NOTEDB_KEY] = {"comments": scrub(carried["comments"], salt), "diffs": carried["diffs"]}
    return change


def month_bounds(month: str) -> tuple[str, str]:
    """[first day, first day of the next month) for `YYYY-MM`."""
    year, number = (int(part) for part in month.split("-"))
    following = f"{year + (number == 12)}-{1 if number == 12 else number + 1:02d}"
    return f"{month}-01", f"{following}-01"


def fetch_month(
    org: str,
    projects: Sequence[str],
    month: str,
    salt: str,
    *,
    pacer: Pacer,
    branch: str = BRANCH_DEFAULT,
    workdir: Path | None = None,
    by_patch_set_ref: bool | None = None,
    base_url: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every change NoteDb records as submitted during `month` whose commit reached `branch`.

    Changes merged only into other branches are not enumerated; `branch` names the one whose
    history is read. Window membership is decided later, by each change's NoteDb creation
    time, exactly as for the REST route. `by_patch_set_ref` adds the ref-listing fallback
    for hosts whose merged commits carry no `Reviewed-on:` trailer (AOSP); it lists every
    ref in the project, so it is for repositories of moderate size only.
    """
    base = base_url or GIT_HOSTS[org]
    if by_patch_set_ref is None:
        by_patch_set_ref = org in TRAILERLESS
    start, end = month_bounds(month)
    since = candidates_since(org, start)
    started = datetime.now(UTC).isoformat()
    rows: list[dict[str, Any]] = []
    per_project: dict[str, Any] = {}
    operations = requests = 0
    with tempfile.TemporaryDirectory(prefix="sphragis-notedb-", dir=workdir) as scratch:
        for index, project in enumerate(projects):
            repo = Repo.open(Path(scratch) / f"{index}.git", f"{base}/{project}", pacer)
            fetch_history(repo, branch, since)
            by_commit = (
                patch_set_commits(repo, repo.list_remote("patch_set_refs", "refs/changes/*"))
                if by_patch_set_ref
                else None
            )
            merged, enumeration = merged_commits(
                repo,
                branch,
                since,
                end,
                review_host=REVIEW_HOSTS.get(org, ""),
                project=project,
                by_commit=by_commit,
            )
            by_number = {m.number: m for m in merged if m.number is not None}
            found, counts = collect(
                repo,
                sorted(by_number),
                project=project,
                merged=by_number,
                submitted_between=(start, end),
            )
            status = Counter(str(row["status"]) for row in found)
            rows.extend(pseudonymise(row, salt) for row in found if row["status"] == "MERGED")
            per_project[project] = {
                "enumeration": enumeration,
                "collect": counts,
                "status": dict(status),
            }
            operations += repo.operations
            requests += repo.http_requests
    record = {
        "route": "notedb",
        "base_url": base,
        "branch": branch,
        "month": month,
        "submitted_between": [start, end],
        "candidates_committed_since": since,
        "projects": per_project,
        "git_operations": operations,
        "http_requests": requests,
        "diff": DIFF_METHOD,
        "gaps": GAPS,
        "count": len(rows),
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
    }
    return rows, record
