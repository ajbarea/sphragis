"""Is the cursor-160 NaN reproducible, or a transient? One observation is not a constant."""
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

def trial(label, batch_items):
    for p in trainable:
        p.grad = None
    batch = _collate(batch_items, tok.pad_token_id)
    loss = model(**batch).loss
    finite_loss = bool(torch.isfinite(loss))
    loss.backward()
    gn = float(torch.nn.utils.clip_grad_norm_(trainable, 1e9))
    return f"{label}: loss={'nan' if not finite_loss else f'{float(loss):.4f}'} grad_norm={gn:.4e} {'NaN' if gn != gn else 'ok'}"

print("--- the suspect pair, 5 identical repeats")
for k in range(5):
    print("  " + trial(f"rep{k}", items[160:162]))
print("--- each item alone")
print("  " + trial("item160", items[160:161]))
print("  " + trial("item161", items[161:162]))
print("--- reversed order")
print("  " + trial("reversed", [items[161], items[160]]))
print("--- 160 paired with an unrelated item")
print("  " + trial("160+0", [items[160], items[0]]))
print("  " + trial("161+0", [items[161], items[0]]))
print("--- a control pair known good")
print("  " + trial("0+1", items[0:2]))
print("DIAG7_OK")
