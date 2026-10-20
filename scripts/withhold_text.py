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
    granted_content,
    granted_findings,
    granted_path,
    key_id,
    load_key,
    pinned,
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


def refuse_granted(path: Path, raw: bytes, document: object) -> None:
    """Exit on a document drawn from a host opened by written grant, whose model text the grants
    keep out of anything published, a digest of it included; the committed exceptions pass."""
    resolved = path.resolve()
    rel = resolved.relative_to(ROOT).as_posix() if resolved.is_relative_to(ROOT) else path.name
    granted = (granted_path(rel) or granted_content(document)) and granted_findings(document)
    if granted and not pinned(rel, raw):
        raise SystemExit(
            f"{path} is drawn from a host opened by written grant, which keeps its model text "
            "out of anything published, digests included: leave it in the results root."
        )


def published(raw: str, document: object) -> str:
    """The document in the results files' own layout, a trailing newline kept if there was one."""
    text = json.dumps(document, indent=2)
    return text + "\n" if raw.endswith("\n") else text


def main() -> None:
    args = parser.parse_args()
    key = load_key()
    files: dict[str, dict[str, Any]] = {}
    for path in args.paths:
        data = path.read_bytes()
        raw = data.decode()
        document = json.loads(raw)
        refuse_granted(path, data, document)
        document, count = withhold(document, path.name, key)
        out = path if args.in_place else args.out_dir / path.name
        if count:
            out.write_text(published(raw, document))
            files[path.name] = {"fields": document["withheld_text"]["fields"], "texts": count}
        elif not args.in_place:
            out.write_text(raw)
    print(f"withheld {sum(f['texts'] for f in files.values())} texts in {len(files)} files")
    if args.summary:
        previous = json.loads(args.summary.read_text()) if args.summary.is_file() else None
        summary = merged_summary(previous, files, key_id(key))
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
