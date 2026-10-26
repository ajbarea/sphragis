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

Run: make redact-check    # report
     make redact          # apply
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

from sphragis.corpus.redact import addresses_in, redact_file

RESULTS = pathlib.Path(__file__).resolve().parents[1] / "datasets" / "results"


def artifacts(results: pathlib.Path = RESULTS) -> list[pathlib.Path]:
    """Every file under `results` git does not ignore: what is published or about to be.
    An ignored file there is a local input (`contamination-inputs/`), never published, and
    rewriting it changes study data."""
    files = sorted(p for p in results.rglob("*") if p.is_file())
    ignored = subprocess.run(
        ["git", "-C", str(results), "check-ignore", "--stdin", "-z"],
        input="\0".join(str(p) for p in files),
        capture_output=True,
        text=True,
    )
    if ignored.returncode not in (0, 1):
        raise SystemExit(f"git check-ignore failed: {ignored.stderr.strip()}")
    skip = {pathlib.Path(p) for p in ignored.stdout.split("\0") if p}
    return [p for p in files if p not in skip]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="apply the redaction")
    parser.add_argument("--check", action="store_true", help="exit non-zero if any remain")
    args = parser.parse_args()

    everything: set[str] = set()
    changed: list[str] = []
    # Every file git does not ignore, not only *.json: the directory also holds .txt and .npz,
    # and a file class nobody scans is how a guard passes while the disclosure stands.
    for path in artifacts():
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

    if not args.check:
        return
    # Re-scan rather than reusing the pre-write tally, so `--write --check` means "fix it
    # and confirm it is clean" instead of always failing on what it just repaired.
    remaining = {address for path in artifacts() for address in addresses_in(path)}
    if remaining:
        print(f"\n{len(remaining)} address(es) remain; run `make redact`", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
