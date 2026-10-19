"""Corpus pipeline entry point: the one place that touches disk or the network."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from sphragis.corpus.build import DROP_REASONS, CommentFetcher, DiffFetcher, build_from_change
from sphragis.corpus.fetchers import scrubbed_comment_fetcher, scrubbed_diff_fetcher
from sphragis.corpus.gerrit import Transport, created_on_or_after, fetch_changes
from sphragis.corpus.github import GITHUB_KEY, GITHUB_ORGS, collect_month, github_fetchers
from sphragis.corpus.github_api import GitHubAPI
from sphragis.corpus.grants import unheld
from sphragis.corpus.hosts import claim_host
from sphragis.corpus.load import (
    build_record,
    raw_digest,
    refined_dir,
    refined_examples,
    write_build_record,
    write_source_record,
)
from sphragis.corpus.manifest import verify
from sphragis.corpus.notedb import (
    GIT_HOSTS,
    GIT_PERMITTED,
    NOTEDB_KEY,
    _raise_on_sigterm,  # noqa: F401 -- fetch_month owns the handling; re-exported for test access
    embedded_fetchers,
    fetch_month,
    valid_project_name,
)
from sphragis.corpus.pacing import Pacer
from sphragis.corpus.pipeline import freeze_windows, run_dedup, run_split, window_body
from sphragis.corpus.refine import RULES_VERSION, index_changes, refine
from sphragis.corpus.rules import BUILD_RULES
from sphragis.corpus.scrub import scrub
from sphragis.corpus.split import seal_open, seal_path
from sphragis.corpus.storage import (
    iter_snapshot,
    read_snapshot,
    refused_snapshot,
    write_snapshot,
)
from sphragis.corpus.windows import WINDOWS
from sphragis.durable import (
    DurableLog,
    exclusive,
    read_json_object,
    write_atomic,
    write_json_atomic,
)

STAGES = ("fetch", "build", "stamp", "refine", "dedup", "split", "freeze", "verify")

# One Gerrit instance is one organization. OpenStack and Qt checked live 2026-09-14. AOSP
# checked live 2026-09-18: it answers residential addresses and refuses datacenter ranges, so it
# is fetched from a workstation, and its volume needs --project to stay bounded. AOSP is RQ2's
# third organization, for a cross-organization C++ cell beside Qt; RQ1 is registered on two.
# Chromium checked live 2026-09-22, from a workstation as AOSP is fetched. Like AOSP it needs
# --project, and chromium/src alone merges more in a month than the 10,000 results the host
# serves for one query, which fetch_changes refuses rather than truncates. It is the C++
# organization beside Qt that the study's windows can still use: AOSP's public review stopped
# on 2025-03-27.
GERRIT = {
    "aosp": "https://android-review.googlesource.com",
    "chromium": "https://chromium-review.googlesource.com",
    # Checked live 2026-09-29: about 29,800 changes merged in the training window. A candidate
    # organization for H1, and at most an exploratory H2 cell (research log, 2026-09-29).
    "lineageos": "https://review.lineageos.org",
    "openstack": "https://review.opendev.org",
    "qt": "https://codereview.qt-project.org",
    # Wikimedia serves Gerrit under /r. Checked live 2026-09-26; a candidate for Qt's place in H1
    # (research log, 2026-09-26), collected under the Wikitech Robot policy recorded below.
    "wikimedia": "https://gerrit.wikimedia.org/r",
}


# The review hosts a REST client may call, each with the reason it may. robots.txt is
# Disallow: / on android-review, chromium-review and codereview.qt-project.org, and Google's
# terms bar automated access that ignores it, so those are fetched over git where a git host
# serves NoteDb, or not at all until the host grants permission in writing, as Qt's did. A host
# absent here is refused.
class GrantScope(NamedTuple):
    """What a written grant covers, enforced wherever the host is read: the projects a listing may
    name, the created dates a fetch may keep (`created_before` exclusive, as `before:` reads), and
    the commented changes a build may read a month, None where the grant sets no count."""

    projects: tuple[str, ...]
    created_from: str
    created_before: str
    read_cap: int | None
    # The last month `YYYY-MM` whose listing is read for the changes the scope covers. A month
    # lists the changes last updated in it, so a change created inside the grant and updated
    # later is listed only past `created_before`; None reads no month past it.
    listed_through: str | None = None


def last_open_month() -> str:
    """The last month `YYYY-MM` before the sealed test window starts."""
    year, month = (int(part) for part in WINDOWS["test"][0][:7].split("-"))
    year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return f"{year:04d}-{month:02d}"


def grant_months(scope: GrantScope, month: str) -> bool:
    """Whether month `YYYY-MM` can hold a change `scope` lets a fetch keep: from `created_from`'s
    month through the month `created_before` falls in, unless it falls on that month's first, or
    through `listed_through` where the scope sets it. A fetch keeps only changes created inside
    the grant whatever the month (`created_in_grant`)."""
    if scope.created_from[:7] > month:
        return False
    if scope.listed_through is not None:
        return month <= scope.listed_through
    return f"{month}-01" < scope.created_before


def created_in_grant(scope: GrantScope, created: Any) -> bool:
    """Whether a change created at Gerrit's `YYYY-MM-DD hh:mm:ss` time is one `scope` covers."""
    return isinstance(created, str) and scope.created_from <= created < scope.created_before


#: Per scoped host, the change numbers its grant covers that a run has admitted (`admit`): the
#: only ones its transport asks about one at a time.
Admitted = dict[str, set[str]]

_BY_CHANGE = re.compile(r"/changes/([^/]+)(?:/|$)")


def host_of(org: str) -> str:
    """The REST host an organization's Gerrit is read from, "" for an organization with none."""
    return urllib.parse.urlsplit(GERRIT.get(org, "")).hostname or ""


def admit(admitted: Admitted, host: str, rows: Iterable[Mapping[str, Any]]) -> None:
    """Let `host`'s transport ask about the changes of `rows` its grant covers, each checked here
    (`created_in_grant`, a granted project); a host without a scope needs no admission."""
    scope = REST_PERMITTED[host].scope if host in REST_PERMITTED else None
    if scope is None:
        return
    admitted.setdefault(host, set()).update(
        str(row["_number"])
        for row in rows
        if row.get("project") in scope.projects and created_in_grant(scope, row.get("created"))
    )


def unadmitted_change(target: str, scope: GrantScope | None, admitted: set[str]) -> str | None:
    """The change a request about one change names, if a scoped host has not admitted it."""
    found = _BY_CHANGE.search(target.partition("?")[0])
    if scope is None or found is None:
        return None
    change = urllib.parse.unquote(found.group(1))
    return None if change in admitted else change


def rest_rows(changes: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The changes a build reads from the REST host: not NoteDb's, not GitHub's."""
    return [c for c in changes if NOTEDB_KEY not in c and GITHUB_KEY not in c]


def outside_grant(scope: GrantScope | None, month: str, rows: Sequence[Mapping[str, Any]]) -> str:
    """Why a month's REST rows would be read past what a host's grant covers, "" if they would
    not: the month, a project it does not name, or a change created outside its window. A
    change's comments and diffs are asked by number, which names neither, so a path that asks by
    number (the build, the comment-count probe) refuses here first, a snapshot held from before
    the grant included, and admits what passes (`admit`); the transport asks about no other."""
    if scope is None or not rows:
        return ""
    if not grant_months(scope, month):
        return "outside the host's grant"
    if {row.get("project") for row in rows} - set(scope.projects):
        return "rows of projects outside the host's grant"
    early_or_late = sum(not created_in_grant(scope, row.get("created")) for row in rows)
    if early_or_late:
        return f"{early_or_late} rows created outside the host's grant"
    return ""


class RestPermission(NamedTuple):
    reason: str
    # seconds between requests: the robots.txt Crawl-delay, or the interval a written grant sets
    # in its place; pacing never goes below it
    crawl_delay: float
    # robots.txt Disallow patterns for the host, matched as robots.txt does (prefix, `*`, `$`)
    disallow: tuple[str, ...] = ()
    # seconds the host asks a client to stay quiet after any 5xx
    error_pause: float = 0.0
    # the host asks for concurrency 1, so one process at a time may hold a transport to it
    single_client: bool = False
    # seconds of silence the host asks for between one response and the next request
    idle_gap: float = 0.0
    # a grant that covers part of the host; None where the whole host may be read
    scope: GrantScope | None = None
    # the permission is a written grant to this study's collector, not a public policy, so it
    # holds only on a machine that lists the host in `grants_file()`
    written_grant: bool = False


#: The census of AOSP's projects at its release tag (`scripts/aosp_census.py`); the C++-majority
#: projects it names are the only ones AOSP's grant scope lists (research log, 2026-10-18).
AOSP_CENSUS = Path(__file__).resolve().parents[2] / "datasets" / "results" / "aosp-census.json"


def census_projects(path: Path = AOSP_CENSUS) -> tuple[str, ...]:
    """The C++-majority projects a census names, none before one is committed, which leaves every
    listing of the scope refused."""
    try:
        projects = json.loads(path.read_text())["cpp_majority"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        # Unreadable as well as absent: the scope then names nothing, and no stage of any other
        # organization fails on this import.
        return ()
    return tuple(projects) if isinstance(projects, list) else ()


REST_PERMITTED = {
    "review.lineageos.org": RestPermission(
        "robots.txt disallows everything only to HTTrack, puf, MSIECrawler and Nutch and asks no"
        " Crawl-delay (checked 2026-09-29); the project's legal page sets no terms on automated"
        " access; paced as OpenStack asks, one client at a time, quiet five minutes after a 5xx",
        2.0,
        error_pause=300.0,
        single_client=True,
    ),
    "codereview.qt-project.org": RestPermission(
        "robots.txt is Disallow: / with Crawl-Delay 3 (checked 2026-09-23); Qt's Gerrit"
        " administrators granted the REST API by email on 2026-10-15 (QTQAINFRA-8064) at a 2 s"
        " interval from one client with the identifying User-Agent, told the source IP address;"
        " quiet fifteen minutes after a 5xx, since Qt's firewall once outlasted 25 minutes of"
        " silence (pacing.Pacer)",
        2.0,
        # The grant supersedes robots.txt's Disallow: /, so no pattern is recorded.
        disallow=(),
        error_pause=900.0,
        single_client=True,
        written_grant=True,
    ),
    "review.opendev.org": RestPermission(
        "robots.txt disallows no path and asks Crawl-delay 2 (checked 2026-09-23)", 2.0
    ),
    "chromium-review.googlesource.com": RestPermission(
        "robots.txt is Disallow: / (checked 2026-09-23); Chromium's infra-dev list (Chrome"
        " Infra) answered on 2026-10-15 that it has no concerns with the Gerrit REST API at the"
        " requested rate, for about 2,000 merged changes with their inline comments created"
        " 2024-11 to 2025-08, from one client at no more than one request a second (research"
        " log, 2026-10-16); quiet five minutes after a 5xx, as for LineageOS",
        1.0,
        # The grant supersedes robots.txt's Disallow: /, so no pattern is recorded.
        disallow=(),
        error_pause=300.0,
        single_client=True,
        # The pool fixed in the research log (2026-10-16): with the comment-count probe's 330
        # reads, at most 170 x 10 + 330 = 2,030 changes.
        scope=GrantScope(("v8/v8",), "2024-11-01", "2025-09-01", 170),
        written_grant=True,
    ),
    "android-review.googlesource.com": RestPermission(
        "robots.txt is Disallow: / (checked 2026-09-23); Chrome Infra answered on infra-dev on"
        " 2026-10-15, in a thread titled for chromium-review and android-review, that it has no"
        " concerns with Gerrit's REST API at one client and no more than one request a second,"
        " and AJ reads the answer as covering AOSP (research log, 2026-10-18); quiet five minutes"
        " after a 5xx, as for Chromium",
        1.0,
        # The grant supersedes robots.txt's Disallow: /, so no pattern is recorded.
        disallow=(),
        error_pause=300.0,
        single_client=True,
        # Item 4 of the H2 plan: the census's C++-majority projects, changes created before
        # aosp-main went read-only; the answer sets a rate, not a count, so no read cap.
        # Listed through the month before the sealed test window, for the grant's changes updated
        # after aosp-main went read-only.
        scope=GrantScope(census_projects(), "2024-10-01", "2025-03-27", None, last_open_month()),
        written_grant=True,
    ),
    "gerrit.wikimedia.org": RestPermission(
        "robots.txt disallows the UI, gitiles, /r/a/, /r/q/ and patch and archive downloads, not"
        " the changes REST endpoints, and asks Crawl-delay 1 (checked 2026-09-26); the Wikitech"
        " Robot policy (modified 2026-03-16) asks concurrency 1, 1 s between requests, a"
        " 15-minute pause on any 5xx and an identifying User-Agent",
        2.0,
        disallow=(
            "/g",
            "/r/a/",
            "/r/plugins/gitiles",
            "/r/login/",
            "/r/q/",
            "/r/changes/*/revisions/*/patch?*",
            "/r/changes/*/revisions/*/archive?format=*",
        ),
        error_pause=900.0,
        single_client=True,
        idle_gap=1.0,
    ),
}


def unheld_grant(host: str) -> str | None:
    """Why the REST route may not read `host` from this machine although `REST_PERMITTED` lists
    it, None if it may: its permission is a written grant that `holds_grant` does not find."""
    permission = REST_PERMITTED.get(host)
    reason = None if permission is None else unheld(host, permission.written_grant)
    return None if reason is None else f"refusing {host}: {reason}"


def grant_scope(org: str) -> GrantScope | None:
    """The scope of the grant `org`'s REST host is read under, None for a whole-host permission."""
    permission = REST_PERMITTED.get(host_of(org))
    return permission.scope if permission else None


def out_of_scope_listing(target: str, scope: GrantScope) -> str | None:
    """Why a `/changes/?q=` listing on a scoped host is refused, None if it names only granted
    projects. A request about one change (its comments, a diff) names no project, so it passes
    here and is checked against the changes admitted under the grant (`unadmitted_change`)."""
    path, _, query = target.partition("?")
    if not path.rstrip("/").endswith("/changes"):
        return None
    asked = urllib.parse.parse_qs(query).get("q", [""])
    if len(asked) != 1:
        return "a listing with more than one query"
    q = asked[0]
    if re.search(r"\w(?=project:)", q):
        return "a listing by a project operator other than project:"
    named = re.findall(r"project:([^\s()]+)", q)
    if not named:
        return "a listing that names no project"
    if re.search(r"\b(OR|NOT)\b|(^|[\s(])-", q):
        return "a listing whose OR, NOT or negation could reach past its projects"
    outside = sorted(set(named) - set(scope.projects))
    return f"a listing of {outside}" if outside else None


# Sent on every request. Wikimedia's User-Agent policy asks for a client name, version and
# contact; the other hosts get the same identification.
USER_AGENT = "sphragis/0.1 (https://ajbarea.github.io; code-review research) python-http.client"


def robots_disallows(target: str, patterns: tuple[str, ...]) -> bool:
    """Whether a request path (with its query) matches a robots.txt Disallow pattern.

    As in RFC 9309: a pattern matches a prefix of the path, `*` matches any run of characters
    and a trailing `$` anchors the end.
    """
    for pattern in patterns:
        if not pattern:  # an empty Disallow allows everything
            continue
        anchored = pattern.endswith("$")
        body = pattern[:-1] if anchored else pattern
        regex = ".*".join(re.escape(part) for part in body.split("*"))
        if re.match(regex + (r"\Z" if anchored else ""), target):
            return True
    return False


SALT_ENV = "SPHRAGIS_CORPUS_SALT"


def require_salt() -> str:
    """The pseudonymisation salt, or exit.

    Pseudonyms must be stable across runs or a corpus built today will not compare with
    one built tomorrow, and the author filter in `build` compares pseudonyms.
    """
    salt = os.environ.get(SALT_ENV, "").strip()
    if not salt:
        raise SystemExit(
            f"{SALT_ENV} is unset. Generate one with "
            '`python -c "import secrets; print(secrets.token_hex(32))"`, export it in the '
            "shell that runs the corpus (nothing here reads a .env file), and keep it out of "
            "git: it is what makes the pseudonyms irreversible."
        )
    return salt


def calendar_month(value: str) -> str:
    """A `--month` as `YYYY-MM`: a grant's months are compared as strings, so `2024-9` would
    compare past the grant's first month."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a YYYY-MM month")
    return value


def calendar_date(value: str) -> str:
    """A `--cutoff` as `YYYY-MM-DD`: created dates compare as strings, so `2024-10-1` would drop
    the first nine days of the month."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])", value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a YYYY-MM-DD date")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sphragis.corpus")
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument(
        "--org",
        default="openstack",
        choices=sorted(set(GERRIT) | set(GIT_HOSTS) | set(GITHUB_ORGS)),
    )
    parser.add_argument(
        "--month", default="2024-10", type=calendar_month, help="YYYY-MM, for fetch"
    )
    parser.add_argument(
        "--expect-month",
        help="build: exit 0 if this YYYY-MM was built or found current, "
        f"{BUILD_NEEDS_REFETCH} if its snapshot is refused or missing (fetch it)",
    )
    parser.add_argument("--root", type=Path, default=Path("datasets/gerrit"))
    parser.add_argument(
        "--cutoff", default="2024-10-01", type=calendar_date, help="drop changes created before"
    )
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        help="fetch only these projects; repeatable, recorded in the snapshot's query",
    )
    parser.add_argument("--overwrite", action="store_true", help="replace an existing snapshot")
    parser.add_argument(
        "--reproduce",
        action="store_true",
        help="verify: also rerun dedup and split from the refined examples and compare windows",
    )
    parser.add_argument(
        "--via",
        choices=("rest", "git", "github"),
        default="rest",
        help="fetch through the REST API, read NoteDb over git (notedb.py), or collect a "
        "registered GitHub organization (github.py; needs GITHUB_TOKEN)",
    )
    parser.add_argument(
        "--branch",
        action="append",
        default=[],
        help="with --via git: restrict enumeration to this branch (repeatable); default is "
        "every branch Gerrit accepts changes on",
    )
    parser.add_argument(
        "--fewer-requests",
        action="store_true",
        help="build: skip a change whose snapshot records no comment (counted as "
        f"{UNCOMMENTED}) and ask each diff once per change; needs --count-probe",
    )
    parser.add_argument(
        "--read-cap",
        type=int,
        help="build: read at most this many changes a month (with --fewer-requests, of those with "
        f"comments), in a seeded order (the rest counted as {OVER_READ_CAP}); a grant's budget",
    )
    parser.add_argument(
        "--count-probe",
        type=Path,
        help="build --fewer-requests: the organization's comment_count_probe.py result, which "
        "must hold",
    )
    parser.add_argument(
        "--request-interval",
        type=float,
        default=1.0,
        help="minimum seconds between requests to one Gerrit host (fetch, build)",
    )
    parser.add_argument(
        "--allow-mixed-routes",
        action="store_true",
        help="fetch: allow this month's route (rest/git) to differ from the org's other months",
    )
    return parser


def http_transport(
    timeout: float = 15.0,
    *,
    min_interval: float = 0.0,
    host_intervals: Mapping[str, float] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    lock_dir: Path | None = None,
    wall: Callable[[], float] = time.time,
    admitted: Admitted | None = None,
) -> Transport:
    """A real HTTP transport over one persistent connection per host.

    A host with a grant scope is asked about one change only once its caller has admitted it
    (`admit`, which checks the change against the grant), so a path that forgets the check fails
    closed rather than reading past the grant.

    Separated so every test runs offline. The earlier transport opened a fresh TLS
    connection for every request under a single 60-second timeout. Against review.opendev.org
    about 1 in 40 fresh handshakes hangs (measured 2026-09-15: 39 of 40 answered in under
    0.2 s, one never did), so a month's build of thousands of requests spent most of its time
    waiting out dropped connections: a build ran 16 minutes on 1 second of CPU. Reusing the
    connection makes handshakes rare, which is also gentler on a community server, and the
    shorter timeout makes a dropped one cost seconds rather than a minute.

    Statuses are returned, never raised, so gerrit._get applies its retry budget and honours
    Retry-After. A connection failure is reported as 503, which is retryable.

    Requests to one host are held `min_interval` apart, or further when `REST_PERMITTED` records
    a longer crawl delay for it; `pacing.Pacer` records why the default is one a second.
    """
    connections: dict[str, http.client.HTTPConnection] = {}
    held: Admitted = {} if admitted is None else admitted
    if host_intervals is None:
        host_intervals = {host: p.crawl_delay for host, p in REST_PERMITTED.items()}
    pacer = Pacer(min_interval, host_intervals=host_intervals, clock=clock, sleep=sleep)

    def connect(scheme: str, host: str) -> http.client.HTTPConnection:
        if host not in connections:
            factory = (
                http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
            )
            connections[host] = factory(host, timeout=timeout)
        return connections[host]

    def drop(host: str) -> None:
        stale = connections.pop(host, None)
        if stale is not None:
            stale.close()

    def transport(url: str) -> tuple[int, dict[str, str], str]:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""  # lower-cased, without port or userinfo
        if host not in REST_PERMITTED:
            raise SystemExit(
                f"refusing {host}: no REST permission recorded, and robots.txt "
                "disallows automated clients on the review hosts not recorded there. "
                "Fetch over git (--via git) where a git host serves NoteDb, or record the host's "
                "permission in REST_PERMITTED"
            )
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        permission = REST_PERMITTED[host]
        refusal = unheld_grant(host)
        if refusal:
            raise SystemExit(refusal)
        if robots_disallows(target, permission.disallow):
            raise SystemExit(f"refusing {host}{target}: the host's robots.txt disallows the path")
        if permission.scope is not None:
            why = out_of_scope_listing(target, permission.scope)
            if why:
                raise SystemExit(
                    f"refusing {host}: {why}, outside its grant ({permission.scope.projects})"
                )
            change = unadmitted_change(target, permission.scope, held.get(host, set()))
            if change is not None:
                raise SystemExit(
                    f"refusing {host}: change {change} was not admitted under its grant (cli.admit)"
                )
        claim = claim_host(host, lock_dir) if permission.single_client else None

        def quiet(seconds: float) -> None:
            """Hold the host for `seconds`, here and, for a claimed host, for the next process."""
            pacer.hold(host, seconds)
            if claim is not None and seconds > 0:
                claim.hold_until(wall() + seconds)

        if claim is not None:
            remaining = claim.quiet_until() - wall()
            if remaining > 0:
                sleep(remaining)
        # One silent retry only for a keep-alive the server closed while idle, which is
        # routine and not a failure; anything else is reported and left to the retry budget.
        # Paced by host name, as permission is, so a port or letter case cannot open a second
        # pace, and the retry is a request like any other.
        for attempt in range(2):
            pacer.wait(host)
            connection = connect(parts.scheme, parts.netloc)
            try:
                connection.request(
                    "GET", target, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
                )
                response = connection.getresponse()
                body = response.read().decode()
                pacer.hold(host, permission.idle_gap)
                if response.status >= 500:
                    quiet(permission.error_pause)
                return response.status, dict(response.getheaders()), body
            except (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError):
                drop(parts.netloc)
                pacer.hold(host, permission.idle_gap)
                if attempt == 0:
                    continue
                quiet(permission.error_pause)
                return 503, {}, ""
            except (OSError, http.client.HTTPException):
                drop(parts.netloc)
                pacer.hold(host, permission.idle_gap)
                quiet(permission.error_pause)
                return 503, {}, ""
        return 503, {}, ""

    return transport


def refuse_if_sealed(root: Path, org: str, month: str) -> None:
    """Exit before any request if `month` could return test-window content while sealed.

    The test window is fetched only after in-principle acceptance; fetch timestamps are the
    Stage 2 evidence that collection followed acceptance, and nothing enforced it here.

    Every month from the window's start onward is refused, not only the months inside it.
    Gerrit's `after:`/`before:` filter on a change's last update, not its creation, so a
    fetch of a later month returns changes created inside the window and merged afterwards.

    The seal lives at `<root>/<org>/seal.json`; a missing seal is locked.
    """
    if f"{month}-01" < WINDOWS["test"][0]:
        return
    if not seal_open(root, org):
        raise SystemExit(
            f"refusing to fetch {org} {month}: on or after the sealed test window's start "
            f"({WINDOWS['test'][0]}), and {seal_path(root, org)} records no in-principle acceptance"
        )


def _fetched_routes(root: Path, org: str) -> set[str]:
    """Every route this org's snapshot records were fetched by.

    A record naming no `route` at all is a REST snapshot from before the field existed, not an
    unknown route to ignore: without this, an existing REST corpus was invisible to the mixed
    routes guard, and a git-route fetch into the same org-month went through unrefused.
    """
    raw = Path(root) / org / "raw"
    routes = set()
    for record_path in sorted(raw.glob("*.record.json")):
        record = read_json_object(record_path)
        if record is None:
            continue
        snapshot = record_path.with_name(record_path.name.replace(".record.json", ".ndjson.gz"))
        if not record.get("complete", True) and not snapshot.exists():
            # A new month stopped before its snapshot landed: no route fetched anything here.
            continue
        routes.add(record.get("route") or "rest")
    return routes


def refuse_mixed_routes(root: Path, org: str, route: str, *, allow: bool) -> None:
    """Refuse a fetch whose route differs from an org's other months, unless `allow` is set.

    A REST and a git-route month of the same org-month would carry different fields for the
    same reality (`kind` computed rather than server-cached, no `insertions`/`deletions`, and
    so on), so mixing them silently would make later stages compare examples that were never
    fetched the same way. `allow` records the decision, on the month that made it.
    """
    if allow:
        return
    other = _fetched_routes(root, org) - {route}
    if other:
        raise SystemExit(
            f"refusing {org}: already fetched via {sorted(other)}, this run is {route!r}; "
            "pass --allow-mixed-routes to mix, or fetch the rest of the org the same way"
        )


def _stage_fetch(args: argparse.Namespace) -> int:
    """One organization-month into an immutable snapshot, scrubbed on the way in."""
    salt = require_salt()
    refuse_if_sealed(Path(args.root), args.org, args.month)
    # One fetch of an org-month at a time, whatever the route: a second would replace a month
    # the first is still writing, or append to a checkpoint the first rewrites.
    with exclusive(Path(args.root) / ".locks" / args.org / f"{args.month}.lock"):
        return _fetch_month(args, salt)


def _fetch_month(args: argparse.Namespace, salt: str) -> int:
    """The route-specific half of `_stage_fetch`, run while the org-month is held."""
    if args.via == "git":
        return _stage_fetch_git(args, salt)
    if args.via == "github":
        return _stage_fetch_github(args, salt)
    if args.org not in GERRIT:
        raise SystemExit(f"{args.org} has no REST host here; fetch it with --via git")
    refuse_mixed_routes(Path(args.root), args.org, "rest", allow=args.allow_mixed_routes)
    scope = grant_scope(args.org)
    if scope is not None:
        if not args.project or set(args.project) - set(scope.projects):
            raise SystemExit(f"{args.org}'s grant covers --project {' '.join(scope.projects)} only")
        if not grant_months(scope, args.month):
            raise SystemExit(
                f"{args.org}'s grant covers changes created {scope.created_from} to before "
                f"{scope.created_before}, not {args.month}"
            )
        if args.cutoff < scope.created_from:
            raise SystemExit(
                f"{args.org}'s grant keeps changes created from {scope.created_from}: "
                f"pass --cutoff {scope.created_from}"
            )
    year, month = args.month.split("-")
    following = (
        f"{int(year) + (month == '12')}-{'01' if month == '12' else f'{int(month) + 1:02d}'}"
    )
    query = f"status:merged after:{args.month}-01 before:{following}-01"
    if scope is not None:
        # A scoped host answers no OR (`out_of_scope_listing`), so each project is its own listing.
        queries = [f"{query} (project:{p})" for p in args.project]
    elif args.project:
        queries = [query + " (" + " OR ".join(f"project:{p}" for p in args.project) + ")"]
    else:
        queries = [query]
    transport = http_transport(min_interval=args.request_interval)
    changes, records = [], []
    for asked in queries:
        listed, listing = fetch_changes(
            GERRIT[args.org], asked, transport=transport, options=("ALL_REVISIONS",)
        )
        changes.extend(listed)
        records.append(listing)
    numbers = [c["_number"] for c in changes]
    if len(set(numbers)) != len(numbers):
        raise SystemExit(f"{args.org} {args.month}: the listings repeat a change")
    record = (
        records[0]
        if len(records) == 1
        else {
            "base_url": GERRIT[args.org],
            "listings": records,
            "pages": sum(r["pages"] for r in records),
            "count": len(changes),
        }
    )
    kept = created_on_or_after(changes, args.cutoff)
    before_cutoff, after_grant = len(changes) - len(kept), 0
    if scope is not None:
        in_grant = [c for c in kept if created_in_grant(scope, c["created"])]
        after_grant, kept = len(kept) - len(in_grant), in_grant
        record = {**record, "created_after_grant": after_grant}
    scrubbed = [scrub(change, salt) for change in kept]
    record = {
        **record,
        "route": "rest",
        "mixed_routes_allowed": bool(args.allow_mixed_routes),
    }
    path = write_snapshot(
        args.root, args.org, args.month, scrubbed, record=record, overwrite=args.overwrite
    )
    print(
        f"{args.org} {args.month}: fetched {len(changes)} in {record['pages']} pages, "
        f"kept {len(kept)}, dropped {before_cutoff} created before {args.cutoff}"
        + (f" and {after_grant} from {scope.created_before}" if scope and after_grant else "")
    )
    print(f"wrote {path}")
    derived = _examples_dir(args) / f"{args.month}.jsonl"
    if derived.is_file():
        print(f"note: {derived} was built from the replaced snapshot; build will redo it")
    return 0


def _github_api(args: argparse.Namespace) -> GitHubAPI:
    """The GitHub client, authenticated from GITHUB_TOKEN, which is never printed or recorded."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit(
            "set GITHUB_TOKEN to collect from GitHub (e.g. GITHUB_TOKEN=$(gh auth token))"
        )
    return GitHubAPI(token, min_interval=args.request_interval)


def _stage_fetch_github(args: argparse.Namespace, salt: str) -> int:
    """One registered GitHub organization-month into the same snapshot shape.

    Reached after the salt and the seal are checked in `_stage_fetch`. A month is the PRs merged
    in it, each keeping its creation time, so window assignment is by creation as on Gerrit.
    """
    refuse_mixed_routes(Path(args.root), args.org, "github", allow=args.allow_mixed_routes)
    # Rows land here as each PR finishes (scrubbed), so a failed month resumes where it stopped.
    checkpoint = DurableLog(args.root, args.org, "raw", f"{args.month}.partial.jsonl")
    rows, record = collect_month(
        args.org, args.month, _github_api(args), salt=salt, checkpoint=checkpoint
    )
    kept = created_on_or_after(rows, args.cutoff)
    record = {
        **record,
        "cutoff": args.cutoff,
        "created_before_cutoff": len(rows) - len(kept),
        "mixed_routes_allowed": bool(args.allow_mixed_routes),
    }
    path = write_snapshot(
        args.root, args.org, args.month, kept, record=record, overwrite=args.overwrite
    )
    checkpoint.unlink(missing_ok=True)
    print(
        f"{args.org} {args.month}: listed {record['listed']}, kept {len(kept)}, "
        f"agent-authored {record['agent_authored']}, withdrawn {record['withdrawn']}"
    )
    print(f"wrote {path}")
    return 0


def _git_pacer(min_interval: float) -> Pacer:
    """A pacer for the git route: a host's floor from `GIT_PERMITTED` never goes away.

    `min_interval` is the run's own `--request-interval`, which can slow a host down further
    but, per `Pacer.interval`, can never go below what the host's entry records -- 0 or a
    negative value included.
    """
    return Pacer(
        min_interval,
        host_intervals={host: p.min_interval for host, p in GIT_PERMITTED.items()},
    )


def _stage_fetch_git(args: argparse.Namespace, salt: str) -> int:
    """One organization-month from NoteDb over git into the same snapshot shape.

    Reached only after the salt and the seal have been checked in `_stage_fetch`, which both
    routes share. A month is the changes NoteDb records as submitted in it; each keeps its
    NoteDb creation time, so window assignment is by creation exactly as for REST.
    """
    if args.org not in GIT_HOSTS:
        raise SystemExit(f"{args.org} has no git host with NoteDb refs; use --via rest")
    if not args.project:
        raise SystemExit("--via git reads one repository per project: pass --project")
    bad = [p for p in args.project if not valid_project_name(p)]
    if bad:
        raise SystemExit(f"refusing --project {bad}: not a plain project path")
    refuse_mixed_routes(Path(args.root), args.org, "notedb", allow=args.allow_mixed_routes)
    # `fetch_month` installs its own SIGTERM handling around the scratch repository it owns
    # (item 4b): every caller gets it, so nothing extra is needed here.
    rows, record = fetch_month(
        args.org,
        args.project,
        args.month,
        salt,
        pacer=_git_pacer(args.request_interval),
        branches=args.branch,
    )
    kept = created_on_or_after(rows, args.cutoff)
    record = {
        **record,
        "cutoff": args.cutoff,
        "created_before_cutoff": len(rows) - len(kept),
        "mixed_routes_allowed": bool(args.allow_mixed_routes),
    }
    path = write_snapshot(
        args.root, args.org, args.month, kept, record=record, overwrite=args.overwrite
    )
    print(
        f"{args.org} {args.month} via git: {len(rows)} submitted, kept {len(kept)}, "
        f"{record['http_requests']} HTTP requests in {record['git_operations']} fetches"
    )
    print(f"wrote {path}")
    return 0


# The test window is fetched no earlier than this many months after its final month, so that
# the confirmatory contrast's censoring is a protocol guarantee rather than an accident of
# when acceptance happened to land. 2026-10 plus three months is 2027-01; MSR 2027 notifies
# on 2027-02-04.
FETCH_HORIZON_MONTHS = 3
# Build's exit when some months were refused: everything else built, those need a refetch.
# Not 2, which uv and argparse already use for their own failures.
BUILD_NEEDS_REFETCH = 3


def _examples_dir(args: argparse.Namespace) -> Path:
    return Path(args.root) / args.org / "examples"


def _refined_dir(args: argparse.Namespace) -> Path:
    return refined_dir(Path(args.root), args.org)


def _load_examples(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Refined examples: dedup, split and freeze never read the built files."""
    return refined_examples(Path(args.root), args.org)


def _stage_refine(args: argparse.Namespace) -> int:
    """Apply the data audit's label rules to every built month, from its raw snapshots."""
    raw = Path(args.root) / args.org / "raw"
    # Refine indexes every month's raw changes, so one unreadable month refuses them all. Every
    # month the quick check refuses is named at once; damage past a header surfaces while reading.
    snapshots = sorted(raw.glob("*.ndjson.gz"))
    refused = [(path.name, reason) for path in snapshots if (reason := refused_snapshot(path))]
    for name, reason in refused:
        print(f"{args.org} {name}: {reason}")
    if refused:
        return 1
    try:
        index = index_changes(change for path in snapshots for change in iter_snapshot(path))
    except ValueError as error:
        print(f"{args.org}: {error}")
        return 1
    built = sorted(_examples_dir(args).glob("*.jsonl"))
    if not built:
        print(f"no examples under {_examples_dir(args)}; run build first")
        return 1
    out = _refined_dir(args)
    out.mkdir(parents=True, exist_ok=True)
    # A refined month whose built month is gone is derived output with nothing to derive from.
    names = {path.name for path in built}
    for orphan in out.glob("*.jsonl"):
        if orphan.name not in names:
            orphan.unlink()
            for sidecar in (".drops.json", ".source.json"):
                out.joinpath(orphan.name.replace(".jsonl", sidecar)).unlink(missing_ok=True)
    totals: Counter[str] = Counter()
    kept_total = 0
    for path in built:
        rows = [json.loads(line) for line in path.read_text().splitlines() if line]
        kept, counts = refine(rows, index)
        totals.update(counts)
        kept_total += len(kept)
        target = out / path.name
        write_json_atomic(out / path.name.replace(".jsonl", ".drops.json"), counts)
        write_atomic(target, "".join(json.dumps(r) + "\n" for r in kept).encode())
        # Written last: a refined month counts as current only once its examples have landed.
        write_source_record(Path(args.root), args.org, path, target)
    print(f"{args.org}: {kept_total} examples kept over {len(built)} months, {dict(totals)}")
    return 0


def drops_path(examples_file: Path) -> Path:
    """Where a month's drop counts live: `2024-10.jsonl` beside `2024-10.drops.json`."""
    return examples_file.with_name(examples_file.name.removesuffix(".jsonl") + ".drops.json")


def source_path(examples_file: Path) -> Path:
    """Where the digest of the snapshot a month was built from lives."""
    return build_record(examples_file)


def snapshot_digest(snapshot: Path) -> str:
    digest = hashlib.sha256()
    digest.update(snapshot.read_bytes())
    return digest.hexdigest()


def built_from_current_snapshot(target: Path, snapshot: Path) -> bool | None:
    """Whether a month's examples came from the snapshot now on disk. None if unrecorded.

    Recorded as a content digest rather than compared by modification time. mtime answers
    a different question: `rsync`, `cp` and a fresh checkout all make a snapshot newer than
    the examples beside it without changing a byte, and each would cost hours of refetching.
    It is also sensitive to which of two writes in the same filesystem tick came first,
    which is a test that passes locally and fails on other hardware.
    """
    record = source_path(target)
    if not record.is_file():
        return None
    try:
        recorded = json.loads(record.read_text())
    except (ValueError, OSError):
        # A truncated or empty record is what a kill or a full disk leaves behind during the
        # final write. Raising here made one bad month abort the build for every month after
        # it, with no message and no way out: --overwrite did not help, because the guard ran
        # before it. An unreadable record means the same as an absent one -- unknown.
        return None
    if not isinstance(recorded, dict):
        return None
    if not recorded.get("complete", True):
        # The marker was opened and never closed, so the examples beside it are a partial
        # write. That is known-bad rather than unknown: rebuild. Records backfilled before
        # the marker existed carry no `complete` key and default to closed.
        return False
    return recorded.get("snapshot_sha256") == snapshot_digest(snapshot)


def built_read_cap(target: Path) -> int | None:
    """The per-month read cap a month's build record names, None for an uncapped build."""
    try:
        cap = json.loads(source_path(target).read_text()).get("read_cap")
    except (ValueError, OSError, AttributeError):
        return None
    return cap if isinstance(cap, int) else None


#: Fixed before any capped month was read (research log, 2026-10-16), so the draw reproduces.
READ_CAP_SEED = 20261016


def capped_numbers(
    changes: list[dict[str, Any]], cap: int | None, *, skip_uncommented: bool
) -> set[int] | None:
    """The change numbers a capped build reads: `cap` of the changes it would otherwise read, in a
    seeded hash order of their numbers. Not the lowest numbers: a month lists the changes last
    updated in it, so its lowest numbers are the ones longest open."""
    if cap is None:
        return None
    numbers = {int(c["_number"]) for c in changes if not (skip_uncommented and uncommented(c))}

    def rank(n: int) -> str:
        return hashlib.sha256(f"{READ_CAP_SEED}:{n}".encode()).hexdigest()

    return set(sorted(numbers, key=rank)[:cap])


def built_with_fewer_requests(target: Path) -> bool:
    """Whether a month's build record says it was built with `--fewer-requests`."""
    try:
        return bool(json.loads(source_path(target).read_text()).get("fewer_requests"))
    except (ValueError, OSError, AttributeError):
        return False


def built_under_current_rules(target: Path) -> bool | None:
    """Whether a month was built under the build rules now in the code; None if unrecorded."""
    try:
        recorded = json.loads(source_path(target).read_text()).get("build_rules")
    except (ValueError, OSError, AttributeError):
        return None
    return None if recorded is None else recorded == BUILD_RULES


# The months accepted without a rebuild, by snapshot digest, and the build rules the acceptance
# holds for. The research log entry of 2026-09-23 holds the audit behind it.
STAMP_ALLOWLIST = Path(__file__).resolve().parent / "stamped-months.json"
STAMP_NOTE = (
    "built before build rules were recorded; accepted without a rebuild on 2026-09-23 after every "
    "build-code change since was found reapplied by refine or not to change output"
)


def _stamp_allowlist() -> dict[str, Any]:
    return json.loads(STAMP_ALLOWLIST.read_text())


def _stage_stamp(args: argparse.Namespace) -> int:
    """Record the build rules on the months the 2026-09-23 audit accepted without a rebuild.

    Those months were built before the build recorded its rules and judged equivalent to a build
    under the rules `stamped-months.json` names. A month is stamped only while the code's build
    rules are still those, its snapshot is one the audit covered and is still the one on disk,
    and its record carries no build rules; anything else must be rebuilt. Every month is checked
    before any is written.
    """
    allowed = _stamp_allowlist()
    if allowed["build_rules"] != BUILD_RULES:
        print(
            f"refusing to stamp: the build rules are {BUILD_RULES}, and the audit covered "
            f"{allowed['build_rules']}; rebuild instead"
        )
        return 1
    built = sorted(_examples_dir(args).glob("*.jsonl"))
    if not built:
        print(f"no examples under {_examples_dir(args)}; run build first")
        return 1
    raw = Path(args.root) / args.org / "raw"
    plans: list[tuple[Path, dict[str, Any]]] = []
    refused = []
    for path in built:
        month = path.name.removesuffix(".jsonl")
        recorded = read_json_object(source_path(path))
        snapshot = raw / f"{month}.ndjson.gz"
        digest = recorded.get("snapshot_sha256") if recorded else None
        if recorded is None:
            refused.append(f"{path.name}: no readable build record")
        elif recorded.get("build_rules") == BUILD_RULES:
            continue
        elif recorded.get("build_rules") is not None:
            refused.append(f"{path.name}: built under other build rules; rebuild it")
        elif not recorded.get("complete", True):
            refused.append(f"{path.name}: build did not complete")
        elif not digest or not snapshot.is_file() or digest != snapshot_digest(snapshot):
            refused.append(f"{path.name}: its snapshot is missing or was refetched since")
        elif allowed["snapshots"].get(digest) != f"{args.org}/{month}":
            refused.append(f"{path.name}: not a month the audit covered")
        else:
            try:
                rows = [json.loads(line) for line in path.read_text().splitlines() if line]
            except ValueError:
                refused.append(f"{path.name}: unreadable examples")
                continue
            stamp = {**recorded, "build_rules": BUILD_RULES, "stamped": STAMP_NOTE}
            # Built before prompt context existed: the same examples, without context, which the
            # contamination battery refuses; counted so the record says so.
            without = sum("context_before" not in row for row in rows)
            if without:
                stamp["without_context"] = without
            plans.append((source_path(path), stamp))
    if refused:
        print(f"refusing to stamp {args.org}: {refused}")
        return 1
    for record, stamp in plans:
        write_json_atomic(record, stamp)
    print(f"{args.org}: stamped {len(plans)} of {len(built)} months with build rules {BUILD_RULES}")
    return 0


def _load_drops(args: argparse.Namespace) -> dict[str, int]:
    """Build-stage drop counts summed over every month on disk."""
    total: Counter[str] = Counter()
    for path in sorted(_examples_dir(args).glob("*.drops.json")):
        total.update(json.loads(path.read_text()))
    return dict(sorted(total.items()))


#: The drop a `--fewer-requests` build counts for a change its snapshot records no comment on.
UNCOMMENTED = "uncommented_by_count"
#: The drop a `--read-cap` build counts for a commented change past the month's cap.
OVER_READ_CAP = "over_read_cap"


def uncommented(change: Mapping[str, Any]) -> bool:
    """Whether the change's snapshot records no inline comment on any patch set.

    Gerrit's `total_comment_count` counts every inline comment across a change's patch sets, as
    of the fetch; a change without the field (an index that lacks the data) is not uncommented,
    and neither is a NoteDb or GitHub row. Like `comment_error`, the drop it counts is a change.
    """
    if NOTEDB_KEY in change or GITHUB_KEY in change:
        return False  # those rows carry their comments; skipping them saves no request
    return change.get("total_comment_count") == 0


def cached_diffs(diffs: DiffFetcher) -> DiffFetcher:
    """One change's diff fetcher, asking once for each file at each patch-set pair.

    Every comment on one file at one patch set asks for the same diff. A request that fails is
    not remembered, so the next comment asks again, as without the cache.
    """
    answered: dict[tuple[int, int, str, int], Mapping[str, Any]] = {}

    def diff(number: int, patch_set: int, path: str, base: int) -> Mapping[str, Any]:
        key = (number, patch_set, path, base)
        if key not in answered:
            answered[key] = diffs(number, patch_set, path, base)
        return answered[key]

    return diff


#: The 95% upper bound on the share of skipped rows that carry comments a probe must reach: about
#: 300 empty answers (research log, 2026-10-15).
COUNT_PROBE_BOUND = 0.01


def require_count_probe(args: argparse.Namespace) -> None:
    """Refuse `--fewer-requests` unless the organization's comment-count probe holds, bounds the
    skipped share's comments tightly enough, and asked the raw snapshots this build reads."""
    if args.count_probe is not None and not args.fewer_requests:
        raise SystemExit("--count-probe is read only with --fewer-requests")
    if args.read_cap is not None and args.read_cap < 1:
        raise SystemExit("--read-cap is a positive count")
    if not args.fewer_requests:
        return
    if args.count_probe is None or not args.count_probe.is_file():
        raise SystemExit("--fewer-requests needs --count-probe: a comment_count_probe.py result")
    try:
        probe = json.loads(args.count_probe.read_text())
    except (ValueError, OSError) as error:
        raise SystemExit(f"{args.count_probe}: {error}") from error
    if (
        not isinstance(probe, dict)
        or probe.get("org") != args.org
        or probe.get("holds") is not True
    ):
        raise SystemExit(f"{args.count_probe} does not hold for {args.org}")
    bound = probe.get("upper_95_share_with_comments")
    if not isinstance(bound, float) or bound > COUNT_PROBE_BOUND:
        raise SystemExit(
            f"{args.count_probe} bounds the skipped share at {bound}, not {COUNT_PROBE_BOUND}"
        )
    if probe.get("raw_digest") != raw_digest(Path(args.root), args.org):
        raise SystemExit(f"{args.count_probe} asked other raw snapshots than {args.root}'s")


def require_grant_cap(args: argparse.Namespace) -> None:
    """Refuse REST reads of a scoped host past its grant's monthly cap."""
    scope = grant_scope(args.org)
    if (
        scope is not None
        and scope.read_cap is not None
        and (args.read_cap is None or args.read_cap > scope.read_cap)
    ):
        raise SystemExit(
            f"{args.org}'s grant reads at most {scope.read_cap} changes a month: "
            f"pass --read-cap {scope.read_cap} or less"
        )


def _stage_build(args: argparse.Namespace) -> int:
    """Every snapshot for an organization into examples, one file per month."""
    require_count_probe(args)
    scope = grant_scope(args.org)
    salt = require_salt()
    raw = Path(args.root) / args.org / "raw"
    snapshots = sorted(raw.glob("*.ndjson.gz"))
    if not snapshots:
        print(f"no snapshots under {raw}; run fetch first")
        return 1
    rest: tuple[CommentFetcher, DiffFetcher] | None = None
    # The changes a scoped host's transport may ask about, admitted month by month below.
    admitted: Admitted = {}

    def fetchers_for(change: dict[str, Any]) -> tuple[CommentFetcher, DiffFetcher]:
        """A NoteDb or GitHub row answers from what it carries; a REST row fetches."""
        nonlocal rest
        if NOTEDB_KEY in change:
            return embedded_fetchers(change)
        if GITHUB_KEY in change:
            return github_fetchers(change)
        if rest is None:
            if args.org not in GERRIT:
                raise SystemExit(f"{args.org} has no REST host, and a row carries no NoteDb data")
            require_grant_cap(args)
            transport = http_transport(min_interval=args.request_interval, admitted=admitted)
            rest = (
                scrubbed_comment_fetcher(GERRIT[args.org], salt, transport=transport),
                scrubbed_diff_fetcher(GERRIT[args.org], transport=transport),
            )
        if args.fewer_requests:
            return rest[0], cached_diffs(rest[1])
        return rest

    out_dir = _examples_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    total, drops, refused, current = 0, Counter(), [], set()
    for snapshot in snapshots:
        month = snapshot.name.removesuffix(".ndjson.gz")
        target = out_dir / f"{month}.jsonl"
        reason = refused_snapshot(snapshot)
        if reason:
            # Skipped, not raised: one month whose fetch stopped must not cost every month after.
            print(f"{args.org} {month}: {reason}")
            refused.append(month)
            continue
        # A month refetched after it was built leaves examples derived from a snapshot that
        # no longer exists, and resume cannot tell those from finished work: it skips them,
        # and the corpus quietly mixes months built under different fetch parameters. Seen
        # on the control window, where 2024-01 had been collected as its own window and was
        # being recollected under a wider cutoff.
        matches = built_from_current_snapshot(target, snapshot) if target.exists() else None
        if matches is False:
            print(f"{args.org} {month}: built from a different snapshot, rebuilding")
        elif matches is True and built_under_current_rules(target) is False:
            # Refining it again would stamp the old build's output as current.
            print(f"{args.org} {month}: built under other build rules, rebuilding")
            matches = False
        elif matches is True and built_with_fewer_requests(target) != args.fewer_requests:
            print(f"{args.org} {month}: built in the other request mode, rebuilding")
            matches = False
        elif matches is True and built_read_cap(target) != args.read_cap:
            print(f"{args.org} {month}: built under another read cap, rebuilding")
            matches = False
        elif matches is True and built_under_current_rules(target) is None:
            print(
                f"{args.org} {month}: no build rules recorded; the loader refuses it until it "
                "is stamped or rebuilt"
            )
        if matches is None and target.exists() and not args.overwrite:
            print(f"{args.org} {month}: no snapshot digest recorded, cannot check staleness")
        if target.exists() and not args.overwrite and matches is not False:
            # Resume. A month costs minutes of network time, and Qt needs roughly 400
            # requests per month, so discarding completed work on interruption is not
            # affordable.
            # Counted from disk, not skipped: the summary is the corpus, and a resumed build
            # that reported only its own months under-stated it by exactly the months it had
            # already done. That figure is what the sampling section reports.
            skipped = sum(1 for line in target.read_text().splitlines() if line)
            total += skipped
            drops.update(
                json.loads(drops_path(target).read_text()) if drops_path(target).is_file() else {}
            )
            print(f"{args.org} {month}: skip, already built ({skipped} examples)")
            current.add(month)
            continue
        try:
            changes = read_snapshot(snapshot)
        except ValueError as error:
            # A snapshot whose header reads but whose body does not: zeros past the header.
            print(f"{args.org} {month}: {error}")
            refused.append(month)
            continue
        asked = rest_rows(changes)
        why = outside_grant(scope, month, asked)
        if why:
            print(f"{args.org} {month}: {why}; not built")
            refused.append(month)
            continue
        if args.org in GERRIT:
            admit(admitted, host_of(args.org), asked)
        rows: list[dict[str, Any]] = []
        month_drops: Counter[str] = Counter()
        if args.fewer_requests:
            # Every reason present, as a built change leaves them, and this one even at zero, so
            # the month's drops say how it was built whatever was skipped.
            month_drops.update(dict.fromkeys((*DROP_REASONS, UNCOMMENTED), 0))
        if args.read_cap is not None:
            month_drops[OVER_READ_CAP] = 0
        reads = capped_numbers(changes, args.read_cap, skip_uncommented=args.fewer_requests)
        for change in changes:
            if args.fewer_requests and uncommented(change):
                month_drops[UNCOMMENTED] += 1
                continue
            if reads is not None and int(change["_number"]) not in reads:
                month_drops[OVER_READ_CAP] += 1
                continue
            built, dropped = build_from_change(args.org, change, *fetchers_for(change))
            rows.extend(built)
            month_drops.update(dropped)
        # Drop counts beside the examples, written first so a month with examples always has
        # them. They were printed and lost: the files on disk are post-filter, so the
        # discard rate by reason could not be reconstructed without refetching every diff,
        # and the Stage 1 report states those rates.
        write_json_atomic(drops_path(target), dict(sorted(month_drops.items())))
        # The record is the completion marker, so it is written open before the examples and
        # closed after them, and the examples land by rename. `write_text` is not atomic: an
        # interrupted rebuild otherwise leaves a truncated month beside a digest that still
        # matches, and the next run certifies it as finished and counts its truncated tail as
        # an example.
        digest = snapshot_digest(snapshot)
        write_build_record(
            target,
            digest,
            complete=False,
            fewer_requests=args.fewer_requests,
            read_cap=args.read_cap,
        )
        write_atomic(target, "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode())
        write_build_record(
            target,
            digest,
            complete=True,
            fewer_requests=args.fewer_requests,
            read_cap=args.read_cap,
        )
        drops.update(month_drops)
        current.add(month)
        print(f"{args.org} {month}: {len(rows)} examples, drops {dict(month_drops)}")
        total += len(rows)
    months = len(snapshots) - len(refused)
    print(f"{args.org}: {total} examples over {months} months, drops {dict(drops)}")
    if refused:
        print(f"{args.org}: {len(refused)} month(s) not built; fetch them again, then build")
    if args.expect_month:
        # Build walks the whole org, so a caller after one month needs that month's verdict,
        # judged by what this loop did with it rather than by the files it left.
        expected = args.expect_month
        if expected in current:
            return 0
        if expected not in refused:
            print(f"{args.org} {expected}: no snapshot; fetch it")
        return BUILD_NEEDS_REFETCH
    # Not 1: the months that could be built were, and building again will not help the rest.
    return BUILD_NEEDS_REFETCH if refused else 0


def _stage_dedup(args: argparse.Namespace) -> int:
    """Report what dedup would remove. Deliberately writes nothing."""
    examples = _load_examples(args)
    if not examples:
        print(f"no examples under {_examples_dir(args)}; run build first")
        return 1
    kept, removed = run_dedup(examples)
    print(f"{args.org}: {len(examples)} in, {len(kept)} kept, removed {dict(removed)}")
    return 0


def _stage_split(args: argparse.Namespace) -> int:
    """Report the window assignment. Deliberately writes nothing."""
    examples = _load_examples(args)
    if not examples:
        print(f"no examples under {_examples_dir(args)}; run build first")
        return 1
    kept, _ = run_dedup(examples)
    windows, straddling, unassigned = run_split(kept, WINDOWS)
    for name, rows in windows.items():
        print(f"  {name:<6} {len(rows)}")
    if unassigned:
        print(f"UNASSIGNED {len(unassigned)} change(s) match no window: {unassigned[:5]}")
    if straddling:
        print(f"STRADDLING {straddling}")
    return 1 if (straddling or unassigned) else 0


def _stage_freeze(args: argparse.Namespace) -> int:
    """The only stage that commits windows to disk."""
    examples = _load_examples(args)
    if not examples:
        print(f"no examples under {_examples_dir(args)}; run build first")
        return 1
    kept, removed = run_dedup(examples)
    windows, straddling, unassigned = run_split(kept, WINDOWS)
    if straddling:
        print(f"refusing to freeze: changes straddle a window boundary: {straddling}")
        return 1
    if unassigned:
        # Freezing here would record counts for a corpus quietly missing these changes,
        # and the manifest would verify cleanly forever after.
        print(
            f"refusing to freeze: {len(unassigned)} change(s) match no window "
            f"(first: {unassigned[:5]}). Widen WINDOWS or drop the out-of-range months."
        )
        return 1
    manifest = freeze_windows(
        Path(args.root),
        args.org,
        windows,
        stats={
            "built_drops": _load_drops(args),
            "refine_counts": _load_refine_counts(args),
            "deduped": dict(removed),
            "unassigned_changes": 0,
            # The rules the corpus was built and refined under; verify fails when either moves.
            "build_rules": BUILD_RULES,
            "rules": RULES_VERSION,
        },
    )
    print(f"{args.org}: froze {manifest['counts']}")
    return 0


def _load_refine_counts(args: argparse.Namespace) -> dict[str, int]:
    total: Counter[str] = Counter()
    for path in sorted(_refined_dir(args).glob("*.drops.json")):
        total.update(json.loads(path.read_text()))
    return dict(total)


def _stage_verify(args: argparse.Namespace) -> int:
    """Re-derive every window's hash from disk and fail on any drift."""
    manifest_path = Path(args.root) / args.org / "manifest.json"
    if not manifest_path.is_file():
        print(f"no manifest at {manifest_path}; freeze the corpus first")
        return 1
    manifest = json.loads(manifest_path.read_text())
    splits = manifest_path.parent / "splits"
    windows = {
        name: [
            json.loads(line)["id"]
            for line in (splits / f"{name}.jsonl").read_text().splitlines()
            if line
        ]
        for name in manifest["counts"]
    }
    problems = verify(manifest, windows)
    stats = manifest.get("stats", {})
    for key, current in (("build_rules", BUILD_RULES), ("rules", RULES_VERSION)):
        if stats.get(key) != current:
            problems.append(
                f"frozen under {key} {stats.get(key)}, current are {current}: rebuild or refine "
                "and refreeze, or check out the code the manifest was frozen with"
            )
    if args.reproduce:
        problems.extend(f"reproduce: {p}" for p in _reproduce(args, manifest))
    if problems:
        for problem in problems:
            print(f"DRIFT {problem}")
        return 1
    total = sum(manifest["counts"].values())
    reproduced = ", reproduced from the refined examples" if args.reproduce else ""
    print(
        f"{args.org}: clean{reproduced}, {total} examples across {len(manifest['counts'])} windows"
    )
    return 0


def _reproduce(args: argparse.Namespace, manifest: Mapping[str, Any]) -> list[str]:
    """Rerun dedup and split in memory; what the frozen windows no longer match.

    The rule digests cover build and refine. Dedup and split are covered only here, so a change
    to either leaves the frozen files intact and `verify` clean without this check.
    """
    kept, removed = run_dedup(_load_examples(args))
    windows, straddling, unassigned = run_split(kept, WINDOWS)
    splits = Path(args.root) / args.org / "splits"
    problems = []
    # Byte for byte, over every window either side names: ids alone pass a dedup that keeps a
    # different copy of an id, and the rerun's names alone pass a window dropped from WINDOWS.
    for name in sorted(set(manifest["counts"]) | set(windows)):
        frozen = splits / f"{name}.jsonl"
        if name not in windows:
            problems.append(f"{name}: frozen, but the current bounds yield no such window")
        elif not frozen.is_file():
            problems.append(f"{name}: the current bounds yield it, but nothing was frozen")
        elif window_body(windows[name]) != frozen.read_text():
            problems.append(f"{name}: the rerun differs from the frozen split file")
    recorded = manifest.get("stats", {}).get("deduped")
    if recorded is None:
        problems.append("the manifest records no dedup counts to compare against")
    elif dict(removed) != recorded:
        problems.append(f"deduped {dict(removed)}, manifest records {recorded}")
    if straddling or unassigned:
        problems.append(f"{len(straddling)} straddling and {len(unassigned)} unassigned change(s)")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.stage == "fetch":
        return _stage_fetch(args)
    stages = {
        "fetch": _stage_fetch,
        "build": _stage_build,
        "stamp": _stage_stamp,
        "refine": _stage_refine,
        "dedup": _stage_dedup,
        "split": _stage_split,
        "freeze": _stage_freeze,
        "verify": _stage_verify,
    }
    return stages[args.stage](args)


if __name__ == "__main__":  # pragma: no cover - exercised by the module-entry test
    # Without this, `python -m sphragis.corpus.cli fetch ...` imports the module, runs
    # nothing and exits 0, which reads as a fetch that succeeded and wrote no snapshot.
    raise SystemExit(main())
