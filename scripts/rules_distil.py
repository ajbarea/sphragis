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
import json
from pathlib import Path
from typing import Any

from sphragis.experiment.retrieval import (
    corpus_of,
    first_partitions,
    pools,
    read_halves,
    trainable,
)
from sphragis.experiment.rules import distil, review_text

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


def main() -> None:
    args = parser.parse_args()
    if args.source == "reviews" and (args.partition is None or args.results is None):
        raise SystemExit("--source reviews needs --partition and --results")
    if args.source == "guide" and args.guides is None:
        raise SystemExit("--source guide needs --guides")

    from sphragis.experiment.model import HFGenerator, revision

    generator = HFGenerator()
    tokenizer = generator.tokenizer

    def length(text: str) -> int:
        return len(tokenizer(text, add_special_tokens=False)["input_ids"])

    def generate(prompt: str, max_new_tokens: int) -> str:
        generator.max_new_tokens = max_new_tokens
        return generator.generate(prompt)

    report: dict[str, Any] = {"source": args.source, "org": args.org, "files": {}}
    try:
        if args.source == "reviews":
            listing = json.loads(
                (args.results / f"admissible-partitions-{args.org}.json").read_text()
            )
            order, size = first_partitions(listing, org=args.org)
            root, trained, recorded = corpus_of(args.results, args.org, args.partition, order, size)
            train, _, corpora = read_halves(root, args.org, recorded)
            cut = pools(train, size=size, fits=trainable(tokenizer))
            pooled = {half: len(index.pool) for half, index in cut.items()}
            if pooled != trained:
                raise ValueError(f"pools hold {pooled} rows, the adapters trained on {trained}")
            report |= {"partition": args.partition, "train_size": size, "corpora": corpora}
            for half, index in cut.items():
                sources = [review_text(row) for row in index.pool]
                report["files"][half] = {
                    "pool_ids": [row["id"] for row in index.pool],
                    **distil(sources, kind="reviews", generate=generate, length=length),
                }
                print(f"{half}: {len(report['files'][half]['rules'])} rules", flush=True)
        else:
            guide = json.loads((args.guides / f"guide-{args.org}.json").read_text())
            sources = [p for source in guide["sources"] for p in paragraphs(source["text"])]
            report["guide"] = [
                {key: source[key] for key in ("title", "revision", "sha256")}
                for source in guide["sources"]
            ]
            report["files"][args.org] = distil(
                sources, kind="guide", generate=generate, length=length
            )
            print(f"{args.org}: {len(report['files'][args.org]['rules'])} rules", flush=True)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    from sphragis.experiment.model import run_provenance

    report["generator"] = f"{generator.model_id}@{revision(generator.model_id)}"
    report["inference_dtype"] = generator.computed_dtype
    report["provenance"] = run_provenance()
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
