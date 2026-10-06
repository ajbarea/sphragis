"""Distil the rules files the rules-file comparator prompts with, by the pinned distiller, greedy.

`--source reviews`: one admissible partition of an organization, each half's file distilled from
its pool, the rows its adapter trains on (`retrieval.pools`, on the corpus that adapter's run read).
`--source guide`: the organization's written conventions, from `rules_guides.py`'s snapshot,
split into paragraphs. Both go through `rules.distil`, the one map and reduce pipeline. Every map
list and the reduce answer are kept beside the file. Exploratory, outside the registered cells.

    uv run --no-sync python scripts/rules_distil.py --source reviews --org openstack \\
        --partition 2 --results datasets/results --out rules-reviews-openstack-p2.json
    uv run --no-sync python scripts/rules_distil.py --source guide --org openstack \\
        --guides datasets/rules --out rules-guide-openstack.json

Run on the cluster: scripts/rules_distil.sbatch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from sphragis.experiment.retrieval import (
    corpus_of,
    fingerprint,
    first_partitions,
    half_pools,
    trainable,
)
from sphragis.experiment.rules import (
    distil,
    distiller_signature,
    paragraphs,
    pinned,
    pipeline,
    review_text,
)
from sphragis.refusal import refusals

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", choices=["reviews", "guide"], required=True)
parser.add_argument("--org", required=True)
parser.add_argument("--partition", type=int, help="with --source reviews: the partition seed")
parser.add_argument("--results", type=Path, help="with --source reviews: the adapters' runs")
parser.add_argument("--guides", type=Path, help="with --source guide: the guides' snapshot")
parser.add_argument("--out", type=Path, required=True)


def sources_of(args: argparse.Namespace, tokenizer: Any) -> tuple[dict, dict[str, dict]]:
    """What the job distils, checked before any model loads: the report's head, and per file
    its kind, its sources and what they are recorded as. Pools are cut by `tokenizer`, the
    evaluated model's, as its adapters' training sets were."""
    head: dict[str, Any] = {"source": args.source, "org": args.org}
    if args.source == "reviews":
        listing = json.loads((args.results / f"admissible-partitions-{args.org}.json").read_text())
        order, size = first_partitions(listing, org=args.org)
        root, trained, recorded = corpus_of(args.results, args.org, args.partition, order, size)
        cut, _, corpora = half_pools(root, args.org, size, trainable(tokenizer), recorded, trained)
        head |= {"partition": args.partition, "train_size": size, "corpora": corpora}
        return head, {
            half: {
                "kind": "reviews",
                "sources": [review_text(row) for row in index.pool],
                "pool_ids": [row["id"] for row in index.pool],
            }
            for half, index in cut.items()
        }
    guide = json.loads((args.guides / f"guide-{args.org}.json").read_text())
    # The text distilled is the text the snapshot pinned, or the file claims a hash it is not.
    for source in guide["sources"]:
        if hashlib.sha256(source["text"].encode()).hexdigest() != source["sha256"]:
            raise ValueError(f"{source['title']}: its text does not match its recorded sha256")
    head["guide"] = pinned(guide)
    sources = [p for source in guide["sources"] for p in paragraphs(source["text"])]
    return head, {args.org: {"kind": "guide", "sources": sources}}


def main() -> None:
    args = parser.parse_args()
    if args.source == "reviews" and (args.partition is None or args.results is None):
        raise SystemExit("--source reviews needs --partition and --results")
    if args.source == "guide" and args.guides is None:
        raise SystemExit("--source guide needs --guides")

    from sphragis.experiment.model import (
        DISTILLER_DTYPE,
        DISTILLER_ID,
        DISTILLER_TEMPLATE,
        MODEL_ID,
        HFGenerator,
        _require_tokenizer,
        pinned_id,
    )

    # Only the tokenizers until the data has passed every check: the model loads after.
    evaluated = _require_tokenizer(MODEL_ID)
    with refusals():
        report, plan = sources_of(args, evaluated)

    # The distiller loads only when a file is not already cached under this signature, so a
    # resubmitted job whose files were all made spends no model load.
    signature = distiller_signature(pinned_id(DISTILLER_ID), DISTILLER_DTYPE, DISTILLER_TEMPLATE)
    distiller_tokens = _require_tokenizer(DISTILLER_ID)
    loaded: list[Any] = []

    def length(text: str) -> int:
        return len(distiller_tokens(text, add_special_tokens=False)["input_ids"])

    budgeted_by = pinned_id(MODEL_ID)

    def file_length(text: str) -> int:
        return len(evaluated(text, add_special_tokens=False)["input_ids"])

    def generate(prompt: str, max_new_tokens: int) -> tuple[str, bool]:
        if not loaded:
            loaded.append(
                HFGenerator(
                    model_id=DISTILLER_ID, dtype=DISTILLER_DTYPE, template=DISTILLER_TEMPLATE
                )
            )
            if str(loaded[0].computed_dtype) != str(DISTILLER_DTYPE):
                raise SystemExit(f"the distiller loaded in {loaded[0].computed_dtype}")
        generator = loaded[0]
        generator.max_new_tokens = max_new_tokens
        return generator.generate(prompt), generator.last_capped

    # Each file is kept beside the result as it is made, under a fingerprint of what made it, so
    # a refusal or a wall clock after one file does not cost the files already distilled.
    report["files"] = {}
    for name, item in plan.items():
        cache = args.out.with_name(f"{args.out.stem}.{name}.part.json")
        mark = fingerprint(signature, json.dumps([item, pipeline(), budgeted_by], sort_keys=True))
        try:
            made = json.loads(cache.read_text()) if cache.is_file() else None
        except json.JSONDecodeError:
            # A write a kill cut short: made again.
            made = None
        if made is None or made.get("fingerprint") != mark:
            with refusals(f"{name}: "):
                made = distil(
                    item["sources"],
                    kind=item["kind"],
                    generate=generate,
                    length=length,
                    file_length=file_length,
                )
            made["fingerprint"] = mark
            partial = cache.with_suffix(".partial")
            partial.write_text(json.dumps(made, ensure_ascii=False) + "\n")
            os.replace(partial, cache)
        report["files"][name] = {
            **({"pool_ids": item["pool_ids"]} if "pool_ids" in item else {}),
            **made,
        }
        print(f"{name}: {len(made['rules'])} rules, capped maps {sum(made['map_capped'])}")

    from sphragis.experiment.model import run_provenance

    report["generator"] = signature
    # The model whose tokens the file budget is counted in: the one that reads the file.
    report["budgeted_by"] = budgeted_by
    report["pipeline"] = pipeline()
    report["provenance"] = run_provenance()
    # Whole or not at all, so a rules job never reads a file half written.
    partial = args.out.with_suffix(".partial")
    partial.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    os.replace(partial, args.out)
    for name in plan:
        args.out.with_name(f"{args.out.stem}.{name}.part.json").unlink(missing_ok=True)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
