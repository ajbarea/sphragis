"""Last structural difference: gradient accumulation. And whether the LR is simply too high."""
import json, sys
from pathlib import Path
import torch
from peft import get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer
from sphragis.experiment.model import LORA, MODEL_ID, TRAINING
from sphragis.experiment.runner import build_prompt
from sphragis.experiment.training import build_supervised

rows = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines() if line]
tok = AutoTokenizer.from_pretrained(MODEL_ID)
if tok.pad_token_id is None:
    tok.pad_token = tok.eos_token
items = []
for r in rows:
    try:
        items.append(build_supervised(tok, r, prompt_builder=build_prompt,
                                      max_length=TRAINING["max_seq_length"]))
    except ValueError:
        pass

def collate(batch):
    n = max(len(b["input_ids"]) for b in batch)
    pad = tok.pad_token_id
    return {
        "input_ids": torch.tensor([b["input_ids"] + [pad]*(n-len(b["input_ids"])) for b in batch]).cuda(),
        "labels": torch.tensor([b["labels"] + [-100]*(n-len(b["labels"])) for b in batch]).cuda(),
        "attention_mask": torch.tensor([b["attention_mask"] + [0]*(n-len(b["attention_mask"])) for b in batch]).cuda(),
    }

base = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.bfloat16, device_map="cuda:0")

for lr in (2e-4, 5e-5):
    model = get_peft_model(base, LORA); model.train()
    trainable = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(trainable, lr=lr)
    bad, losses = 0, []
    micro = 0
    for opt_step in range(10):                      # 10 optimiser steps
        opt.zero_grad()
        for _ in range(8):                          # accumulation 8, as Trainer used
            batch = collate(items[(micro*2) % len(items):(micro*2) % len(items)+2])
            micro += 1
            loss = model(**batch).loss / 8
            if not torch.isfinite(loss):
                print(f"  [lr={lr:g}] opt_step {opt_step}: NON-FINITE loss"); bad += 1; break
            loss.backward()
        if bad: break
        gn = torch.nn.utils.clip_grad_norm_(trainable, 1.0)
        losses.append(float(loss) * 8)
        if not torch.isfinite(gn):
            print(f"  [lr={lr:g}] opt_step {opt_step}: NON-FINITE grad_norm after accumulation"); bad += 1; break
        opt.step()
    print(f"[lr={lr:g}, accum=8] non-finite: {bad}  losses: {[f'{x:.3f}' for x in losses[:6]]}")
    # reset the adapter for the next condition
    model.unload()
print("DIAG3_OK")
