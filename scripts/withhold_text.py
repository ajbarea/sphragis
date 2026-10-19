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

from sphragis.measure.withhold import key_id, load_key, withhold
from sphragis.provenance import provenance_header

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


def main() -> None:
    args = parser.parse_args()
    key = load_key()
    files: dict[str, dict[str, object]] = {}
    for path in args.paths:
        raw = path.read_text()
        document, count = withhold(json.loads(raw), path.name, key)
        out = path if args.in_place else args.out_dir / path.name
        if count:
            out.write_text(published(raw, document))
            files[path.name] = {"fields": document["withheld_text"]["fields"], "texts": count}
        elif not args.in_place:
            out.write_text(raw)
    print(f"withheld {sum(f['texts'] for f in files.values())} texts in {len(files)} files")
    if args.summary:
        summary = {
            "key_id": key_id(key),
            "files": len(files),
            "texts": sum(f["texts"] for f in files.values()),
            "by_file": dict(sorted(files.items())),
            "provenance": provenance_header(),
        }
        args.summary.write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
