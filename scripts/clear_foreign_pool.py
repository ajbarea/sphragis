"""H2's registered foreign pool as the stop rule found it covering, written once for the arms.

The stop rule (research log, 2026-10-19) read the pool as the shortest prefix of the source's
order whose refined rows inside its span, deduplicated as one corpus and then cleared of every
exact or near copy of an example in the other organization's unsealed windows, cover every draw.
This writes that prefix pool as a derived corpus file recording both sources
(`load.write_derived_file`), for `rq1_pilot.py --foreign-pool`. It runs the coverage's own code
(`foreign_pool_coverage.prefix_pool`, `unsealed`) on the inputs its artifact names, and writes
nothing unless the pool's digest is the stop rule's and the registered one
(`decomposition.registered_pool`; `--exploratory` for Fuchsia's arm). Reads no outcome.

    uv run --no-sync --no-active python scripts/clear_foreign_pool.py \\
        --coverage datasets/results/foreign-pool-coverage-chromium.json \\
        --root ~/ajsoftworks/sphragis-data-local/chromium-pool/pool \\
        --against qt=datasets/gerrit-granted \\
        --pool ~/ajsoftworks/sphragis-data-local/chromium-pool/cleared/chromium.jsonl \\
        --out datasets/results/foreign-pool-cleared-chromium.json
    uv run --no-sync --no-active python scripts/clear_foreign_pool.py --exploratory \\
        --coverage datasets/results/foreign-pool-coverage-fuchsia.json \\
        --root ~/ajsoftworks/sphragis-data-local/fuchsia/listing \\
        --against qt=datasets/gerrit-granted \\
        --pool ~/ajsoftworks/sphragis-data-local/fuchsia/cleared/fuchsia.jsonl \\
        --out datasets/results/foreign-pool-cleared-fuchsia.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from foreign_pool_coverage import counts, prefix_pool, unsealed  # noqa: E402

from sphragis.corpus.load import refined_examples, write_derived_file  # noqa: E402
from sphragis.durable import write_json_atomic  # noqa: E402
from sphragis.experiment.decomposition import FOREIGN_SPANS, registered_pool  # noqa: E402
from sphragis.experiment.holdout import pool_digest  # noqa: E402
from sphragis.provenance import provenance_header  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--coverage", type=Path, required=True, help="foreign_pool_coverage.py's")
parser.add_argument("--root", type=Path, required=True, help="the source's built corpus root")
parser.add_argument("--against", required=True, help="ORG=ROOT: the organization cleared against")
parser.add_argument("--pool", type=Path, required=True, help="the cleared pool's derived file")
parser.add_argument("--out", type=Path, required=True, help="the clearing's artifact")
parser.add_argument(
    "--exploratory", action="store_true", help="H2's exploratory source, beside the registered one"
)


def stop_rule(coverage: dict[str, Any], org: str, *, exploratory: bool = False) -> dict[str, Any]:
    """The coverage's stop rule, refused unless it covered, against `org`, at the registered
    source and digest (the exploratory source's with `exploratory`)."""
    try:
        digest = registered_pool(coverage["source"], exploratory=exploratory)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    stop = coverage.get("stop_rule") or {}
    if not stop.get("covers_every_draw"):
        raise SystemExit("the coverage's stop rule found no covering prefix")
    if stop["against"] != org:
        raise SystemExit(f"the stop rule cleared against {stop['against']}, not {org}")
    if stop["digest"] != digest:
        raise SystemExit(f"the stop rule's pool is {stop['digest']}, not the registered one")
    return stop


def cleared(
    stop: dict[str, Any],
    order: Sequence[str],
    refined: Sequence[dict[str, Any]],
    theirs: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """The stop rule's prefix pool, refused unless it is the pool the stop rule read."""
    if len(theirs) != stop["against_examples"]:
        raise SystemExit(
            f"{len(theirs)} unsealed {stop['against']} examples, not the stop rule's "
            f"{stop['against_examples']}"
        )
    pool = prefix_pool(refined, order[: stop["projects"]], theirs)
    if pool_digest(pool) != stop["digest"]:
        raise SystemExit(f"the cleared pool is {pool_digest(pool)}, not the stop rule's")
    return pool


def main() -> None:
    args = parser.parse_args()
    if "=" not in args.against:
        raise SystemExit("--against takes ORG=ROOT")
    org, against_root = args.against.split("=", 1)
    coverage = json.loads(args.coverage.read_text())
    stop = stop_rule(coverage, org, exploratory=args.exploratory)
    source = coverage["source"]
    span = FOREIGN_SPANS[source]
    order = [entry["project"] for entry in json.loads(Path(stop["order"]).read_text())["order"]]
    refined = [
        row
        for row in refined_examples(args.root, source)
        if span[0] <= str(row.get("created", ""))[:10] < span[1]
    ]
    pool = cleared(stop, order, refined, unsealed(Path(against_root), org))
    write_derived_file(args.pool, pool, sources=[(args.root, source), (Path(against_root), org)])
    cpp, other = counts(pool)
    write_json_atomic(
        args.out,
        {
            "source": source,
            "exploratory": args.exploratory,
            "coverage": str(args.coverage),
            "projects": list(order[: stop["projects"]]),
            "against": {"org": org, "examples": stop["against_examples"]},
            "cleared_pool": {
                "examples": len(pool),
                "in_language": cpp,
                "other": other,
                "digest": pool_digest(pool),
            },
            "file": str(args.pool.resolve()),
            "provenance": provenance_header(),
        },
    )
    print(f"{source}: cleared pool {len(pool)} ({cpp} C++, {other} other) at {args.pool}")


if __name__ == "__main__":
    main()
