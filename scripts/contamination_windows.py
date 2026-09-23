"""Assemble the two sides of the contamination battery, matched month for month.

The post-cutoff side postdates the checkpoint's release outright; the pre-cutoff control sits
inside any reading of its training data. The only thing that may differ between them is the
date, so both sides take the same number of months, are built by the same pipeline under a
uniform cutoff, and are deduplicated the same way. Zhang et al. (ACL 2026) show that
differences in construction distort temporal signals on their own.

Run: uv run --no-active python scripts/contamination_windows.py
"""

from __future__ import annotations

import json
from pathlib import Path

from sphragis.corpus.load import refined_month_files
from sphragis.corpus.pipeline import run_dedup

OUT = Path("datasets/results/contamination-inputs")
# Six months each. Post starts at the corpus's first month rather than a chosen one.
POST = ("2024-10", "2024-11", "2024-12", "2025-01", "2025-02", "2025-03")
PRE = ("2023-08", "2023-09", "2023-10", "2023-11", "2023-12", "2024-01")
SIDES = {
    "post": (Path("datasets/gerrit"), POST),
    "pre": (Path("datasets/gerrit-control"), PRE),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for side, (root, months) in SIDES.items():
        available = {p.stem: p for p in refined_month_files(root, "openstack")}
        rows: list[dict] = []
        for month in months:
            path = available.get(month)
            if path is None:
                raise SystemExit(
                    f"missing refined {month} under {root}; the {side} side is incomplete"
                )
            rows.extend(json.loads(line) for line in path.open() if line.strip())
        kept, removed = run_dedup(rows)
        target = OUT / f"openstack-{side}.jsonl"
        target.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in kept))
        changes = len({r["change_id"] for r in kept})
        print(
            f"{side}: {len(months)} months, {len(rows)} examples, {len(kept)} after dedup "
            f"over {changes} changes, removed {dict(removed)}"
        )
        print(f"  wrote {target}")


if __name__ == "__main__":
    main()
