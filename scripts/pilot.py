"""Pilot: does an ADAPTED 7B clear the exact-match floor on this corpus?

The question the whole Stage 1 protocol rests on. Base model scores EM 0 on real hunks
(expected, matches a base model zero-shot). CodeReviewer reports 30.32% once fine-tuned.
This asks whether adaptation moves it here.

Scoring and grouping go through `sphragis.measure` and `sphragis.experiment.runner`, so the
pilot exercises the same code the confirmatory grid will, and its per-change outcomes drop
straight into the cluster bootstrap. A hand-rolled scoring loop here would be a second
implementation of the binding metric.
"""

import argparse
import contextlib
import json
import time
from pathlib import Path
from statistics import median

import torch
from peft import get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer

from sphragis.corpus.load import derived_file_rows
from sphragis.corpus.pipeline import run_dedup
from sphragis.experiment.holdout import holdout_by_change, verbatim_overlap
from sphragis.experiment.model import (
    LORA,
    MAX_NEW_TOKENS,
    MODEL_ID,
    TRAINING,
    run_provenance,
    train_adapter,
)
from sphragis.experiment.runner import build_prompt, evaluate, to_clusters
from sphragis.experiment.training import build_supervised, render_chat
from sphragis.measure.stats import cluster_bootstrap

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("examples", nargs="?", type=Path, default=Path("pilot-examples.jsonl"))
parser.add_argument("--out", type=Path, default=Path("pilot-outcomes.json"))
parser.add_argument(
    "--legacy-corpus",
    action="store_true",
    help="read a file not cut under the current label rules, to reproduce an earlier result; "
    "recorded in the output",
)
parser.add_argument("--split-seed", type=int, default=0, help="which changes land in eval")
parser.add_argument("--train-seed", type=int, default=1, help="adapter initialisation + order")
parser.add_argument("--bootstrap-seed", type=int, default=7)
parser.add_argument("--max-new-tokens", type=int, default=MAX_NEW_TOKENS)
args = parser.parse_args()

rows = derived_file_rows(args.examples, legacy=args.legacy_corpus)

# Deduplicate FIRST, exactly as the real pipeline does. Skipping it measured the pilot
# under conditions the study will never reproduce: 27 of 201 OpenStack examples are
# duplicates, 13 of them landed in the held-out set at this split seed, and every one was
# a miss, so the reported figure was deflated against the study's own conditions.
rows, removed = run_dedup(rows)
print(f"dedup: {len(rows)} kept, removed {dict(removed)}", flush=True)

# Split by CHANGE, never by example. Consecutive hunks of one change are near-copies, so a
# random split puts siblings on both sides: measured at 63% of eval sharing a change_id
# with train, and 20% sharing an exact `before` text, which inflated exact match from an
# honest number to 0.512. This is the same grouping rule sphragis.corpus.split enforces for
# the real windows.
train_rows, eval_rows = holdout_by_change(rows, seed=args.split_seed)
changes = sorted({r["change_id"] for r in rows})
_leaked = verbatim_overlap(train_rows, eval_rows)
print(f"content overlap eval-vs-train: {len(_leaked)} of {len(eval_rows)}", flush=True)
assert not _leaked, f"{len(_leaked)} eval examples repeat a training pair verbatim"
print(
    f"changes {len(changes)}: {len({r['change_id'] for r in train_rows})} train / "
    f"{len({r['change_id'] for r in eval_rows})} eval",
    flush=True,
)
print(f"examples {len(rows)}  train {len(train_rows)}  eval {len(eval_rows)}", flush=True)

tok = AutoTokenizer.from_pretrained(MODEL_ID)
if tok.pad_token_id is None:
    tok.pad_token = tok.eos_token


def make(rs):
    out = []
    for r in rs:
        # An example whose target alone exceeds the budget cannot be trained on without
        # cutting the answer, so it is skipped rather than mangled.
        with contextlib.suppress(ValueError):
            out.append(
                build_supervised(
                    tok, r, prompt_builder=build_prompt, max_length=TRAINING["max_seq_length"]
                )
            )
    return out


train_ds = make(train_rows)
print(f"trainable items {len(train_ds)}", flush=True)


class LiveGenerator:
    """A `Generator` over the model already resident on the GPU.

    `HFGenerator` loads its own weights from a saved adapter; the pilot scores the same
    object before and after `get_peft_model` wraps it, so it needs a view rather than a
    second load.
    """

    def __init__(self, model):
        self.model = model

    def generate(self, prompt: str) -> str:
        self.model.eval()
        # render_chat, the function build_supervised trains on: the pilot scored both arms
        # on the raw prompt, which was out of distribution for the instruct base model and
        # flattered ADAPTED - BASE on every metric but exact match.
        ids = tok(render_chat(tok, prompt), return_tensors="pt", add_special_tokens=False).to(
            "cuda:0"
        )
        with torch.inference_mode():
            out = self.model.generate(
                **ids,
                max_new_tokens=args.max_new_tokens,
                do_sample=False,
                pad_token_id=tok.eos_token_id,
            )
        return str(tok.decode(out[0][ids["input_ids"].shape[-1] :], skip_special_tokens=True))


def arm(model, label: str) -> list[dict]:
    """Score one arm over the held-out changes and report the ladder."""
    t0 = time.time()
    results = evaluate(LiveGenerator(model), eval_rows)
    n = max(len(results), 1)

    def mean(key: str) -> float:
        return sum(float(r[key]) for r in results) / n

    print(
        f"[{label}] EM={mean('exact_match'):.3f} "
        f"normEM={mean('normalized_exact_match'):.3f} "
        f"sim={mean('edit_similarity'):.3f} "
        f"pred_chars_med={median(len(r['prediction']) for r in results):.0f} "
        f"ref_chars_med={median(len(str(r['after'])) for r in eval_rows):.0f}  "
        f"({time.time() - t0:.0f}s)",
        flush=True,
    )
    return results


base = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.bfloat16, device_map="cuda:0")
print("loaded base", flush=True)
base_results = arm(base, "BASE")

# Before get_peft_model, not after. train_adapter's own torch.manual_seed runs once the
# adapter already exists, so the LoRA init was drawn from whatever RNG state the base
# arm's generation left behind -- which depends on the eval set size. Two conditions
# differing only by seed have to differ only by seed.
torch.manual_seed(args.train_seed)
model = get_peft_model(base, LORA)
model.print_trainable_parameters()

report = train_adapter(model, train_ds, pad_token_id=tok.pad_token_id, seed=args.train_seed)
losses = report.losses
print(f"losses: {[f'{x:.3f}' for x in losses]}", flush=True)
print(
    f"steps {report.applied_steps} applied of {report.steps}, "
    f"{report.skipped_steps} skipped, {report.skipped_micro_batches} micro-batches skipped, "
    f"{report.examples_seen} example exposures",
    flush=True,
)
# A non-finite loss is skipped before it can reach `losses`, so checking `losses` for NaN
# proves nothing. The skip counters are where a diverging run is visible.
assert report.skipped_steps == 0, f"{report.skipped_steps} optimiser steps were skipped"
print("training done", flush=True)
adapted_results = arm(model, "ADAPTED")

# The interval is over CHANGES, not examples: sibling hunks of one change are not
# independent draws, so an example-level interval would be too narrow by construction.
clusters = to_clusters(adapted_results, base_results)
interval = cluster_bootstrap(clusters, seed=args.bootstrap_seed)
print(
    f"\nADAPTED - BASE exact match: {interval['estimate']:+.3f} "
    f"[{interval['low']:+.3f}, {interval['high']:+.3f}] "
    f"(95% cluster bootstrap over {len(clusters)} changes)",
    flush=True,
)

args.out.write_text(
    json.dumps(
        {
            "provenance": run_provenance(),
            "legacy_corpus": args.legacy_corpus,
            "model_id": MODEL_ID,
            "split_seed": args.split_seed,
            "train_seed": args.train_seed,
            "bootstrap_seed": args.bootstrap_seed,
            "n_changes": len(changes),
            "n_train_examples": len(train_ds),
            "n_eval_examples": len(eval_rows),
            "losses": losses,
            "steps": report.steps,
            "applied_steps": report.applied_steps,
            "skipped_steps": report.skipped_steps,
            "skipped_micro_batches": report.skipped_micro_batches,
            "dedup_removed": dict(removed),
            "interval": interval,
            "base": base_results,
            "adapted": adapted_results,
        },
        indent=2,
    )
)
print(f"wrote {args.out}", flush=True)
print("PILOT_OK")
