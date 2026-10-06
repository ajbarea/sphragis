"""The examples each registered sensitivity of H1 removes, by id, for one organization and window.

Two of the readings registered beside the pass rule recompute an H1 cell without a set of
examples (registered decisions, "Readings beside the pass rule"): those whose change's merged
commit message carries an AI trailer naming a tool (`scripts/ai_trailers.py`'s flagged changes),
and those whose (project, Change-Id) sits only on release, maintenance or deployment branches
(`sphragis.corpus.backports`). A run's rows carry ids and Change-Ids but no project, so the ids
are read here, from the corpus the partitions were built from, deduplicated and split as the
study loads it; `partition_pilot.py --without NAME=FILE` drops them from every run.

    uv run --no-sync --no-active python scripts/sensitivity_ids.py \\
        --root ~/ajsoftworks/sphragis-data/corpus-v3 --org openstack \\
        --ai-trailers datasets/results/ai-trailers.json \\
        --out datasets/results/sensitivity-ids-openstack-dev.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sphragis.corpus.backports import (
    BACKPORT,
    index_changes,
    only_backport,
)
from sphragis.corpus.split import seal_open
from sphragis.corpus.windows import WINDOWS
from sphragis.experiment.holdout import window_split
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True, help="the corpus the partitions came from")
parser.add_argument("--org", required=True)
parser.add_argument(
    "--window",
    default="dev",
    choices=sorted(WINDOWS),
    help="the test window only once it is unsealed, as the runner reads it",
)
parser.add_argument("--ai-trailers", type=Path, required=True, help="ai_trailers.py's artifact")
parser.add_argument("--out", type=Path, required=True)


def flagged_changes(trailers: dict, org: str) -> set[tuple[str, str]]:
    """The (project, Change-Id) pairs `ai_trailers.py` found an AI trailer on."""
    if org not in trailers.get("orgs", {}):
        raise SystemExit(f"the AI-trailer artifact holds no {org}")
    pairs = set()
    for entry in trailers["orgs"][org]["changes_ai"]:
        project, change_id = entry.rsplit(" ", 1)
        pairs.add((project, change_id))
    return pairs


def covers(trailers: dict, window: str) -> bool:
    """Whether the trailer searches span the window: changes are flagged only where searched."""
    start, end = WINDOWS[window]
    return trailers["since"] <= start and end <= trailers["merged_before"]


def main() -> None:
    args = parser.parse_args()
    # The sealed window, as the sibling readers refuse it, until acceptance unseals it.
    if args.window == "test" and not seal_open(args.root, args.org):
        raise SystemExit(f"{args.org}'s test window is sealed: {args.root / args.org}/seal.json")
    trailers = json.loads(args.ai_trailers.read_text())
    if not covers(trailers, args.window):
        raise SystemExit(
            f"{args.ai_trailers} searched changes merged {trailers['since']} to "
            f"{trailers['merged_before']}, not the {args.window} window {WINDOWS[args.window]}; "
            "for the test window, after acceptance, rerun ai_trailers.py --through-test"
        )
    try:
        _, rows, read = window_split(args.root, args.org, eval_window=args.window)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    ai = flagged_changes(trailers, args.org)
    branches, merged = index_changes(args.root, args.org)
    # The searches are bounded by merge date and windows by creation date, so an example whose
    # change merged on or after the search bound, or never, was never searched: counted, so the
    # AI-assisted reading states what it could not see.

    def searched(row: dict) -> bool:
        when = merged.get((row["project"], row["change_id"]))
        return when is not None and when < trailers["merged_before"]

    unsearched = sorted(r["id"] for r in rows if not searched(r))
    removed = {
        "ai_assisted": sorted(r["id"] for r in rows if (r["project"], r["change_id"]) in ai),
        "backport_only": sorted(
            r["id"]
            for r in rows
            if only_backport(branches.get((r["project"], r["change_id"]), set()))
        ),
    }
    report = {
        "org": args.org,
        "window": args.window,
        "source": read["source"],
        "examples": len(rows),
        "changes": len({(r["project"], r["change_id"]) for r in rows}),
        "rules": {
            "ai_assisted": f"(project, Change-Id) among {args.ai_trailers}'s changes_ai",
            "backport_only": f"(project, Change-Id) only on branches matching {BACKPORT.pattern}",
        },
        "ids": removed,
        "ai_unsearched": unsearched,
        # Every example this listing was made from, so a reader can check its runs came from it.
        "universe": sorted(r["id"] for r in rows),
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        {name: len(ids) for name, ids in removed.items()},
        f"of {len(rows)};",
        f"{len(unsearched)} unsearched",
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
