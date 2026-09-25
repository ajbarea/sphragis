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
from functools import cache
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote, urlsplit

from sphragis.corpus import gerrit_diff
from sphragis.corpus.build import CommentFetcher, DiffFetcher
from sphragis.corpus.examples import REWORK, is_code_file
from sphragis.corpus.pacing import Pacer
from sphragis.corpus.rules import FETCH_RULES
from sphragis.corpus.scrub import scrub
from sphragis.corpus.windows import TEST_WINDOW_START

#: Git hosts serving each organization's repositories, with their NoteDb refs.
GIT_HOSTS = {
    "aosp": "https://android.googlesource.com",
    "chromium": "https://chromium.googlesource.com",
}


class GitPermission(NamedTuple):
    permitted: bool
    reason: str
    min_interval: float  # seconds between requests to this host; pacing never goes below it


# Mirrors `cli.REST_PERMITTED`: one entry per git host this route may fetch from, so granting
# Chromium permission later is a one-line change to an entry already here, not a new host to
# wire up. android.googlesource.com serves no robots.txt path relevant to a git fetch and
# answered a workstation address (checked 2026-09-18). chromium.googlesource.com is the same
# kind of host, but bulk Chromium collection has not been granted permission (checked
# 2026-09-22): it stays listed and refused until that changes.
GIT_PERMITTED = {
    "android.googlesource.com": GitPermission(
        True, "no robots.txt restriction reaches a git fetch (checked 2026-09-18)", 1.0
    ),
    "chromium.googlesource.com": GitPermission(
        False, "bulk Chromium collection waits for the host's permission (checked 2026-09-22)", 1.0
    ),
}

#: A `--project` value that could resolve outside its own repository once joined into a URL.
_UNSAFE_PROJECT = re.compile(r"[+?#]|\.\.")


def valid_project_name(project: str) -> bool:
    """False for a project name a URL or ref path could read as something other than itself."""
    return bool(project) and not project.startswith("/") and not _UNSAFE_PROJECT.search(project)


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
    "revisions.*.fetch": "a server URL template; `ref` is kept",
    "insertions/deletions": "server-computed against the final patch set's parent; "
    "`files` counts lines against the merged commit's parent instead",
    "attention_set/removed_from_attention_set": "stored as `Attention` footers, not mapped",
    "has_review_started/total_comment_count/unresolved_comment_count": "derivable, not mapped",
    "comments.*.change_message_id": "not in the note JSON",
    "diff.meta_a/meta_b/change_type/diff_header": "not reproduced; `build` reads `content` only",
    "diff.content": "recomputed by `gerrit_diff`, a port of what Gerrit runs; the charset "
    "is UTF-8 else ISO-8859-1 where Gerrit detects one",
    "author.tags": "NoteDb states no account type; `is_service_user` never matches a NoteDb "
    "comment, and refine's bot-template rule is what catches an automated one instead",
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


def _git_version() -> str:
    return (
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        .stdout.decode()
        .strip()
    )


@cache
def _lazy_fetch_probe(env_items: frozenset[tuple[str, str]]) -> None:
    """Confirm a `cat-file` of an object outside a partial clone's filter fails under `env`.

    Builds a real bare server repository with one blob and a real `--filter=blob:none` clone
    of it, so the check exercises git's actual promisor-remote path rather than assuming one
    git version behaves like another. `env` normally carries `GIT_NO_LAZY_FETCH=1`; a git that
    does not honor it fetches the blob anyway, `cat-file` succeeds, and that is refused.
    """
    env = dict(env_items)
    with tempfile.TemporaryDirectory(prefix="sphragis-lazy-probe-") as scratch:
        root = Path(scratch)
        server = root / "server.git"
        subprocess.run(["git", "init", "--quiet", "--bare", str(server)], check=True)
        blob = (
            subprocess.run(
                ["git", "--git-dir", str(server), "hash-object", "-w", "--stdin"],
                input=b"probe\n",
                capture_output=True,
                check=True,
            )
            .stdout.decode()
            .strip()
        )
        tree = (
            subprocess.run(
                ["git", "--git-dir", str(server), "mktree"],
                input=f"100644 blob {blob}\tf\n".encode(),
                capture_output=True,
                check=True,
            )
            .stdout.decode()
            .strip()
        )
        ident = {
            "GIT_AUTHOR_NAME": "probe",
            "GIT_AUTHOR_EMAIL": "probe@example.invalid",
            "GIT_COMMITTER_NAME": "probe",
            "GIT_COMMITTER_EMAIL": "probe@example.invalid",
        }
        commit = (
            subprocess.run(
                ["git", "--git-dir", str(server), "commit-tree", tree, "-m", "probe"],
                capture_output=True,
                check=True,
                env={**os.environ, **ident},
            )
            .stdout.decode()
            .strip()
        )
        subprocess.run(
            ["git", "--git-dir", str(server), "update-ref", "refs/heads/main", commit], check=True
        )
        for key, value in (("uploadpack.allowFilter", "true"),):
            subprocess.run(["git", "--git-dir", str(server), "config", key, value], check=True)
        clone = root / "clone.git"
        subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--bare",
                "--filter=blob:none",
                f"file://{server}",
                str(clone),
            ],
            check=True,
            env=_isolated_env(),
        )
        probed = subprocess.run(
            ["git", "--git-dir", str(clone), "cat-file", "-p", blob],
            capture_output=True,
            env=env,
        )
        if probed.returncode == 0:
            raise GitError(
                f"{_git_version()} fetched a filtered-out object lazily instead of failing under "
                "GIT_NO_LAZY_FETCH=1: this git is too old to run the git route safely (needs a "
                "git release that honors GIT_NO_LAZY_FETCH, 2.36 or later)"
            )


def _require_lazy_fetch_disabled(env: Mapping[str, str] | None = None) -> None:
    """Once per distinct environment, confirm this git fails a lazy fetch rather than making one.

    `env=None` is the route's own isolated environment (checked once per process, the result
    cached); a test passes a variant, such as one missing `GIT_NO_LAZY_FETCH`, to exercise the
    refusal without a second git binary.
    """
    _lazy_fetch_probe(frozenset((env or _isolated_env()).items()))


def change_ref(number: int, suffix: str | int) -> str:
    """`refs/changes/NN/<number>/<suffix>`, NN being the change number's last two digits."""
    return f"refs/changes/{number % 100:02d}/{number}/{suffix}"


def gerrit_timestamp(epoch: int | float) -> str:
    """A git timestamp in Gerrit's REST format, UTC."""
    return datetime.fromtimestamp(int(epoch), UTC).strftime(_TIMESTAMP)


#: `writtenOn`'s older, pre-ISO-8601 form: "Dec 18, 2024 10:50:22 PM". Matched and mapped by
#: hand, not `strptime("%b")`, which reads the current locale's month names and would refuse
#: this English text on a machine set to another language. UTC is assumed, as the ISO form
#: this replaced always carried `Z`; nothing in the corpus has contradicted it so far.
_LEGACY_MONTH_NAMES = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip
_LEGACY_MONTHS = {m: i for i, m in enumerate(_LEGACY_MONTH_NAMES, start=1)}
_LEGACY_WRITTEN_ON = re.compile(
    r"^(?P<mon>[A-Za-z]{3}) (?P<day>\d{1,2}), (?P<year>\d{4}) "
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2}) (?P<ampm>AM|PM)$"
)


def _legacy_written_on(value: str) -> datetime | None:
    match = _LEGACY_WRITTEN_ON.match(value)
    if match is None or match["mon"] not in _LEGACY_MONTHS:
        return None
    hour = int(match["hour"]) % 12 + (12 if match["ampm"] == "PM" else 0)
    try:
        return datetime(
            int(match["year"]),
            _LEGACY_MONTHS[match["mon"]],
            int(match["day"]),
            hour,
            int(match["minute"]),
            int(match["second"]),
            tzinfo=UTC,
        )
    except ValueError:
        return None


def _note_timestamp(value: Any) -> str | None:
    """A note JSON `writtenOn` in Gerrit's REST format.

    Accepts ISO 8601 (`2024-12-18T22:50:22Z`) and the legacy form `_legacy_written_on` reads.
    Never raises: an unrecognised value is None, same as a missing one, so a corrupt timestamp
    on one comment does not abort the whole project-month; the caller counts and drops it.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        moment = _legacy_written_on(value)
        if moment is None:
            return None
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
        """Open a scratch repository, refusing one already pointed at a different remote.

        `remote.<name>.url` is multi-valued in git's own config model: an override on a single
        command adds a value rather than replacing the first, so a reused directory's original
        remote is what a fetch actually contacts however this call's `url` reads. A mismatch
        here means the caller's accounting (host, pacing, permission) would not describe what
        git does, so it is refused rather than silently followed.
        """
        _require_lazy_fetch_disabled()
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
        else:
            configured = (
                repo._run(["config", "--get", "remote.origin.url"], check=False)
                .stdout.decode()
                .strip()
            )
            if configured != url:
                raise GitError(
                    f"{path} already holds a remote for {configured!r}, this run asked for "
                    f"{url!r}: refusing to reuse a scratch repository pointed elsewhere"
                )
        return repo

    @property
    def host(self) -> str:
        """The host git will contact, lowercased and without a port: the pacer's own key."""
        return urlsplit(self.url).hostname or "local"

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
        """A local command. A lazy fetch fails rather than reaching the network.

        `GIT_NO_LAZY_FETCH=1` in `_isolated_env` is what makes that failure happen; `Repo.open`
        checks once per process that this git actually honors it.
        """
        return self._run(list(args), stdin=stdin).stdout

    def _network(self, purpose: str, args: Sequence[str], stdin: bytes | None, items: int) -> bytes:
        permission = GIT_PERMITTED.get(self.host)
        if urlsplit(self.url).scheme not in ("file", "") and (
            permission is None or not permission.permitted
        ):
            reason = permission.reason if permission else "not recorded in GIT_PERMITTED"
            raise GitError(f"refusing to contact {self.host} over the network: {reason}")
        self.pacer.wait(self.host)
        with tempfile.NamedTemporaryFile(prefix="sphragis-curl-", suffix=".trace") as trace:
            started = time.monotonic()
            done = self._run(
                [
                    "-c",
                    "http.cookieFile=",
                    "-c",
                    "credential.helper=",
                    "-c",
                    "http.followRedirects=false",
                    *args,
                ],
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

    def fetch_refs(
        self, purpose: str, refs: Sequence[str], *options: str, force: bool = False
    ) -> list[str]:
        """Fetch exact refs to the same names locally; return the ones the server lacks.

        A missing ref fails the whole fetch, and a change can vanish upstream (deleted, made
        private). The missing ref is dropped and the batch retried, one extra fetch per
        vanished change rather than one request per change. Refs already held are not
        fetched again, so a rerun over the same scratch repository costs no requests -- unless
        `force`, which always fetches: a ref an earlier, shallower fetch already holds (the
        seal's tip probe) must still be re-requested to deepen it.
        """
        missing: list[str] = []
        if force:
            pending = list(refs)
        else:
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

    def list_remote(self, purpose: str, *patterns: str) -> list[tuple[str, str]]:
        """`git ls-remote` for one or more patterns, in one request: (object id, ref name) pairs.

        Git sends no ref prefix for an ls-remote pattern, so the server lists every ref and
        the client filters. That is one request, but a large one: a wildcard listing over
        chromium/src timed out. Use it only on repositories of moderate size.
        """
        out = self._network(purpose, ["ls-remote", "origin", *patterns], None, 1)
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


#: `refs/heads/*` is read for every organization: REST's `status:merged` query has no branch
#: filter, so the git route reads every branch Gerrit can accept a change on. Chromium also
#: submits onto release branches under this second namespace.
EXTRA_BRANCH_NAMESPACES = {"chromium": ("refs/branch-heads/*",)}
DEFAULT_BRANCH_NAMESPACES = ("refs/heads/*",)


def branch_namespaces(org: str) -> tuple[str, ...]:
    """The ref-listing patterns this org's changes can be merged under."""
    return DEFAULT_BRANCH_NAMESPACES + EXTRA_BRANCH_NAMESPACES.get(org, ())


def branch_refs(repo: Repo, org: str, only: Sequence[str] = ()) -> list[str]:
    """Every branch ref Gerrit accepts changes on for this org, or just `only` if given.

    One `ls-remote` of every namespace the org uses, since a wildcard listing over a
    repository the size of chromium/src is one large request, not many small ones. `only`
    matches a full ref (`refs/heads/main`) or a bare name (`main`), against what the listing
    actually holds -- a name that does not exist yields nothing rather than a guessed ref.
    """
    pairs = repo.list_remote("branch_refs", *branch_namespaces(org))
    available = sorted({ref for _, ref in pairs if ref})
    if not only:
        return available
    wanted = set(only)
    return sorted(ref for ref in available if ref in wanted or ref.rsplit("/", 1)[-1] in wanted)


def fetch_history(repo: Repo, refs: Sequence[str], since: str) -> None:
    """Every named branch's commits since `since`, without trees or blobs, in one fetch.

    Commits alone are what enumeration reads, and they are small; the trees `files` needs
    are fetched afterwards for the merged commits only, which on a repository the size of
    chromium/src is the difference between a month's trees and every tree since `since`.

    Git has no upper date bound on a fetch, so this transfers every commit up to each branch's
    tip, including merges after the month being collected. They are filtered out in
    `merged_commits` before anything is recorded, and the scratch repository holding them is
    deleted with the run.
    """
    repo.fetch(
        "history",
        [f"+{ref}:{ref}" for ref in refs],
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
    refs: Sequence[str],
    since: str,
    end: str,
    *,
    review_host: str,
    project: str,
    by_commit: Mapping[str, int] | None = None,
) -> tuple[list[MergedCommit], dict[str, int]]:
    """Candidate changes: commits on any of `refs` committed in [since, end), mapped to a change.

    Every branch is walked in one `git log` over all `refs`: a commit reachable from more than
    one is still shown once, so only a change whose commit differs per branch (a cherry-pick)
    can be found twice, and `seen` dedups it to one entry by change number. `--since`/`--until`
    let git skip most of the walk itself; the same bound is re-checked on each commit's own
    timestamp, since git's date filters stop at the first commit outside the window along a
    parent chain, which a history combining several branches cannot rely on.

    A commit's date is a candidate filter, never the merge date. Under the rewriting submit
    strategies (Chromium's cherry-pick) the committer date is the merge; AOSP merges the
    uploaded commit unchanged, often through a merge commit, so its committer date is the
    upload, days before the merge. The month a change belongs to is therefore decided later,
    from NoteDb's own submission time (`collect`'s `submitted_between`), and `since` sits
    `SLACK_DAYS` before the month so a change uploaded before the month and merged
    inside it is still a candidate. One uploaded more than that before its merge is missed.

    A change is found by its `Reviewed-on:` trailer, or else by its commit id among the
    patch-set refs (`by_commit`), for hosts that stamp no trailer; neither depends on which
    branch carried the commit.
    """
    start = since
    lower = datetime.fromisoformat(start).replace(tzinfo=UTC).timestamp()
    upper = datetime.fromisoformat(end).replace(tzinfo=UTC).timestamp()
    if not refs:
        return [], {}
    raw = repo.git(
        "log",
        "--format=%H%x00%P%x00%ct%x00%B%x1e",
        # An explicit UTC offset, so this does not depend on the process's local timezone the
        # way a bare date string parsed by git's approxidate would.
        f"--since={start} 00:00:00 +0000",
        f"--until={end} 00:00:00 +0000",
        *refs,
    )
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
    note_parse_errors: int = 0
    comment_timestamp_errors: int = 0


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
    raw_comments, record.note_parse_errors = _note_comments(repo, tip)
    record.comments, record.comment_timestamp_errors = _filter_bad_timestamps(raw_comments)
    return record


def _note_blobs(repo: Repo, tip: str) -> list[str]:
    """Blob ids of every note in the notes tree at `tip`."""
    listing = repo.git("ls-tree", "-r", "-z", tip).decode()
    return [entry.split()[2] for entry in listing.split("\0") if entry and " blob " in entry]


def _note_comments(repo: Repo, tip: str) -> tuple[list[dict[str, Any]], int]:
    """Every published inline comment in the notes tree at `tip`, and note blobs that failed.

    A blob that is not valid JSON is counted (`note_parse_errors`) rather than skipped without
    a trace, so a corrupt or truncated note is visible in the month record instead of quietly
    reducing a change's comments.
    """
    comments: list[dict[str, Any]] = []
    errors = 0
    for blob in repo.read_objects(_note_blobs(repo, tip)).values():
        try:
            note = json.loads(blob)
        except ValueError:
            errors += 1
            continue
        comments.extend(note.get("comments") or [])
    return comments, errors


def _filter_bad_timestamps(
    comments: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Drop a comment whose `writtenOn` is a non-empty string `_note_timestamp` cannot read.

    Counted rather than left to abort the project-month: an absent or empty `writtenOn` is not
    an error (`rest_comment` already tolerates it, as REST's own payload can), only one that is
    present and unreadable.
    """
    kept: list[dict[str, Any]] = []
    errors = 0
    for comment in comments:
        written = comment.get("writtenOn")
        if isinstance(written, str) and written and _note_timestamp(written) is None:
            errors += 1
            continue
        kept.append(dict(comment))
    return kept, errors


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


@dataclass(frozen=True)
class _CommitInfo:
    """A commit's own header fields, which a shallow graft does not rewrite."""

    tree: str
    parents: tuple[str, ...]
    message: str


def _commit_info(raw: bytes) -> _CommitInfo:
    header, _, message = raw.partition(b"\n\n")
    lines = header.decode(errors="replace").splitlines()
    tree = next(line[5:] for line in lines if line.startswith("tree "))
    parents = tuple(line[7:] for line in lines if line.startswith("parent "))
    return _CommitInfo(tree, parents, message.decode(errors="replace"))


def _commit_infos(repo: Repo, commits: Iterable[str]) -> dict[str, _CommitInfo]:
    return {oid: _commit_info(raw) for oid, raw in repo.read_objects(commits).items()}


def _commit_parents(repo: Repo, commits: Iterable[str]) -> dict[str, list[str]]:
    """Parent ids from each commit's own header."""
    return {oid: list(info.parents) for oid, info in _commit_infos(repo, commits).items()}


#: Gerrit's other ChangeKind values; REWORK (imported from `examples`) is the one `refine`
#: acts on. Kept as plain strings, matching what REST itself sends on `revisions.*.kind`.
NO_CHANGE_KIND = "NO_CHANGE"
NO_CODE_CHANGE_KIND = "NO_CODE_CHANGE"
TRIVIAL_REBASE_KIND = "TRIVIAL_REBASE"
TRIVIAL_REBASE_MESSAGE_KIND = "TRIVIAL_REBASE_WITH_MESSAGE_UPDATE"


def _replay_tree(repo: Repo, *, base: str, onto: str, commit: str) -> str | None:
    """The tree `git merge-tree` writes for replaying `commit` onto `onto`, base `base`.

    None when the merge could not be verified: a real conflict (exit 1, and a conflicted tree
    would not equal the successor's anyway) or a merge git could not attempt at all because
    this repository lacks the blob content a content-level merge needs (a non-zero exit with
    no usable tree). Either way the step is not confirmed trivial, so the caller treats it as
    REWORK -- the same conservative call the spec makes for an actual conflict.
    """
    done = repo._run(  # noqa: SLF001 - a local plumbing command, not a network operation
        ["merge-tree", "--write-tree", f"--merge-base={base}", onto, commit], check=False
    )
    if done.returncode != 0:
        return None
    return done.stdout.decode().split()[0]


def _successor_kind(
    repo: Repo, a_commit: str, b_commit: str, a: _CommitInfo, b: _CommitInfo
) -> str:
    """Gerrit's ChangeKind of a successor revision `b`, relative to its predecessor `a`.

    NO_CHANGE: same tree, same parents, same message. NO_CODE_CHANGE: same tree and parents,
    a different message. TRIVIAL_REBASE(_WITH_MESSAGE_UPDATE): a single, different parent on
    each side, and replaying `a` onto `b`'s parent (three-way, base `a`'s parent) reproduces
    `b`'s tree exactly (`_replay_tree`). MERGE_FIRST_PARENT_UPDATE is not attempted: a merge
    commit on either side is told apart from REWORK only by whether the trees already match
    (documented simplification -- Gerrit's own same-tree-after-replay check on the first
    parent is not run), so a merge pair only reaches NO_CHANGE/NO_CODE_CHANGE or REWORK.
    """
    same_message = a.message == b.message
    if a.parents == b.parents:
        if a.tree != b.tree:
            return REWORK
        return NO_CHANGE_KIND if same_message else NO_CODE_CHANGE_KIND
    if len(a.parents) == 1 and len(b.parents) == 1:
        merged_tree = _replay_tree(repo, base=a.parents[0], onto=b.parents[0], commit=a_commit)
        if merged_tree is None or merged_tree != b.tree:
            return REWORK
        return TRIVIAL_REBASE_KIND if same_message else TRIVIAL_REBASE_MESSAGE_KIND
    if len(a.parents) > 1 and len(b.parents) > 1:
        if a.tree != b.tree:
            return REWORK
        return NO_CHANGE_KIND if same_message else NO_CODE_CHANGE_KIND
    # A root commit on one side, or one side a merge and the other not: not attempted.
    return REWORK


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
    kinds: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """A change in the REST `ChangeInfo` shape, unscrubbed.

    Each revision also carries `parents`, its commit's parent ids, which REST returns only
    with the CURRENT_COMMIT option the REST route did not request: two patch sets on
    different parents were separated by a rebase. `kinds` is each revision's ChangeKind
    (`_successor_kind`), computed from git objects rather than read from a server cache, so a
    revision it does not cover carries `kind_source: "git"` but no `kind`.
    """
    full_branch = record.branch or ""
    branch = full_branch.removeprefix("refs/heads/")
    encoded = quote(project, safe="")
    current = max(record.patch_sets) if record.patch_sets else None
    kinds = kinds or {}
    revisions = {
        ps_data["commit"]: {
            "_number": ps,
            "branch": full_branch,
            "created": gerrit_timestamp(ps_data["created"]),
            "ref": change_ref(record.number, ps),
            "uploader": {"_account_id": ps_data["uploader"]},
            "parents": parents.get(ps_data["commit"], []),
            "kind_source": "git",
            **({"kind": kinds[ps_data["commit"]]} if ps_data["commit"] in kinds else {}),
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


#: Deep enough for any meta chain (one commit per patch set, comment batch or status change);
#: git stops at the real root regardless, so this only has to exceed the largest chain seen.
_META_FULL_DEPTH = "1000000"


def _commit_committer_time(raw: bytes) -> int:
    """The `committer` line's epoch seconds, from a commit object's own header."""
    header = raw.split(b"\n\n", 1)[0].decode(errors="replace")
    line = next(line for line in header.splitlines() if line.startswith("committer "))
    return int(line.rsplit(" ", 2)[1])


def _meta_tips(repo: Repo, numbers: Sequence[int]) -> tuple[dict[int, int], list[int]]:
    """Each candidate's meta-ref tip commit time, fetched at depth 1 without blobs.

    Read before any fuller fetch, so a change whose last NoteDb update reaches the sealed
    test window is dropped before its meta chain or notes cost anything. Returns (committer
    time by number, numbers whose ref the server lacks).
    """
    refs = {change_ref(n, "meta"): n for n in numbers}
    missing_refs = set(repo.fetch_refs("meta_tip", list(refs), "--depth=1", "--filter=blob:none"))
    tips: dict[str, str] = {}
    if set(refs) - missing_refs:
        raw = repo.git("for-each-ref", "--format=%(refname)%09%(objectname)", "refs/changes/")
        for line in raw.decode().splitlines():
            ref, _, oid = line.partition("\t")
            if ref in refs and ref not in missing_refs:
                tips[ref] = oid
    objects = repo.read_objects(tips.values())
    times = {refs[ref]: _commit_committer_time(objects[oid]) for ref, oid in tips.items()}
    return times, [refs[r] for r in missing_refs]


def collect(
    repo: Repo,
    numbers: Sequence[int],
    *,
    project: str,
    merged: Mapping[int, MergedCommit] | None = None,
    submitted_between: tuple[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch and read `numbers` from one project: unscrubbed rows with their comments and diffs.

    A fixed number of batched fetches, whatever the number of changes. First, the tip of each
    candidate's meta ref alone (depth 1, no blobs): a change last updated at or after the
    sealed test window's start is dropped there, before its meta chain or notes are fetched at
    all, and counted `touches_test_window`. For the rest: the full meta refs and their note
    blobs; the patch-set commits the comments need, by id and without blobs or history; the
    trees of the merged commits and their parents; then exactly the blobs those diffs and the
    `files` counts read. A change NoteDb does not record as submitted inside
    `submitted_between` costs one meta-chain-and-notes fetch to learn that (its submission
    time is not known any sooner), then is dropped before anything else -- patch sets, diffs
    or blobs.
    """
    merged = merged or {}
    counts: Counter[str] = Counter()
    tip_times, tip_missing = _meta_tips(repo, numbers)
    sealed = {n for n, when in tip_times.items() if gerrit_timestamp(when) >= TEST_WINDOW_START}
    counts["touches_test_window"] = len(sealed)
    skip = sealed | set(tip_missing)
    candidates = [n for n in numbers if n not in skip]
    missing = set(
        repo.fetch_refs(
            "meta",
            [change_ref(n, "meta") for n in candidates],
            f"--depth={_META_FULL_DEPTH}",
            force=True,
        )
    )
    counts["meta_missing"] = len(tip_missing) + sum(
        change_ref(n, "meta") in missing for n in candidates
    )
    held = [n for n in candidates if change_ref(n, "meta") not in missing]
    repo.fetch_objects(
        "notes", [oid for n in held for oid in _note_blobs(repo, change_ref(n, "meta"))]
    )
    records = {}
    for number in held:
        record = read_change(repo, number)
        counts["note_parse_errors"] += record.note_parse_errors
        counts["comment_timestamp_errors"] += record.comment_timestamp_errors
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
    infos = _commit_infos(repo, every)
    parents = {oid: list(info.parents) for oid, info in infos.items()}
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

    # Which consecutive patch-set pairs need a trivial-rebase replay to tell REWORK apart from
    # TRIVIAL_REBASE: single, differing parents on each side. `a`, and both sides' parents, need
    # their full tree (not just the commit header `every` already carries) for `git merge-tree`.
    kind_roots: set[str] = set()
    for record in records.values():
        ordered = sorted(record.patch_sets)
        for prev_ps, ps in zip(ordered, ordered[1:], strict=False):
            a_commit = record.patch_sets[prev_ps]["commit"]
            b_commit = record.patch_sets[ps]["commit"]
            a_info, b_info = infos.get(a_commit), infos.get(b_commit)
            if a_info is None or b_info is None or a_info.parents == b_info.parents:
                continue
            if len(a_info.parents) == 1 and len(b_info.parents) == 1:
                kind_roots.update((a_commit, a_info.parents[0], b_info.parents[0]))
    roots = sorted(
        commits
        | upstream
        | kind_roots
        | {oid for m in counted.values() for oid in (m.commit, m.parents[0])}
    )
    if roots:
        # Read from the commit objects: `rev-parse <commit>^{tree}` loads the tree itself.
        listed = repo.git(
            "log", "--no-walk", "--stdin", "--format=%T", stdin="\n".join(roots).encode()
        )
        root_trees = listed.decode().split()
        repo.fetch_objects("trees", root_trees, "--filter=blob:none")

    kind_by_commit: dict[str, str] = {}
    for record in records.values():
        ordered = sorted(record.patch_sets)
        if ordered:
            kind_by_commit[record.patch_sets[ordered[0]]["commit"]] = REWORK
        for prev_ps, ps in zip(ordered, ordered[1:], strict=False):
            a_commit = record.patch_sets[prev_ps]["commit"]
            b_commit = record.patch_sets[ps]["commit"]
            a_info, b_info = infos.get(a_commit), infos.get(b_commit)
            if a_info is None or b_info is None:
                continue
            kind_by_commit[b_commit] = _successor_kind(repo, a_commit, b_commit, a_info, b_info)
    counts["kind_computed"] = len(kind_by_commit)

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
        row = _row(record, project, merged.get(number), parents, kind_by_commit)
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
    branches: Sequence[str] = (),
    workdir: Path | None = None,
    by_patch_set_ref: bool | None = None,
    base_url: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every change NoteDb records as submitted during `month`, on any branch it can merge to.

    Mirrors REST's `status:merged` query, which carries no branch filter: every `refs/heads/*`
    ref is read by default (plus an org's other namespaces, `branch_namespaces`), and a change
    is counted once even when its commit reaches more than one. `branches` restricts this to
    named branches; empty means every branch. Window membership is decided later, by each
    change's NoteDb creation time, exactly as for the REST route. `by_patch_set_ref` adds the
    ref-listing fallback for hosts whose merged commits carry no `Reviewed-on:` trailer (AOSP);
    it lists every ref in the project, so it is for repositories of moderate size only.
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
            refs = branch_refs(repo, org, branches)
            fetch_history(repo, refs, since)
            by_commit = (
                patch_set_commits(repo, repo.list_remote("patch_set_refs", "refs/changes/*"))
                if by_patch_set_ref
                else None
            )
            merged, enumeration = merged_commits(
                repo,
                refs,
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
                "branches_read": refs,
                "enumeration": enumeration,
                "collect": counts,
                "status": dict(status),
            }
            operations += repo.operations
            requests += repo.http_requests
    record = {
        "route": "notedb",
        "base_url": base,
        "branches": sorted(branches) if branches else "all",
        "month": month,
        "submitted_between": [start, end],
        "candidates_committed_since": since,
        "projects": per_project,
        "git_operations": operations,
        "http_requests": requests,
        "diff": DIFF_METHOD,
        "gaps": GAPS,
        "fetch_rules": FETCH_RULES,
        "count": len(rows),
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
    }
    return rows, record
