"""Sweep EVERY item individually. The earlier diagnostic only checked the first 24."""
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
items, meta = [], []
for r in rows:
    try:
        items.append(build_supervised(tok, r, prompt_builder=build_prompt,
                                      max_length=TRAINING["max_seq_length"]))
        meta.append(r)
    except ValueError:
        pass

base = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.bfloat16, device_map="cuda:0")
model = get_peft_model(base, LORA)
model.train()
trainable = [p for p in model.parameters() if p.requires_grad]

bad = []
for idx, item in enumerate(items):
    for p in trainable:
        p.grad = None
    batch = {k: torch.tensor([v]).cuda() for k, v in item.items()}
    loss = model(**batch).loss
    ok_loss = bool(torch.isfinite(loss))
    loss.backward()
    gn = torch.nn.utils.clip_grad_norm_(trainable, 1e9)   # measure, do not clip
    ok_grad = bool(torch.isfinite(gn))
    if not (ok_loss and ok_grad):
        sup = sum(1 for x in item["labels"] if x != -100)
        bad.append((idx, float(loss) if ok_loss else float("nan"), float(gn), len(item["input_ids"]), sup))
        print(f"  item {idx}: loss={'nan' if not ok_loss else f'{float(loss):.3f}'} "
              f"grad_norm={float(gn):.3e} len={len(item['input_ids'])} sup={sup}")
        print(f"     before={meta[idx]['before'][:60]!r}")
        print(f"     after ={meta[idx]['after'][:60]!r}")
print(f"\nitems swept: {len(items)}   non-finite: {len(bad)}")
if not bad:
    print("no single item is non-finite on a fresh adapter; the instability accumulates")
print("DIAG4_OK")
