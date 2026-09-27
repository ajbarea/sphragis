"""Both configs broke at step 10 = cursor 160. Sweep PAIRS on a FRESH adapter: is it the
data pairing, or does it need the trained weights?"""
import json, sys
from pathlib import Path
import torch
from peft import get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer
from sphragis.experiment.model import LORA, MODEL_ID, TRAINING, _collate
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

base = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.bfloat16, device_map="cuda:0")
model = get_peft_model(base, LORA); model.train()
trainable = [p for p in model.parameters() if p.requires_grad]

print("sweeping every consecutive PAIR on a fresh adapter, no optimiser steps")
bad = []
for cursor in range(0, len(items) - 1, 2):
    for p in trainable:
        p.grad = None
    pair = items[cursor:cursor + 2]
    batch = _collate(pair, tok.pad_token_id)
    loss = model(**batch).loss
    ok_loss = bool(torch.isfinite(loss))
    if ok_loss:
        loss.backward()
        gn = float(torch.nn.utils.clip_grad_norm_(trainable, 1e9))
    else:
        gn = float("nan")
    if not ok_loss or gn != gn:
        lens = [len(i["input_ids"]) for i in pair]
        sup = [sum(1 for x in i["labels"] if x != -100) for i in pair]
        print(f"  cursor {cursor}: loss={'nan' if not ok_loss else f'{float(loss):.3f}'} "
              f"grad_norm={gn:.3e} lens={lens} sup={sup}")
        bad.append(cursor)
print(f"pairs swept: {len(range(0, len(items)-1, 2))}   bad pairs: {len(bad)}  at {bad[:8]}")
if not bad:
    print("no pair is non-finite on a fresh adapter -> the trained weights are required")
print("DIAG6_OK")
