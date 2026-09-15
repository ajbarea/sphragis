"""What the collected windows contain, and how much leaks across their boundaries.

Reports per organization: examples and changes per window after dedup, and the
near-duplicate rate from each window into the next. Outcome-neutral test 4's threshold is a
Stage 1 decision, and it should rest on the measured cross-window rate rather than on a
number chosen in advance.

Reads built examples only. The test window is sealed and is not read, listed or counted.
"""

import argparse
import json
from pathlib import Path

from sphragis.corpus.cli import WINDOWS
from sphragis.corpus.pipeline import run_dedup, run_split
from sphragis.experiment.neutral import near_duplicate_rate

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, default=Path("datasets/gerrit"))
parser.add_argument("--org", action="append", default=[], help="repeatable; default both")
parser.add_argument(
    "--threshold",
    type=float,
    action="append",
    default=[],
    help="Jaccard thresholds to report; default 0.8 (dedup's), 0.7, 0.6, 0.5",
)
parser.add_argument("--out", type=Path, default=None)
args = parser.parse_args()

# Sealed: never read, never counted, not even to report a zero.
COLLECTED = [name for name in WINDOWS if name != "test"]
report: dict[str, dict] = {}

for org in args.org or ["openstack", "qt"]:
    directory = args.root / org / "examples"
    rows = [
        json.loads(line)
        for path in sorted(directory.glob("*.jsonl"))
        for line in path.read_text().splitlines()
        if line
    ]
    if not rows:
        print(f"{org}: no examples under {directory}; build first")
        continue
    kept, removed = run_dedup(rows)
    windows, straddling, unassigned = run_split(kept, WINDOWS)
    counts = {
        name: {
            "examples": len(windows[name]),
            "changes": len({r["change_id"] for r in windows[name]}),
        }
        for name in COLLECTED
    }
    # Several thresholds, because dedup runs before the split at Jaccard 0.8 and removes those
    # pairs across windows as well as within them: a zero rate at 0.8 is partly guaranteed by
    # construction. The looser thresholds are where residual leakage is visible, and are what
    # the Stage 1 threshold should be chosen against.
    thresholds = sorted(args.threshold or [0.8, 0.7, 0.6, 0.5], reverse=True)
    leakage: dict[str, dict[str, dict[str, float]]] = {}
    for earlier, later in zip(COLLECTED, COLLECTED[1:], strict=False):
        by_threshold = {}
        for threshold in thresholds:
            rate, hits = near_duplicate_rate(windows[earlier], windows[later], threshold=threshold)
            by_threshold[f"{threshold:g}"] = {"rate": rate, "near_duplicates": len(hits)}
        leakage[f"{earlier}->{later}"] = by_threshold
    report[org] = {
        "months_built": len(list(directory.glob("*.jsonl"))),
        "examples": len(rows),
        "after_dedup": len(kept),
        "dedup_removed": removed,
        "windows": counts,
        "straddling_changes": len(straddling),
        "unassigned_changes": len(unassigned),
        "near_duplicate_rate": leakage,
        "dedup_note": (
            "dedup runs before the split at Jaccard 0.8, so the 0.8 rate is partly zero by "
            "construction; read the looser thresholds"
        ),
    }
    print(f"\n{org}: {len(rows)} examples, {len(kept)} after dedup {removed}")
    for name in COLLECTED:
        c = counts[name]
        print(f"  {name:<6} {c['examples']:>6} examples over {c['changes']:>5} changes")
    for pair, by_threshold in leakage.items():
        rendered = "  ".join(
            f"J>={t}: {v['rate']:.4f} ({v['near_duplicates']})" for t, v in by_threshold.items()
        )
        print(f"  near-duplicates {pair:<14} {rendered}")
    if unassigned:
        print(f"  UNASSIGNED {len(unassigned)} changes match no window")

if args.out:
    args.out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {args.out}")
