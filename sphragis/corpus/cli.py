"""Corpus pipeline entry point: the one place that touches disk or the network."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

STAGES = ("fetch", "build", "dedup", "split", "freeze", "verify")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sphragis.corpus")
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--org", default="openstack")
    parser.add_argument("--window", default="pilot")
    parser.add_argument("--root", type=Path, default=Path("datasets/gerrit"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"stage {args.stage!r} has no body yet; see the A2 plan")
    return 1
