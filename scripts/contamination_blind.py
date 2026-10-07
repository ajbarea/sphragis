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

from sphragis.corpus.load import derived_file_rows
from sphragis.corpus.redact import redact_text
from sphragis.measure.blind import clustered_separation, latest_year, out_of_fold
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
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--resamples", type=int, default=2_000)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()

battery = json.loads(args.battery.read_text())
mode = battery["scored_text"]
# A committed result carries its ids redacted, so the window rows are keyed the same way.
rows = {}
for path in (args.post, args.pre):
    for row in derived_file_rows(path, legacy=args.legacy_corpus):
        rows[redact_text(row["id"])] = row
scored = battery["scores"]
missing = [s["id"] for s in scored if s["id"] not in rows]
if missing:
    raise SystemExit(
        f"{len(missing)} scored examples are in neither window file, e.g. {missing[0]}"
    )

labels = [1 if s["window"] == "post" else 0 for s in scored]
groups = [str(rows[s["id"]]["change_id"]) for s in scored]
texts = [scored_text(rows[s["id"]], mode) for s in scored]
# A membership score is member-like when high, and a member is pre-cutoff: negate to post-likeness.
arms = {
    "bag_of_words": out_of_fold(texts, labels, groups, folds=args.folds, seed=args.seed),
    "latest_year": [float(latest_year(text)) for text in texts],
    **{name: [-float(s[name]) for s in scored] for name in MEMBERSHIP},
}
separation = clustered_separation(arms, labels, groups, seed=args.seed, resamples=args.resamples)
dated = sum(1 for text in texts if latest_year(text))
args.out.write_text(
    json.dumps(
        {
            "provenance": provenance_header(),
            "battery": str(args.battery),
            "scored_text": mode,
            "legacy_corpus": args.legacy_corpus,
            "examples": {"post": sum(labels), "pre": len(labels) - sum(labels)},
            "changes": len(set(groups)),
            "with_a_year": dated,
            "folds": args.folds,
            "seed": args.seed,
            "resamples": args.resamples,
            "direction": "AUC: a post-cutoff example looks more post-like than a pre-cutoff one",
            "separation": separation,
        },
        indent=2,
    )
    + "\n"
)
for name, value in separation.items():
    print(f"{name:40s} {value['estimate']:+.3f} [{value['low']:+.3f}, {value['high']:+.3f}]")
