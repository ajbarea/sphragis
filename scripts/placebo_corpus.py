"""Two pseudo-organizations built from one organization's own projects.

The registered gate asks whether an adapter trained on one organization beats an adapter
trained on another, on the first organization's held-out refinements. It cannot by itself say
whether the *organization* is what the adapters learned: a project, a codebase family or a
document type would all produce the same reading, and this study's own separability probe
already reports that two projects inside one organization separate as well as two
organizations do.

This builds the control that answers it. One organization's projects are split into two
halves, each half is written as its own pseudo-organization under a new corpus root, and the
registered runner is pointed at the result with no change at all. If an arbitrary boundary
inside one organization reproduces the cross-organization contrast, the gate is reading
project idiom and the organization is not the unit. If it returns nothing where the real
boundary returns a gain, the boundary is doing the work.

The split is deterministic and stated in advance, because a placebo whose partition can be
reshuffled until it behaves is not a control. Projects are sorted by training-window example
count, largest first, and each is assigned to whichever side is smaller so far: the greedy
least-loaded rule, no seed, no search, and the resulting counts are recorded beside the
corpus rather than chosen.

    uv run --no-sync python scripts/placebo_corpus.py \
        --root ~/corpus-windows --org qt --out-root ~/corpus-windows-placebo-qt
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

from sphragis.corpus.load import mark_derived, refined_dir, refined_month_files
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True, help="a built corpus root")
parser.add_argument("--org", required=True, help="the organization to split against itself")
parser.add_argument(
    "--out-root", type=Path, required=True, help="root for the pseudo-organizations"
)
parser.add_argument(
    "--names",
    nargs=2,
    default=None,
    help="the two pseudo-organization names; defaults to <org>-a and <org>-b",
)
parser.add_argument(
    "--train-window",
    default="train",
    help="the window whose counts the split balances; the others follow the same assignment",
)


def assign(counts: dict[str, int]) -> dict[str, int]:
    """Each project to a side, largest first, always to the smaller side.

    Deterministic and unseeded on purpose. Ties in count break by name, so the assignment is
    a function of the corpus alone and re-running it cannot produce a different control.
    """
    sides = [0, 0]
    out: dict[str, int] = {}
    for project in sorted(counts, key=lambda p: (-counts[p], p)):
        side = 0 if sides[0] <= sides[1] else 1
        out[project] = side
        sides[side] += counts[project]
    return out


def project_counts(rows: Iterable[dict], window: tuple[str, str]) -> Counter[str]:
    """Examples per project created inside one window: what `assign` balances."""
    start, end = window
    return Counter(row["project"] for row in rows if start <= row["created"][:10] < end)


def main() -> None:
    args = parser.parse_args()
    months = refined_month_files(args.root, args.org)
    if not months:
        raise SystemExit(f"no refined examples under {args.root / args.org}")

    rows_by_month = {
        month.name: [json.loads(line) for line in month.read_text().splitlines() if line]
        for month in months
    }
    # The split balances the window the adapters train on, because that is the budget a side
    # could be advantaged by. Every other window follows the same project assignment, so no
    # window is balanced twice and the evaluation sets are whatever the assignment gives.
    from sphragis.corpus.cli import WINDOWS

    train_counts = project_counts(
        (row for rows in rows_by_month.values() for row in rows), WINDOWS[args.train_window]
    )
    if len(train_counts) < 2:
        raise SystemExit(f"{args.org} has {len(train_counts)} projects in {args.train_window}")

    side_of = assign(dict(train_counts))
    names = args.names or [f"{args.org}-a", f"{args.org}-b"]
    # A reused out-root would keep months an earlier split wrote for a half that now has none.
    for name in names:
        for stale in refined_dir(args.out_root, name).glob("*"):
            stale.unlink()
    per_side: dict[int, Counter[str]] = {0: Counter(), 1: Counter()}
    written: dict[int, int] = {0: 0, 1: 0}

    for month, rows in rows_by_month.items():
        halves: dict[int, list[dict]] = defaultdict(list)
        for row in rows:
            # A project with no training-window rows was never assigned; it belongs to
            # neither side and is dropped rather than silently landing on side 0.
            side = side_of.get(row["project"])
            if side is None:
                continue
            row = dict(row, org=names[side])
            halves[side].append(row)
        for side, kept in halves.items():
            # Written as refined months of a derived corpus: the halves have no builds of their
            # own, and the loader checks them against the rules their source was refined under.
            out = refined_dir(args.out_root, names[side])
            out.mkdir(parents=True, exist_ok=True)
            (out / month).write_text("".join(json.dumps(r) + "\n" for r in kept))
            written[side] += len(kept)
            per_side[side][month] += len(kept)

    for name in names:
        mark_derived(args.out_root, name, source_root=args.root, source_org=args.org)
    manifest = {
        "provenance": provenance_header(),
        "source_root": str(args.root),
        "source_org": args.org,
        "rule": "greedy least-loaded over training-window example counts, largest first",
        "train_window": args.train_window,
        "names": names,
        "projects": {
            names[side]: sorted(p for p, s in side_of.items() if s == side) for side in (0, 1)
        },
        "train_examples": {
            names[side]: sum(c for p, c in train_counts.items() if side_of[p] == side)
            for side in (0, 1)
        },
        "examples_written": {names[side]: written[side] for side in (0, 1)},
        "dropped_projects": sorted(
            {row["project"] for rows in rows_by_month.values() for row in rows} - set(side_of)
        ),
    }
    args.out_root.mkdir(parents=True, exist_ok=True)
    (args.out_root / "placebo.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for side in (0, 1):
        print(
            f"{names[side]:16} {len(manifest['projects'][names[side]]):4} projects, "
            f"{manifest['train_examples'][names[side]]:6} train examples, "
            f"{written[side]:6} written"
        )
    print(f"wrote {args.out_root / 'placebo.json'}")


if __name__ == "__main__":
    main()
