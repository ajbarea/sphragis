"""Pilot: does an ADAPTED 7B clear the exact-match floor on this corpus?

The question the whole Stage 1 protocol rests on. Base model scores EM 0 on real hunks
(expected, matches a base model zero-shot). CodeReviewer reports 30.32% once fine-tuned.
This asks whether adaptation moves it here.
"""

import contextlib
import json
import random
import sys
import time
from pathlib import Path

import torch
from peft import get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer

from sphragis.experiment.model import LORA, MODEL_ID, TRAINING, train_adapter
from sphragis.experiment.runner import build_prompt
from sphragis.experiment.training import build_supervised
from sphragis.measure.score import score

EXAMPLES = Path(sys.argv[1] if len(sys.argv) > 1 else "pilot-examples.jsonl")
rows = [json.loads(line) for line in EXAMPLES.read_text().splitlines() if line]

# Split by CHANGE, never by example. Consecutive hunks of one change are near-copies, so a
# random split puts siblings on both sides: measured at 63% of eval sharing a change_id
# with train, and 20% sharing an exact `before` text, which inflated exact match from an
# honest number to 0.512. This is the same grouping rule sphragis.corpus.split enforces for
# the real windows.
changes = sorted({r["change_id"] for r in rows})
random.Random(0).shuffle(changes)
cut = int(0.8 * len(changes))
train_ids, eval_ids = set(changes[:cut]), set(changes[cut:])
train_rows = [r for r in rows if r["change_id"] in train_ids]
eval_rows = [r for r in rows if r["change_id"] in eval_ids]
assert not (train_ids & eval_ids), "a change cannot appear on both sides"
print(f"changes {len(changes)}: {len(train_ids)} train / {len(eval_ids)} eval", flush=True)
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


def collate(batch):
    n = max(len(b["input_ids"]) for b in batch)
    pad = tok.pad_token_id
    return {
        "input_ids": torch.tensor(
            [b["input_ids"] + [pad] * (n - len(b["input_ids"])) for b in batch]
        ),
        "labels": torch.tensor([b["labels"] + [-100] * (n - len(b["labels"])) for b in batch]),
        "attention_mask": torch.tensor(
            [b["attention_mask"] + [0] * (n - len(b["attention_mask"])) for b in batch]
        ),
    }


def evaluate_em(model, label):
    model.eval()
    tot = {"exact_match": 0.0, "normalized_exact_match": 0.0, "edit_similarity": 0.0}
    lens = []
    t0 = time.time()
    for r in eval_rows:
        ids = tok(build_prompt(r), return_tensors="pt").to("cuda:0")
        with torch.inference_mode():
            out = model.generate(
                **ids, max_new_tokens=96, do_sample=False, pad_token_id=tok.eos_token_id
            )
        pred = tok.decode(out[0][ids["input_ids"].shape[-1] :], skip_special_tokens=True)
        s = score(pred, str(r["after"]))
        lens.append(len(pred))
        for k in tot:
            tot[k] += s[k]
    n = max(len(eval_rows), 1)
    med_pred = sorted(lens)[len(lens) // 2] if lens else 0
    med_ref = sorted(len(str(r["after"])) for r in eval_rows)[len(eval_rows) // 2]
    print(
        f"[{label}] EM={tot['exact_match'] / n:.3f} "
        f"normEM={tot['normalized_exact_match'] / n:.3f} "
        f"sim={tot['edit_similarity'] / n:.3f} "
        f"pred_chars_med={med_pred} ref_chars_med={med_ref}  ({time.time() - t0:.0f}s)",
        flush=True,
    )
    return tot["exact_match"] / n


base = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.bfloat16, device_map="cuda:0")
print("loaded base", flush=True)
em_base = evaluate_em(base, "BASE")

model = get_peft_model(base, LORA)
model.print_trainable_parameters()

losses = train_adapter(model, train_ds, pad_token_id=tok.pad_token_id, seed=1)
print(f"losses: {[f'{x:.3f}' for x in losses]}", flush=True)
assert all(x == x for x in losses), "non-finite loss slipped through"
print("training done", flush=True)
em_adapted = evaluate_em(model, "ADAPTED")

print(f"\nFLOOR CHECK: base EM {em_base:.3f} -> adapted EM {em_adapted:.3f}")
print("PILOT_OK")
