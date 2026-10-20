"""Publish results without their model text: each withheld field's texts become keyed digests.

Run on a results file before it enters `datasets/results/`; the full-text original stays in the
cluster's results root. `tests/unit/test_published_review_text.py` refuses a tracked result that
still carries such text. The key is `SPHRAGIS_PREDICTION_KEY`, from `.env`.

    uv run --no-sync --no-active python scripts/withhold_text.py --out-dir datasets/results \\
        ~/ajsoftworks/sphragis-data/results/rq1-partition-openstack-p3-s1-n1850-rp1.0.json
    uv run --no-sync --no-active python scripts/withhold_text.py --in-place \\
        --summary datasets/results/withheld-text.json $(git ls-files 'datasets/results/*.json')
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sphragis.measure.withhold import (
    PLAIN_HASHES_BEFORE_THE_RULE,
    key_id,
    load_key,
    pinned,
    refusals,
    withhold,
)
from sphragis.provenance import provenance_header

ROOT = Path(__file__).resolve().parents[1]

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("paths", nargs="+", type=Path)
where = parser.add_mutually_exclusive_group(required=True)
where.add_argument("--out-dir", type=Path, help="write each published copy here, by name")
where.add_argument("--in-place", action="store_true", help="rewrite tracked copies in place")
parser.add_argument("--summary", type=Path, help="record which files and fields were withheld")


def published(raw: str, document: object) -> str:
    """The document in the results files' own layout, a trailing newline kept if there was one."""
    text = json.dumps(document, indent=2)
    return text + "\n" if raw.endswith("\n") else text


def _in_repo(path: Path) -> str | None:
    resolved = path.resolve()
    return resolved.relative_to(ROOT).as_posix() if resolved.is_relative_to(ROOT) else None


def convert(path: Path, dest: Path, key: bytes) -> tuple[bytes, int, Any]:
    """The bytes to publish for one input, how many texts were withheld, and the published
    document; exits, naming why, unless the guard would pass the result at `dest`.

    A path outside the checkout is judged by every component it has: the results root holds a
    host's files in a folder named for it. A file pinned as committed with plain hashes before the
    rule stays as it is: re-keying it would tie the key to texts whose plain hashes are public.
    """
    data = path.read_bytes()
    rel = _in_repo(path) or "/".join(path.resolve().parts[1:])
    if _in_repo(path) and pinned(PLAIN_HASHES_BEFORE_THE_RULE, rel, data):
        document, count = json.loads(data), 0
    else:
        try:
            document, count = withhold(json.loads(data), path.name, key)
        except ValueError as error:
            raise SystemExit(str(error)) from error
    out = published(data.decode(), document).encode() if count else data
    if problems := refusals(rel, out, [document], _in_repo(dest)):
        raise SystemExit(f"{path} may not be published: {'; '.join(problems[:3])}")
    return out, count, document


def main() -> None:
    args = parser.parse_args()
    key = load_key()
    files: dict[str, dict[str, Any]] = {}
    writes: list[tuple[Path, bytes]] = []
    # Every input is checked before any output is written.
    for path in args.paths:
        dest = path if args.in_place else args.out_dir / path.name
        out, count, document = convert(path, dest, key)
        if count:
            files[path.name] = {"fields": document["withheld_text"]["fields"], "texts": count}
        if count or not args.in_place:
            writes.append((dest, out))
    summary = None
    if args.summary:
        previous = json.loads(args.summary.read_text()) if args.summary.is_file() else None
        summary = merged_summary(previous, files, key_id(key))
    for dest, out in writes:
        dest.write_bytes(out)
    print(f"withheld {sum(f['texts'] for f in files.values())} texts in {len(files)} files")
    if args.summary:
        args.summary.write_text(json.dumps(summary, indent=2) + "\n")


def merged_summary(
    previous: dict[str, Any] | None, files: dict[str, dict[str, Any]], current_key_id: str
) -> dict[str, Any]:
    """The record of every file withheld so far, this run's added under its key id: a run over
    a few files, or under a new key, adds to the record rather than replacing it."""
    by_file: dict[str, dict[str, Any]] = {}
    if previous:
        # A record from before key ids were kept per file names one for all of its files.
        for name, entry in previous.get("by_file", {}).items():
            by_file[name] = {**entry, "key_id": entry.get("key_id", previous.get("key_id"))}
    for name, entry in files.items():
        by_file[name] = {**entry, "key_id": current_key_id}
    return {
        "key_ids": sorted({str(entry["key_id"]) for entry in by_file.values()}),
        "files": len(by_file),
        "texts": sum(int(entry["texts"]) for entry in by_file.values()),
        "by_file": dict(sorted(by_file.items())),
        "provenance": provenance_header(),
    }


if __name__ == "__main__":
    main()
