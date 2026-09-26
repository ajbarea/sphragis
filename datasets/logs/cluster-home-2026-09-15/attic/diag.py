"""Find the real cause of the NaN: dtypes, sequence lengths, and per-batch loss."""
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
lens = sorted(len(i["input_ids"]) for i in items)
sup = sorted(sum(1 for x in i["labels"] if x != -100) for i in items)
print(f"items {len(items)}")
print(f"seq len   min={lens[0]} med={lens[len(lens)//2]} p90={lens[int(.9*len(lens))]} max={lens[-1]}")
print(f"supervised tokens per item: min={sup[0]} med={sup[len(sup)//2]} max={sup[-1]}")
print(f"items with 0 supervised tokens: {sum(1 for s in sup if s == 0)}")

base = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.bfloat16, device_map="cuda:0")
model = get_peft_model(base, LORA)
dt = {}
for n, p in model.named_parameters():
    if p.requires_grad:
        dt[str(p.dtype)] = dt.get(str(p.dtype), 0) + 1
print(f"trainable param dtypes: {dt}")

# One forward/backward per micro-batch, looking for the first non-finite loss.
model.train()
opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=TRAINING["learning_rate"])
bad = 0
for step, i in enumerate(items[:24]):
    ids = torch.tensor([i["input_ids"]]).cuda()
    lab = torch.tensor([i["labels"]]).cuda()
    out = model(input_ids=ids, labels=lab)
    loss = out.loss
    finite = torch.isfinite(loss).item()
    if not finite:
        bad += 1
        print(f"  step {step}: NON-FINITE loss, seq_len={len(i['input_ids'])}, sup={sum(1 for x in i['labels'] if x!=-100)}")
    loss.backward()
    gn = torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
    if not torch.isfinite(gn):
        print(f"  step {step}: NON-FINITE grad_norm, loss={loss.item():.4f}, seq_len={len(i['input_ids'])}")
        bad += 1
        break
    opt.step(); opt.zero_grad()
print(f"non-finite events in 24 micro-steps: {bad}")
print("DIAG_OK")
