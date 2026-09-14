"""Corpus pipeline entry point: the one place that touches disk or the network."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"stage {args.stage!r} for {args.org}: not wired to disk yet; see plan A2")
    return 1
