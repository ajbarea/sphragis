"""A stratified sample of training examples for auditing whether each label is real.

An example's label is the hunk at the next patch set, taken as the answer to the reviewer
comment anchored inside it. Code-review datasets carry comments that ask for nothing and
rewrites that answer something else (Too Noisy To Learn, MSR 2025, found about a third of
CodeReviewer's comments invalid), and for this study the danger is not the rate but a rate that
differs between organizations or halves, which the adapters would learn as house style. So the
sample is drawn per organization, window and placebo half, deterministically, from the same
deduplicated split the gate reads.

The sheet it writes holds corpus text (pseudonymized, but code and comments) and stays out of
git; only the labels, keyed by example id, are committed.

    uv run --no-sync --no-active python scripts/label_audit_sample.py \\
        --root datasets/gerrit --per-cell 15 --out scratch/label-audit-sheet.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from placebo_corpus import assign, project_counts  # noqa: E402

from sphragis.corpus.cli import WINDOWS  # noqa: E402
from sphragis.corpus.pipeline import run_dedup, run_split  # noqa: E402

LABELS = ("valid", "non_actionable", "unrelated_rewrite", "partial", "context_dependent")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, default=Path("datasets/gerrit"))
parser.add_argument("--org", action="append", default=[], help="repeatable; default both")
parser.add_argument("--windows", nargs="+", default=["train", "dev"])
parser.add_argument("--per-cell", type=int, default=15, help="examples per org x window x half")
parser.add_argument("--seed", type=int, default=20260923)
parser.add_argument("--out", type=Path, required=True)


def sample(root: Path, org: str, windows: list[str], per_cell: int, seed: int) -> list[dict]:
    rows = [
        json.loads(line)
        for path in sorted((root / org / "examples").glob("*.jsonl"))
        for line in path.read_text().splitlines()
        if line
    ]
    side_of = assign(dict(project_counts(rows, WINDOWS["train"])))
    kept, _ = run_dedup(rows)
    split, _, _ = run_split(kept, WINDOWS)
    drawn = []
    for window in windows:
        for side in (0, 1):
            pool = sorted(
                (r for r in split[window] if side_of.get(r["project"]) == side),
                key=lambda r: r["id"],
            )
            # One example per change, so a change with many hunks cannot fill a cell.
            by_change: dict[str, dict] = {}
            for row in pool:
                by_change.setdefault(row["change_id"], row)
            candidates = sorted(by_change.values(), key=lambda r: r["id"])
            rng = random.Random(f"{seed}:{org}:{window}:{side}")
            for row in rng.sample(candidates, min(per_cell, len(candidates))):
                drawn.append(
                    {
                        "id": row["id"],
                        "org": org,
                        "window": window,
                        "half": f"{org}-{'ab'[side]}",
                        "project": row["project"],
                        "path": row["path"],
                        "comments": row["comments"],
                        "context_before": row.get("context_before", ""),
                        "before": row["before"],
                        "after": row["after"],
                        "context_after": row.get("context_after", ""),
                        "label": None,
                    }
                )
    return drawn


def main() -> None:
    args = parser.parse_args()
    if "test" in args.windows:
        raise SystemExit("the test window is sealed and is never sampled")
    sheet = [
        entry
        for org in args.org or ["openstack", "qt"]
        for entry in sample(args.root, org, args.windows, args.per_cell, args.seed)
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(e) + "\n" for e in sheet))
    print(f"wrote {len(sheet)} examples to {args.out}; labels: {', '.join(LABELS)}")


if __name__ == "__main__":
    main()
