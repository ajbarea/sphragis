#!/usr/bin/env python3
"""Take third-party email addresses out of the published result artifacts.

A thin command over `sphragis.corpus.redact`, which explains why the corpus keeps the code
intact and the release does not.

What this does NOT do is move a number. Every affected record scores 0.0 on exact match,
exact match raw and normalized exact match, because the address appears on one side of the
pair only, so the two strings differ before and after the substitution alike. Those three
are what the gate and its registered readings are built from.

`edit_similarity` is the one stored value the substitution would move, and it is left at
what was measured rather than recomputed: the repository's stated invariant is that a
re-run reproduces the artifact, and a re-run reproduces the measured value. Records whose
text changed carry `identities_redacted: true`, so the gap between the published text and
the published number is stated rather than discovered as apparent drift.

Run: uv run --no-sync --no-active python scripts/redact_identities.py --check
     uv run --no-sync --no-active python scripts/redact_identities.py --write
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from sphragis.corpus.redact import redact_file

RESULTS = pathlib.Path(__file__).resolve().parents[1] / "datasets" / "results"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="apply the redaction")
    parser.add_argument("--check", action="store_true", help="exit non-zero if any remain")
    args = parser.parse_args()

    everything: set[str] = set()
    changed: list[str] = []
    for path in sorted(RESULTS.rglob("*.json")):
        found, redacted = redact_file(path)
        if not found:
            continue
        everything |= found
        changed.append(path.name)
        if args.write and redacted is not None:
            path.write_text(redacted)

    verb = "redacted from" if args.write else "found in"
    print(f"{len(everything)} third-party address(es) {verb} {len(changed)} artifact(s)")
    for name in changed:
        print(f"  {name}")
    if args.check and everything:
        print("\nrun `python scripts/redact_identities.py --write`", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
