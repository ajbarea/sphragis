"""Corpus pipeline entry point: the one place that touches disk or the network."""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from pathlib import Path

# A date records a determination; DEFERRED records a considered decision not to seek one.
# Either satisfies the gate. PENDING and a missing file do not.
_DETERMINATION = re.compile(r"Determination:\s*(\d{4}-\d{2}-\d{2}|DEFERRED)")

STAGES = ("fetch", "build", "dedup", "split", "freeze", "verify")


def hsro_determination(path: Path) -> str | None:
    """The determination date recorded in the HSRO file, if there is one."""
    if not path.is_file():
        return None
    match = _DETERMINATION.search(path.read_text())
    return match.group(1) if match else None


def require_hsro(path: Path) -> str:
    """Return the determination date, or exit; collection may not start without it."""
    date = hsro_determination(path)
    if date is None:
        raise SystemExit(
            f"{path} records no decision. Write either a determination date or "
            "'Determination: DEFERRED' before collecting."
        )
    return date


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sphragis.corpus")
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--org", default="openstack")
    parser.add_argument("--window", default="pilot")
    parser.add_argument("--hsro", type=Path, default=Path("corpus/HSRO.md"))
    parser.add_argument("--root", type=Path, default=Path("datasets/gerrit"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.stage == "fetch":
        require_hsro(args.hsro)
    print(f"stage {args.stage!r} has no body yet; see the A2 plan")
    return 1
