"""Corpus pipeline entry point: the one place that touches disk or the network."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from sphragis.corpus.build import build_from_change
from sphragis.corpus.fetchers import scrubbed_comment_fetcher, scrubbed_diff_fetcher
from sphragis.corpus.gerrit import Transport, created_on_or_after, fetch_changes
from sphragis.corpus.manifest import verify
from sphragis.corpus.pipeline import freeze_windows, run_dedup, run_split
from sphragis.corpus.scrub import scrub
from sphragis.corpus.split import is_test_window_unlocked
from sphragis.corpus.storage import read_snapshot, write_snapshot

STAGES = ("fetch", "build", "dedup", "split", "freeze", "verify")

# One Gerrit instance is one organization. Both checked live 2026-09-14.
GERRIT = {
    "openstack": "https://review.opendev.org",
    "qt": "https://codereview.qt-project.org",
}

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
            '`python -c "import secrets; print(secrets.token_hex(32))"`, put it in .env, '
            "and keep it out of git: it is what makes the pseudonyms irreversible."
        )
    return salt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sphragis.corpus")
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--org", default="openstack", choices=sorted(GERRIT))
    parser.add_argument("--month", default="2024-10", help="YYYY-MM, for fetch")
    parser.add_argument("--root", type=Path, default=Path("datasets/gerrit"))
    parser.add_argument("--cutoff", default="2024-10-01", help="drop changes created before")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing snapshot")
    parser.add_argument(
        "--request-interval",
        type=float,
        default=1.0,
        help="minimum seconds between requests to one Gerrit host (fetch, build)",
    )
    return parser


def http_transport(
    timeout: float = 15.0,
    *,
    min_interval: float = 0.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Transport:
    """A real HTTP transport over one persistent connection per host.

    Separated so every test runs offline. The earlier transport opened a fresh TLS
    connection for every request under a single 60-second timeout. Against review.opendev.org
    about 1 in 40 fresh handshakes hangs (measured 2026-09-15: 39 of 40 answered in under
    0.2 s, one never did), so a month's build of thousands of requests spent most of its time
    waiting out dropped connections: a build ran 16 minutes on 1 second of CPU. Reusing the
    connection makes handshakes rare, which is also gentler on a community server, and the
    shorter timeout makes a dropped one cost seconds rather than a minute.

    Statuses are returned, never raised, so gerrit._get applies its retry budget and honours
    Retry-After. A connection failure is reported as 503, which is retryable.
    """
    connections: dict[str, http.client.HTTPConnection] = {}
    last_start: dict[str, float] = {}

    def pace(host: str) -> None:
        """Hold requests to one host at least `min_interval` apart.

        Measured 2026-09-15 against review.opendev.org: after hours of unpaced building
        (about 20 requests a second) a quarter to a third of new handshakes were dropped;
        after 20 quiet minutes, 0 of 30. The drops follow our volume, so the fix is to send
        less, not to retry harder.

        The interval was 0.2 s, and that only postponed it: both review.opendev.org and
        codereview.qt-project.org went on to refuse handshakes after about three hours at
        5 requests a second, and Qt's refusal outlasted 25 minutes of silence, which reads
        as a firewall ban rather than load shedding. Building a full corpus is roughly
        70,000 requests against a volunteer-run server, so the default is 1 request a second.
        A frozen corpus is fetched once and reused; an overnight collection costs nothing a
        second run would not cost more.
        """
        if min_interval <= 0:
            return
        previous = last_start.get(host)
        if previous is not None:
            wait = previous + min_interval - clock()
            if wait > 0:
                sleep(wait)
        last_start[host] = clock()

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
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        pace(parts.netloc)
        # One silent retry only for a keep-alive the server closed while idle, which is
        # routine and not a failure; anything else is reported and left to the retry budget.
        for attempt in range(2):
            connection = connect(parts.scheme, parts.netloc)
            try:
                connection.request("GET", target, headers={"Accept": "application/json"})
                response = connection.getresponse()
                body = response.read().decode()
                return response.status, dict(response.getheaders()), body
            except (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError):
                drop(parts.netloc)
                if attempt == 0:
                    continue
                return 503, {}, ""
            except (OSError, http.client.HTTPException):
                drop(parts.netloc)
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
    seal_path = root / org / "seal.json"
    record = json.loads(seal_path.read_text()) if seal_path.is_file() else {}
    if not is_test_window_unlocked(record):
        raise SystemExit(
            f"refusing to fetch {org} {month}: on or after the sealed test window's start "
            f"({WINDOWS['test'][0]}), and {seal_path} records no in-principle acceptance"
        )


def _stage_fetch(args: argparse.Namespace) -> int:
    """One organization-month into an immutable snapshot, scrubbed on the way in."""
    salt = require_salt()
    refuse_if_sealed(Path(args.root), args.org, args.month)
    year, month = args.month.split("-")
    following = (
        f"{int(year) + (month == '12')}-{'01' if month == '12' else f'{int(month) + 1:02d}'}"
    )
    query = f"status:merged after:{args.month}-01 before:{following}-01"
    changes, record = fetch_changes(
        GERRIT[args.org],
        query,
        transport=http_transport(min_interval=args.request_interval),
        options=("ALL_REVISIONS",),
    )
    kept = created_on_or_after(changes, args.cutoff)
    scrubbed = [scrub(change, salt) for change in kept]
    path = write_snapshot(
        args.root, args.org, args.month, scrubbed, record=record, overwrite=args.overwrite
    )
    dropped = len(changes) - len(kept)
    print(
        f"{args.org} {args.month}: fetched {len(changes)} in {record['pages']} pages, "
        f"kept {len(kept)}, dropped {dropped} created before {args.cutoff}"
    )
    print(f"wrote {path}")
    derived = _examples_dir(args) / f"{args.month}.jsonl"
    if derived.is_file():
        print(f"note: {derived} was built from the replaced snapshot; build will redo it")
    return 0


# Window bounds are a study parameter, not a runtime flag: they are fixed in the Stage 1
# report and changing them after the fact would move the confirmatory set.
#
# The test window runs twelve months rather than ten. The gate is conjunctive, so its power
# is the probability BOTH organizations clear zero, and at ten months that is 0.757 with Qt
# binding at 0.763. Twelve months takes the gate to 0.833 for 0.4 points of additional
# differential censoring, still a twelfth of what the dev window carries. It is set now,
# before any test data exists to be seen: 2026-10 closes before the Stage 1 submission on
# 2026-11-20, and the window is still fetched only after in-principle acceptance.
WINDOWS = {
    "pilot": ("2024-10-01", "2024-11-01"),
    "train": ("2024-11-01", "2025-09-01"),
    "dev": ("2025-09-01", "2025-11-01"),
    "test": ("2025-11-01", "2026-11-01"),
}

# The test window is fetched no earlier than this many months after its final month, so that
# the confirmatory contrast's censoring is a protocol guarantee rather than an accident of
# when acceptance happened to land. 2026-10 plus three months is 2027-01; MSR 2027 notifies
# on 2027-02-04.
FETCH_HORIZON_MONTHS = 3


def _examples_dir(args: argparse.Namespace) -> Path:
    return Path(args.root) / args.org / "examples"


def _load_examples(args: argparse.Namespace) -> list[dict[str, Any]]:
    directory = _examples_dir(args)
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.jsonl")):
        rows.extend(json.loads(line) for line in path.read_text().splitlines() if line)
    return rows


def drops_path(examples_file: Path) -> Path:
    """Where a month's drop counts live: `2024-10.jsonl` beside `2024-10.drops.json`."""
    return examples_file.with_name(examples_file.name.removesuffix(".jsonl") + ".drops.json")


def source_path(examples_file: Path) -> Path:
    """Where the digest of the snapshot a month was built from lives."""
    return examples_file.with_name(examples_file.name.removesuffix(".jsonl") + ".source.json")


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


def _load_drops(args: argparse.Namespace) -> dict[str, int]:
    """Build-stage drop counts summed over every month on disk."""
    total: Counter[str] = Counter()
    for path in sorted(_examples_dir(args).glob("*.drops.json")):
        total.update(json.loads(path.read_text()))
    return dict(sorted(total.items()))


def _stage_build(args: argparse.Namespace) -> int:
    """Every snapshot for an organization into examples, one file per month."""
    salt = require_salt()
    raw = Path(args.root) / args.org / "raw"
    snapshots = sorted(raw.glob("*.ndjson.gz"))
    if not snapshots:
        print(f"no snapshots under {raw}; run fetch first")
        return 1
    transport = http_transport(min_interval=args.request_interval)
    comments = scrubbed_comment_fetcher(GERRIT[args.org], salt, transport=transport)
    diffs = scrubbed_diff_fetcher(GERRIT[args.org], transport=transport)
    out_dir = _examples_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    total, drops = 0, Counter()
    for snapshot in snapshots:
        month = snapshot.name.removesuffix(".ndjson.gz")
        target = out_dir / f"{month}.jsonl"
        # A month refetched after it was built leaves examples derived from a snapshot that
        # no longer exists, and resume cannot tell those from finished work: it skips them,
        # and the corpus quietly mixes months built under different fetch parameters. Seen
        # on the control window, where 2024-01 had been collected as its own window and was
        # being recollected under a wider cutoff.
        matches = built_from_current_snapshot(target, snapshot) if target.exists() else None
        if matches is False:
            print(f"{args.org} {month}: built from a different snapshot, rebuilding")
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
            continue
        rows: list[dict[str, Any]] = []
        month_drops: Counter[str] = Counter()
        for change in read_snapshot(snapshot):
            built, dropped = build_from_change(args.org, change, comments, diffs)
            rows.extend(built)
            month_drops.update(dropped)
        # Drop counts beside the examples, written first so a month with examples always has
        # them. They were printed and lost: the files on disk are post-filter, so the
        # discard rate by reason could not be reconstructed without refetching every diff,
        # and the Stage 1 report states those rates.
        drops_path(target).write_text(
            json.dumps(dict(sorted(month_drops.items())), indent=2) + "\n"
        )
        # The record is the completion marker, so it is written open before the examples and
        # closed after them, and the examples land by rename. `write_text` is not atomic: an
        # interrupted rebuild otherwise leaves a truncated month beside a digest that still
        # matches, and the next run certifies it as finished and counts its truncated tail as
        # an example.
        digest = snapshot_digest(snapshot)
        record = source_path(target)
        record.write_text(json.dumps({"snapshot_sha256": digest, "complete": False}) + "\n")
        staging = target.with_suffix(".jsonl.partial")
        staging.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
        staging.replace(target)
        record.write_text(
            json.dumps({"snapshot_sha256": digest, "complete": True}, indent=2) + "\n"
        )
        drops.update(month_drops)
        print(f"{args.org} {month}: {len(rows)} examples, drops {dict(month_drops)}")
        total += len(rows)
    months = len(snapshots)
    print(f"{args.org}: {total} examples over {months} months, drops {dict(drops)}")
    return 0


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
        stats={"built_drops": _load_drops(args), "deduped": dict(removed), "unassigned_changes": 0},
    )
    print(f"{args.org}: froze {manifest['counts']}")
    return 0


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
    if problems:
        for problem in problems:
            print(f"DRIFT {problem}")
        return 1
    total = sum(manifest["counts"].values())
    print(f"{args.org}: clean, {total} examples across {len(manifest['counts'])} windows")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.stage == "fetch":
        return _stage_fetch(args)
    stages = {
        "fetch": _stage_fetch,
        "build": _stage_build,
        "dedup": _stage_dedup,
        "split": _stage_split,
        "freeze": _stage_freeze,
        "verify": _stage_verify,
    }
    return stages[args.stage](args)
