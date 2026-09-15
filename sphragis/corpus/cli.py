"""Corpus pipeline entry point: the one place that touches disk or the network."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sphragis.corpus.build import build_from_change
from sphragis.corpus.fetchers import scrubbed_comment_fetcher, scrubbed_diff_fetcher
from sphragis.corpus.gerrit import Transport, created_on_or_after, fetch_changes
from sphragis.corpus.manifest import verify
from sphragis.corpus.pipeline import freeze_windows, run_dedup, run_split
from sphragis.corpus.scrub import scrub
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
    return parser


def http_transport() -> Transport:
    """A real HTTP transport. Separated so every test runs offline."""

    def transport(url: str) -> tuple[int, dict[str, str], str]:
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return response.status, dict(response.headers), response.read().decode()
        except urllib.error.HTTPError as error:
            # urlopen raises on 4xx/5xx. Returning the status instead is what lets
            # gerrit._get apply its retry budget and honour Retry-After; raising here
            # would make a retryable 500 fatal.
            return error.code, dict(error.headers or {}), ""
        except (urllib.error.URLError, TimeoutError, OSError):
            # A DNS blip or a dropped connection is exactly as transient as a 503 and
            # was not: it escaped the retry budget entirely and killed a 3,336-change
            # build five minutes in, with no resumption. HTTPError is a URLError
            # subclass, so this clause must stay second.
            return 503, {}, ""

    return transport


def _stage_fetch(args: argparse.Namespace) -> int:
    """One organization-month into an immutable snapshot, scrubbed on the way in."""
    salt = require_salt()
    year, month = args.month.split("-")
    following = (
        f"{int(year) + (month == '12')}-{'01' if month == '12' else f'{int(month) + 1:02d}'}"
    )
    query = f"status:merged after:{args.month}-01 before:{following}-01"
    changes, record = fetch_changes(
        GERRIT[args.org],
        query,
        transport=http_transport(),
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
    return 0


# Window bounds are a study parameter, not a runtime flag: they are fixed in the Stage 1
# report and changing them after the fact would move the confirmatory set.
WINDOWS = {
    "pilot": ("2024-10-01", "2024-11-01"),
    "train": ("2024-11-01", "2025-09-01"),
    "dev": ("2025-09-01", "2025-11-01"),
    "test": ("2025-11-01", "2026-09-01"),
}


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
    transport = http_transport()
    comments = scrubbed_comment_fetcher(GERRIT[args.org], salt, transport=transport)
    diffs = scrubbed_diff_fetcher(GERRIT[args.org], transport=transport)
    out_dir = _examples_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    total, drops = 0, Counter()
    for snapshot in snapshots:
        month = snapshot.name.removesuffix(".ndjson.gz")
        target = out_dir / f"{month}.jsonl"
        if target.exists() and not args.overwrite:
            # Resume. A month costs minutes of network time, and Qt needs roughly 400
            # requests per month, so discarding completed work on interruption is not
            # affordable.
            print(f"{args.org} {month}: skip, already built")
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
        target.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
        drops.update(month_drops)
        print(f"{args.org} {month}: {len(rows)} examples, drops {dict(month_drops)}")
        total += len(rows)
    print(f"{args.org}: {total} examples, drops {dict(drops)}")
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
