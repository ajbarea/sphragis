"""How many examples could carry code older than the checkpoint by way of a backport.

A change's window is its creation month, so a backport created inside the corpus can carry code
first written before the checkpoint was published. A backport shares its Change-Id with the
change it was picked from, and the examples keep no branch, so an example is bounded: it is
only on a backport branch when every change with its (project, Change-Id) is (its original is
not in the corpus, so may predate it), and possibly from one when any is (its original is in the
corpus and postdates the checkpoint like the backport). Both count only backports that keep their
Change-Id: a revert, a re-proposal or a hand copy carries old code under a new one, unseen here.
Windows are the corpus's own; the test window is never read.

    uv run --no-sync --no-active python scripts/backport_share.py \\
        --root openstack=../wm-bots/datasets/gerrit \\
        --root wikimedia=../fa-auto/datasets/gerrit \\
        --out datasets/results/backport-share.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from sphragis.corpus.backports import (
    BACKPORT,
    branches_by_change,
    only_backport,
    possibly_backport,
)
from sphragis.corpus.load import refined_examples
from sphragis.corpus.pipeline import run_dedup, run_split
from sphragis.corpus.windows import WINDOWS
from sphragis.provenance import provenance_header

READ_WINDOWS = ("pilot", "train", "dev")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", action="append", required=True, help="org=root of a built corpus")
parser.add_argument("--out", type=Path, required=True)


def bounds(
    windows: Mapping[str, Iterable[Mapping]], branches: Mapping[tuple[str, str], set[str]]
) -> dict:
    """Per window: examples, and those only (lower) or possibly (upper) on a backport branch."""
    out = {}
    for window in READ_WINDOWS:
        rows = list(windows.get(window, []))
        lower = upper = unmatched = 0
        for row in rows:
            names = branches.get((row["project"], row["change_id"]), set())
            unmatched += not names
            upper += possibly_backport(names)
            lower += only_backport(names)
        out[window] = {
            "examples": len(rows),
            "only_backport": lower,
            "possibly_backport": upper,
            "no_raw_change": unmatched,
        }
    return out


def main() -> None:
    args = parser.parse_args()
    report: dict = {"backport_branches": BACKPORT.pattern, "orgs": {}}
    for spec in args.root:
        org, root = spec.split("=", 1)
        branches = branches_by_change(Path(root), org)
        kept, _ = run_dedup(refined_examples(Path(root), org))
        windows, _, _ = run_split(kept, WINDOWS)
        if windows.get("test"):
            raise SystemExit(f"{org}: the corpus holds test-window examples; it is sealed")
        report["orgs"][org] = bounds(windows, branches)
        print(org, report["orgs"][org])
    report["provenance"] = provenance_header()
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
