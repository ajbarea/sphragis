"""Distil the rules files the rules-file comparator prompts with, by the base model, greedy.

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
    pools,
    read_halves,
    trainable,
)
from sphragis.experiment.rules import distil, pinned, pipeline, review_text

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", choices=["reviews", "guide"], required=True)
parser.add_argument("--org", required=True)
parser.add_argument("--partition", type=int, help="with --source reviews: the partition seed")
parser.add_argument("--results", type=Path, help="with --source reviews: the adapters' runs")
parser.add_argument("--guides", type=Path, help="with --source guide: the guides' snapshot")
parser.add_argument("--out", type=Path, required=True)


def paragraphs(text: str) -> list[str]:
    """A guide's paragraphs, blank-line separated, the units its chunks are packed from."""
    return [block.strip() for block in text.split("\n\n") if block.strip()]


def sources_of(args: argparse.Namespace, tokenizer: Any) -> tuple[dict, dict[str, dict]]:
    """What the job distils, checked before any model loads: the report's head, and per file
    its kind, its sources and what they are recorded as."""
    head: dict[str, Any] = {"source": args.source, "org": args.org}
    if args.source == "reviews":
        listing = json.loads((args.results / f"admissible-partitions-{args.org}.json").read_text())
        order, size = first_partitions(listing, org=args.org)
        root, trained, recorded = corpus_of(args.results, args.org, args.partition, order, size)
        train, _, corpora = read_halves(root, args.org, recorded)
        cut = pools(train, size=size, fits=trainable(tokenizer))
        pooled = {half: len(index.pool) for half, index in cut.items()}
        if pooled != trained:
            raise ValueError(f"pools hold {pooled} rows, the adapters trained on {trained}")
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

    from sphragis.experiment.model import MODEL_ID, HFGenerator, _require_tokenizer, revision

    # Only the tokenizer until the data has passed every check: the model loads after.
    try:
        report, plan = sources_of(args, _require_tokenizer(MODEL_ID))
    except ValueError as error:
        raise SystemExit(str(error)) from error

    generator = HFGenerator()
    tokenizer = generator.tokenizer
    signature = f"{generator.model_id}@{revision(generator.model_id)}|{generator.computed_dtype}"

    def length(text: str) -> int:
        return len(tokenizer(text, add_special_tokens=False)["input_ids"])

    def generate(prompt: str, max_new_tokens: int) -> tuple[str, bool]:
        generator.max_new_tokens = max_new_tokens
        text = generator.generate(prompt)
        return text, generator.last_capped

    # Each file is kept beside the result as it is made, under a fingerprint of what made it, so
    # a refusal or a wall clock after one file does not cost the files already distilled.
    report["files"] = {}
    for name, item in plan.items():
        cache = args.out.with_name(f"{args.out.stem}.{name}.part.json")
        mark = fingerprint(signature, json.dumps([item, pipeline()], sort_keys=True))
        try:
            made = json.loads(cache.read_text()) if cache.is_file() else None
        except json.JSONDecodeError:
            # A write a kill cut short: made again.
            made = None
        if made is None or made.get("fingerprint") != mark:
            try:
                made = distil(item["sources"], kind=item["kind"], generate=generate, length=length)
            except ValueError as error:
                raise SystemExit(f"{name}: {error}") from error
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
    report["pipeline"] = pipeline()
    report["provenance"] = run_provenance()
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    for name in plan:
        args.out.with_name(f"{args.out.stem}.{name}.part.json").unlink(missing_ok=True)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
