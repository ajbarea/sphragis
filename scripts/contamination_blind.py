"""Outcome-neutral test 1, read against blind baselines: do the windows separate without the model?

Reads a contamination battery result and the two window files it scored, rebuilds each scored
example's text the way the battery did, and measures how well the windows separate by a bag of
words (naive Bayes, folds grouped by change) and by the latest year in the text, beside the
battery's own membership scores on the same examples. Every separation is an AUC that a
post-cutoff example looks more post-cutoff than a pre-cutoff one, with intervals resampling
whole changes. No model is loaded.

    uv run --no-sync --no-active python scripts/contamination_blind.py \\
        --battery datasets/results/contamination-openstack-6mo-with_context-gapk.json \\
        --post <openstack-post.jsonl> --pre <openstack-pre.jsonl> --legacy-corpus \\
        --out datasets/results/contamination-openstack-6mo-blind.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median

from sphragis.corpus.load import derived_file_rows
from sphragis.corpus.pipeline import run_dedup
from sphragis.corpus.redact import redact_text
from sphragis.measure.blind import auc, clustered_separation, latest_year, repeated_out_of_fold
from sphragis.measure.contamination import scored_text
from sphragis.provenance import provenance_header

MEMBERSHIP = ("min_k_plus_plus", "min_k_percent", "gap_k_percent")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--battery", type=Path, required=True, help="contamination battery result")
parser.add_argument("--post", type=Path, required=True, help="post-cutoff window the battery read")
parser.add_argument("--pre", type=Path, required=True, help="pre-cutoff window the battery read")
parser.add_argument(
    "--legacy-corpus",
    action="store_true",
    help="read window files written before records existed, as the battery that scored them did",
)
parser.add_argument("--folds", type=int, default=5)
parser.add_argument("--seed", type=int, default=0, help="the bootstrap's seed")
parser.add_argument(
    "--fold-seeds", type=int, default=20, help="fold draws averaged: seeds 0 to this minus one"
)
parser.add_argument("--resamples", type=int, default=2_000)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()

battery = json.loads(args.battery.read_text())
mode = battery["scored_text"]
# A committed result carries its ids redacted, so the window rows are keyed the same way. Each
# window is deduplicated as the battery did, so an id it saw twice resolves to the copy it kept.
rows = {}
for path in (args.post, args.pre):
    kept, _ = run_dedup(derived_file_rows(path, legacy=args.legacy_corpus))
    for row in kept:
        key = redact_text(row["id"])
        if key in rows:
            raise SystemExit(f"{key} is in both windows or twice after deduplication")
        rows[key] = row
scored = battery["scores"]
missing = [s["id"] for s in scored if s["id"] not in rows]
if missing:
    raise SystemExit(
        f"{len(missing)} scored examples are in neither window file, e.g. {missing[0]}"
    )

labels = [1 if s["window"] == "post" else 0 for s in scored]
changes = [str(rows[s["id"]]["change_id"]) for s in scored]
projects = [str(rows[s["id"]]["project"]) for s in scored]
try:
    texts = [scored_text(rows[s["id"]], mode) for s in scored]
except ValueError as error:
    raise SystemExit(str(error)) from None
seeds = list(range(args.fold_seeds))


def bag_of_words(groups: list[str]) -> tuple[list[float], dict[str, float]]:
    """Scores averaged over the fold seeds, and the spread of the single-seed AUCs."""
    mean, runs = repeated_out_of_fold(texts, labels, groups, folds=args.folds, seeds=seeds)
    per_seed = sorted(auc(run, labels) for run in runs)
    spread = {"median": median(per_seed), "min": per_seed[0], "max": per_seed[-1]}
    return mean, spread


by_change, spread_change = bag_of_words(changes)
by_project, spread_project = bag_of_words(projects)
membership = {name: [-float(s[name]) for s in scored] for name in MEMBERSHIP}
# A membership score is member-like when high, and a member is pre-cutoff: negate to post-likeness.
arms = {
    "bag_of_words": by_change,
    "latest_year": [float(latest_year(t)) for t in texts],
    **membership,
}
separation = clustered_separation(arms, labels, changes, seed=args.seed, resamples=args.resamples)
# Sensitivity: folds and resampling by project, so no project's own vocabulary carries the
# classifier across windows that hold different projects.
by_project_separation = clustered_separation(
    {"bag_of_words": by_project, "min_k_plus_plus": membership["min_k_plus_plus"]},
    labels,
    projects,
    seed=args.seed,
    resamples=args.resamples,
)
dated = sum(1 for text in texts if latest_year(text))
one_window = {
    window: len(
        {p for p, label in zip(projects, labels, strict=True) if label == side}
        - {p for p, label in zip(projects, labels, strict=True) if label != side}
    )
    for window, side in (("post", 1), ("pre", 0))
}
args.out.write_text(
    json.dumps(
        {
            "provenance": provenance_header(),
            "battery": str(args.battery),
            "scored_text": mode,
            "legacy_corpus": args.legacy_corpus,
            "examples": {"post": sum(labels), "pre": len(labels) - sum(labels)},
            "changes": len(set(changes)),
            "projects": len(set(projects)),
            "projects_in_one_window_only": one_window,
            "with_a_year": dated,
            "folds": args.folds,
            "seed": args.seed,
            "fold_seeds": seeds,
            "resamples": args.resamples,
            "direction": "AUC: a post-cutoff example looks more post-like than a pre-cutoff one",
            "separation": separation,
            "bag_of_words_single_seed_auc": spread_change,
            "by_project": {
                "separation": by_project_separation,
                "bag_of_words_single_seed_auc": spread_project,
            },
        },
        indent=2,
    )
    + "\n"
)
for name, value in separation.items():
    print(f"{name:40s} {value['estimate']:+.3f} [{value['low']:+.3f}, {value['high']:+.3f}]")
