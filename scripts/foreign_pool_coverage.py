"""Whether a built foreign pool covers every matched draw an H2 run can make from it.

Item 4 of the H2 plan (research log, 2026-10-14) takes a source only when its pool covers every
draw: `holdout.matched_foreign_draw` refuses a pool short of a draw rather than training a smaller
arm. The pool is read as the arms read it (`holdout.foreign_pool`: refined, deduplicated as one
corpus, inside the source's registered span); each draw `foreign_need.py` recorded for Qt's
admissible partitions needs as many C++ examples as its sibling's and the rest of N from other
files. Each arm draws from the whole pool, so a draw is covered when the pool alone holds both.
Reads no outcome.

    uv run --no-sync --no-active python scripts/foreign_pool_coverage.py --source aosp \\
        --root ~/ajsoftworks/sphragis-data-local/aosp/listing \\
        --need datasets/results/foreign-need-qt.json \\
        --out datasets/results/foreign-pool-coverage-aosp.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from sphragis.experiment.decomposition import FOREIGN_SOURCES, FOREIGN_SPANS, is_cpp
from sphragis.experiment.holdout import foreign_pool, pool_digest
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", required=True, choices=FOREIGN_SOURCES)
parser.add_argument("--root", type=Path, required=True, help="the source's built corpus root")
parser.add_argument("--need", type=Path, required=True, help="foreign_need.py's artifact")
parser.add_argument("--out", type=Path, required=True)


def coverage(cpp: int, other: int, draws: dict[str, dict[str, dict]]) -> list[dict]:
    """Each draw, by partition seed and half, with what it needs and whether the pool holds it."""
    return [
        {
            "seed": seed,
            "half": half,
            "in_language": draw["in_language"],
            "other": draw["other"],
            "covered": draw["in_language"] <= cpp and draw["other"] <= other,
        }
        for seed, halves in sorted(draws.items(), key=lambda item: int(item[0]))
        for half, draw in sorted(halves.items())
    ]


def main() -> None:
    args = parser.parse_args()
    need = json.loads(args.need.read_text())
    pool, removed = foreign_pool(args.root, args.source, FOREIGN_SPANS[args.source])
    cpp = sum(is_cpp(row) for row in pool)
    other = len(pool) - cpp
    draws = coverage(cpp, other, need["draws"])
    report = {
        "source": args.source,
        "root": str(args.root.resolve()),
        "span": list(FOREIGN_SPANS[args.source]),
        "need": str(args.need),
        "pool": {
            "examples": len(pool),
            "in_language": cpp,
            "other": other,
            "dedup_removed": removed,
            "digest": pool_digest(pool),
            "by_project": dict(Counter(row["project"] for row in pool).most_common()),
        },
        "largest_need": need["need"],
        "draws_covered": sum(d["covered"] for d in draws),
        "draws": len(draws),
        "covers_every_draw": all(d["covered"] for d in draws),
        "shortfall": {
            "in_language": max(0, need["need"]["in_language"]["examples"] - cpp),
            "other": max(0, need["need"]["other"]["examples"] - other),
        },
        "by_draw": draws,
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"{args.source}: pool {len(pool)} ({cpp} C++, {other} other); covers "
        f"{report['draws_covered']} of {len(draws)} draws; shortfall {report['shortfall']}"
    )


if __name__ == "__main__":
    main()
