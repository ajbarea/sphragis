"""Whether a built foreign pool covers every matched draw an H2 run can make from it.

Item 4 of the H2 plan (research log, 2026-10-14) takes a source only when its pool covers every
draw: `holdout.matched_foreign_draw` refuses a pool short of a draw rather than training a smaller
arm. The pool is read as the arms read it (`holdout.foreign_pool`: refined, deduplicated as one
corpus, inside the source's registered span); each draw `foreign_need.py` recorded for Qt's
admissible partitions needs as many C++ examples as its sibling's and the rest of N from other
files. Each arm draws from the whole pool, so a draw is covered when the pool alone holds both; a
partition is covered when both its arms' draws are, since a run refuses a missing arm.

With `--order` and `--against`, the registered stop rule (research log, 2026-10-19): the pool is
the shortest prefix of item 4's order whose refined examples, deduplicated as one corpus and then
cleared of every exact or near duplicate (Jaccard 0.8 on the pair's shingles, the corpus's own
threshold) of an example in the other organization's unsealed windows, cover the draws of every
admissible partition. A prefix adds rows, so if the whole order does not cover, none does.
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
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sphragis.corpus.dedup import jaccard, pair_text, shingles
from sphragis.corpus.load import refined_examples
from sphragis.corpus.pipeline import run_dedup
from sphragis.corpus.windows import WINDOWS
from sphragis.experiment.decomposition import FOREIGN_SOURCES, FOREIGN_SPANS, is_cpp
from sphragis.experiment.holdout import foreign_pool, pool_digest
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", required=True, choices=FOREIGN_SOURCES)
parser.add_argument("--root", type=Path, required=True, help="the source's built corpus root")
parser.add_argument("--need", type=Path, required=True, help="foreign_need.py's artifact")
parser.add_argument("--order", type=Path, help="pool_order.py's artifact: the stop rule's order")
parser.add_argument("--against", help="ORG=ROOT: the organization deduplicated against")
parser.add_argument("--out", type=Path, required=True)

#: The corpus's near-duplicate threshold (`corpus.dedup`).
NEAR = 0.8


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


def partitions_covered(draws: list[dict]) -> int:
    """Partitions whose every arm's draw is covered."""
    by_seed: dict[str, bool] = {}
    for draw in draws:
        by_seed[draw["seed"]] = by_seed.get(draw["seed"], True) and draw["covered"]
    return sum(by_seed.values())


def against(pool: Sequence[dict[str, Any]], other: Sequence[dict[str, Any]]) -> list[dict]:
    """The pool's rows that neither copy nor nearly copy a row of `other`."""
    exact = {pair_text(row) for row in other}
    sized: dict[int, list[frozenset[str]]] = {}
    for row in other:
        signature = shingles(pair_text(row))
        sized.setdefault(len(signature), []).append(signature)
    kept = []
    for row in pool:
        text = pair_text(row)
        if text in exact:
            continue
        signature = shingles(text)
        # Jaccard >= NEAR needs sizes within NEAR of each other.
        low, high = int(len(signature) * NEAR), int(len(signature) / NEAR) + 1
        near = any(
            jaccard(signature, seen) >= NEAR
            for size in range(low, high + 1)
            for seen in sized.get(size, ())
        )
        if not near:
            kept.append(row)
    return kept


def unsealed(root: Path, org: str) -> list[dict[str, Any]]:
    """An organization's deduplicated examples created before the sealed test window."""
    rows, _ = run_dedup(refined_examples(root, org))
    return [row for row in rows if str(row.get("created", "")) < WINDOWS["test"][0]]


def prefix_pool(
    pool: Sequence[dict[str, Any]], projects: Sequence[str], other: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The pool a prefix of the order gives: its refined rows deduplicated, then against `other`."""
    named = set(projects)
    rows, _ = run_dedup([row for row in pool if row["project"] in named])
    return against(rows, other)


def counts(rows: Sequence[dict[str, Any]]) -> tuple[int, int]:
    cpp = sum(is_cpp(row) for row in rows)
    return cpp, len(rows) - cpp


def main() -> None:
    args = parser.parse_args()
    need = json.loads(args.need.read_text())
    pool, removed = foreign_pool(args.root, args.source, FOREIGN_SPANS[args.source])
    cpp = sum(is_cpp(row) for row in pool)
    other = len(pool) - cpp
    draws = coverage(cpp, other, need["draws"])
    stop: dict[str, Any] | None = None
    if args.order is not None or args.against is not None:
        if args.order is None or args.against is None or "=" not in args.against:
            raise SystemExit("the stop rule needs --order and --against ORG=ROOT together")
        org, root = args.against.split("=", 1)
        theirs = unsealed(Path(root), org)
        span = FOREIGN_SPANS[args.source]
        refined = [
            row
            for row in refined_examples(args.root, args.source)
            if span[0] <= str(row.get("created", ""))[:10] < span[1]
        ]
        order = [entry["project"] for entry in json.loads(args.order.read_text())["order"]]
        whole = prefix_pool(refined, order, theirs)
        largest = need["need"]["in_language"]["examples"], need["need"]["other"]["examples"]
        cut = None
        if all(c >= n for c, n in zip(counts(whole), largest, strict=True)):
            for k in range(1, len(order) + 1):
                rows = [row for row in refined if row["project"] in set(order[:k])]
                if any(c < n for c, n in zip(counts(rows), largest, strict=True)):
                    continue
                prefix = prefix_pool(refined, order[:k], theirs)
                if all(d["covered"] for d in coverage(*counts(prefix), need["draws"])):
                    cut = k
                    break
        prefix = prefix_pool(refined, order[:cut], theirs) if cut else whole
        cpp_p, other_p = counts(prefix)
        stop = {
            "order": str(args.order),
            "against": org,
            "against_examples": len(theirs),
            "whole_order": {"examples": len(whole), "in_language": counts(whole)[0]},
            "projects": cut,
            "pool": {"examples": len(prefix), "in_language": cpp_p, "other": other_p},
            "digest": pool_digest(prefix) if cut else None,
            "covers_every_draw": cut is not None,
        }
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
        "partitions_covered": partitions_covered(draws),
        "partitions": len({d["seed"] for d in draws}),
        "stop_rule": stop,
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
