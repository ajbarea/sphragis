"""Pilot-scale RQ1: does the matched adapter beat the mismatched one, per organization?

The 2 by 2 grid plus the base arm, on one month per organization, through the same `walk`
and `gate` the confirmatory run will use. It proves the grid executes end to end on real
weights, which the Stage 1 checklist asks for, and gives a first look at the contrast. It
is not the confirmatory result: one month, held out by change rather than by time, and the
test windows stay sealed.
"""

import argparse
import gc
import json
from pathlib import Path

import torch

from sphragis.corpus.pipeline import run_dedup
from sphragis.experiment.holdout import equalize_training, holdout_by_change, verbatim_overlap
from sphragis.experiment.model import (
    MAX_NEW_TOKENS,
    MODEL_ID,
    TRAINING,
    HFGenerator,
    attach_adapter,
    train_adapter,
)
from sphragis.experiment.runner import build_prompt
from sphragis.experiment.training import build_supervised
from sphragis.experiment.walk import gate, walk

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--corpus", action="append", required=True, metavar="ORG=PATH", help="one per organization"
)
parser.add_argument("--seeds", default="1", help="comma-separated; an odd count")
parser.add_argument("--split-seed", type=int, default=0)
parser.add_argument("--bootstrap-seed", type=int, default=7)
parser.add_argument("--adapters", type=Path, default=Path("adapters"))
parser.add_argument("--out", type=Path, default=Path("rq1-pilot.json"))
parser.add_argument(
    "--equalize-train",
    action="store_true",
    help="subsample every organization's training set to the smallest one's size",
)
parser.add_argument("--max-new-tokens", type=int, default=MAX_NEW_TOKENS)
args = parser.parse_args()

seeds = tuple(int(s) for s in args.seeds.split(","))
corpora = dict(entry.split("=", 1) for entry in args.corpus)
orgs = tuple(sorted(corpora))

train_rows: dict[str, list[dict]] = {}
held_out: dict[str, list[dict]] = {}
summary: dict[str, dict] = {}
for org, path in corpora.items():
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line]
    kept, removed = run_dedup(rows)
    train, test = holdout_by_change(kept, seed=args.split_seed)
    leaked = verbatim_overlap(train, test)
    assert not leaked, f"{org}: {len(leaked)} held-out examples repeat a training pair"
    train_rows[org], held_out[org] = train, test
    summary[org] = {
        "examples": len(rows),
        "dedup_removed": removed,
        "train_examples": len(train),
        "held_out_examples": len(test),
        "held_out_changes": len({r["change_id"] for r in test}),
    }
    print(f"{org}: {summary[org]}", flush=True)

if args.equalize_train:
    train_rows = equalize_training(train_rows, seed=args.split_seed)
    for org in orgs:
        summary[org]["train_examples_equalized"] = len(train_rows[org])
    print(f"equalized training sets: { {o: len(train_rows[o]) for o in orgs} }", flush=True)


class InProcessTrainer:
    """Trains one adapter, saves it, and releases the GPU before returning its path."""

    def __init__(self) -> None:
        self.reports: dict[str, dict] = {}

    def train(self, org: str, seed: int) -> str:
        model, tok = attach_adapter(MODEL_ID, seed)
        if tok.pad_token_id is None:
            tok.pad_token = tok.eos_token
        items, refused = [], 0
        for r in train_rows[org]:
            try:
                items.append(
                    build_supervised(
                        tok, r, prompt_builder=build_prompt, max_length=TRAINING["max_seq_length"]
                    )
                )
            except ValueError:
                refused += 1
        report = train_adapter(model, items, pad_token_id=tok.pad_token_id, seed=seed)
        assert report.skipped_steps == 0, f"{org} s{seed}: {report.skipped_steps} steps skipped"
        target = args.adapters / f"{org}-s{seed}"
        model.save_pretrained(target)
        self.reports[f"{org}-s{seed}"] = {
            "items": len(items),
            "refused": refused,
            "steps": report.steps,
            "applied_steps": report.applied_steps,
            "first_loss": report.losses[0],
            "last_loss": report.losses[-1],
        }
        print(f"trained {target}: {self.reports[f'{org}-s{seed}']}", flush=True)
        del model
        gc.collect()
        torch.cuda.empty_cache()
        return str(target)


def generator_for(adapter: str | None) -> HFGenerator:
    gc.collect()
    torch.cuda.empty_cache()
    print(f"evaluating with {adapter or 'base'}", flush=True)
    return HFGenerator(adapter_path=adapter, max_new_tokens=args.max_new_tokens)


trainer = InProcessTrainer()
results = walk(
    orgs=orgs, seeds=seeds, windows=held_out, trainer=trainer, generator_for=generator_for
)
for run, rows in results.items():
    n = len(rows)
    print(
        f"{run:<28} EM={sum(r['exact_match'] for r in rows) / n:.3f} "
        f"normEM={sum(r['normalized_exact_match'] for r in rows) / n:.3f} "
        f"sim={sum(r['edit_similarity'] for r in rows) / n:.3f}",
        flush=True,
    )

verdict = gate(results, orgs=orgs, seeds=seeds, bootstrap_seed=args.bootstrap_seed)
for org, interval in verdict["binding"].items():
    print(
        f"RQ1 {org}: matched - mismatched EM {interval['estimate']:+.3f} "
        f"[{interval['low']:+.3f}, {interval['high']:+.3f}] "
        f"over {interval['clusters']:.0f} changes",
        flush=True,
    )
print(f"GATE (pilot scale, not confirmatory): {verdict['verdict']}", flush=True)

args.out.write_text(
    json.dumps(
        {
            "model_id": MODEL_ID,
            "seeds": seeds,
            "split_seed": args.split_seed,
            "equalize_train": args.equalize_train,
            "bootstrap_seed": args.bootstrap_seed,
            "max_new_tokens": args.max_new_tokens,
            "corpora": summary,
            "training": trainer.reports,
            "verdict": verdict,
            "results": results,
        },
        indent=2,
        default=str,
    )
)
print(f"wrote {args.out}")
print("RQ1_PILOT_OK")
