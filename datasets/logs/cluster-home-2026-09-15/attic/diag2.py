"""Isolate the difference: batch+padding, and autocast. Manual loop was stable at batch 1."""
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
print(f"pad_token_id={tok.pad_token_id} eos={tok.eos_token_id}")

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
model = get_peft_model(base, LORA); model.train()
opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=TRAINING["learning_rate"])

for label, bs, use_autocast in (("batch2-noautocast", 2, False), ("batch2-autocast", 2, True)):
    bad = 0
    for step in range(12):
        batch = collate(items[step*bs:(step+1)*bs])
        # A batch where every label is masked gives an undefined loss.
        sup = int((batch["labels"] != -100).sum())
        if use_autocast:
            with torch.autocast("cuda", dtype=torch.bfloat16):
                loss = model(**batch).loss
        else:
            loss = model(**batch).loss
        if not torch.isfinite(loss):
            print(f"  [{label}] step {step}: NON-FINITE loss, sup_tokens={sup}"); bad += 1; break
        loss.backward()
        gn = torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
        if not torch.isfinite(gn):
            print(f"  [{label}] step {step}: NON-FINITE grad_norm, loss={loss.item():.4f}, sup_tokens={sup}"); bad += 1; break
        opt.step(); opt.zero_grad()
    print(f"[{label}] non-finite events in 12 steps: {bad}")
print("DIAG2_OK")
