"""The registered dev-window reading with examples that rest only on automated reviewers removed.

Qt's Sanity Bot posts templated inline comments ("Hint: Trailing whitespace", "Hint: Leading
tabs") that the build keeps as reviewer comments, and OpenStack has no equivalent. A bot
enforces written rules, which is the opposite of the unwritten conventions the study measures,
and it runs on one organization only, so an adapter that learned the bot would read as
organization-specific adaptation. This re-reads the registered run (merged seeds, crossed
interval, pooled estimand) twice, as registered and with every example whose comments are all
automated removed, from the same held-out rows.

The rule is the registered bot templates (`sphragis.corpus.automated`), the same the corpus
refinement applies; the artifact records the registry's digest.

    uv run --no-sync --no-active python scripts/bot_sensitivity.py \\
        datasets/results/rq1-windows-qtfull-fp32.json \\
        datasets/results/rq1-windows-qtfull-fp32-s2.json \\
        datasets/results/rq1-windows-qtfull-fp32-s3.json \\
        --corpus datasets/gerrit --out datasets/results/bot-sensitivity-qtfull-fp32.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crossed_reread import merge  # noqa: E402

from sphragis.corpus.automated import matched_bot, registry_digest  # noqa: E402
from sphragis.experiment.walk import crossed_gate  # noqa: E402
from sphragis.provenance import provenance_header  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("runs", type=Path, nargs="+")
parser.add_argument("--corpus", type=Path, default=Path("datasets/gerrit"))
parser.add_argument("--bootstrap-seed", type=int, default=0)
parser.add_argument("--out", type=Path, required=True)


def automated_ids(corpus: Path, orgs: list[str]) -> set[str]:
    """Ids of examples whose every anchored comment is automated.

    Reads the built examples, not the refined ones, on purpose: its subject is the examples the
    refinement removes, and a run scored before the refinement names built example ids.
    """
    found: set[str] = set()
    # A placebo's pseudo-organizations (qt-a, qt-b) are halves of a real one's corpus.
    sources = {org if (corpus / org).is_dir() else org.rsplit("-", 1)[0] for org in orgs}
    missing = [s for s in sources if not (corpus / s / "examples").is_dir()]
    if missing:
        raise SystemExit(f"no built examples for {missing} under {corpus}")
    for org in sorted(sources):
        for path in sorted((corpus / org / "examples").glob("*.jsonl")):
            for line in path.read_text().splitlines():
                if not line:
                    continue
                row = json.loads(line)
                comments = row.get("comments") or []
                if comments and all(matched_bot(c) is not None for c in comments):
                    found.add(row["id"])
    return found


def main() -> None:
    args = parser.parse_args()
    results, seeds = merge(args.runs)
    orgs = sorted({key.split("|")[1] for key in results if key.startswith("base|")})
    bots = automated_ids(args.corpus, orgs)
    filtered = {key: [r for r in rows if r["id"] not in bots] for key, rows in results.items()}
    removed = {
        org: sum(1 for r in results[f"base|{org}"] if r["id"] in bots) / len(results[f"base|{org}"])
        for org in orgs
    }
    readings = {}
    for name, rows in (("registered", results), ("without_automated", filtered)):
        outcome = crossed_gate(rows, orgs=orgs, seeds=seeds, bootstrap_seed=args.bootstrap_seed)
        readings[name] = outcome
        print(f"{name}: {outcome['verdict']}", flush=True)
        for org, cell in outcome["per_org"].items():
            print(f"  {org}: {cell['estimate']:+.4f} [{cell['low']:+.4f}, {cell['high']:+.4f}]")
    print({org: f"{share:.3f} of held-out examples removed" for org, share in removed.items()})
    args.out.write_text(
        json.dumps(
            {
                "runs": [str(p) for p in args.runs],
                "seeds": list(seeds),
                "bot_registry": registry_digest(),
                "removed_share": removed,
                "readings": readings,
                "provenance": provenance_header(),
            },
            indent=2,
        )
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
