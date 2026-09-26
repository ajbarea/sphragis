"""Run PAST the failure point (25 steps > the ~15 where it broke), logging weight growth."""
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

for label, lr, wd in (("lr2e-4_wd0", 2e-4, 0.0), ("lr5e-5_wd0.01", 5e-5, 0.01)):
    model = get_peft_model(base, LORA); model.train()
    trainable = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(trainable, lr=lr, weight_decay=wd)
    cursor, broke = 0, None
    print(f"--- {label}")
    for step in range(25):
        opt.zero_grad()
        total, nonfinite_micro = 0.0, 0
        for _ in range(8):
            batch = _collate(items[cursor:cursor+2] or items[:2], tok.pad_token_id)
            cursor = (cursor + 2) % max(len(items) - 2, 1)
            loss = model(**batch).loss / 8
            if not torch.isfinite(loss):
                nonfinite_micro += 1; break
            loss.backward(); total += float(loss) * 8
        wnorm = float(torch.sqrt(sum((p.detach().float()**2).sum() for p in trainable)))
        gn = float(torch.nn.utils.clip_grad_norm_(trainable, 1e9))
        if nonfinite_micro or not torch.isfinite(torch.tensor(gn)):
            print(f"  step {step}: BROKE  loss_nonfinite={nonfinite_micro} grad_norm={gn:.3e} |W|={wnorm:.3f}")
            broke = step; break
        if step % 3 == 0 or step > 10:
            print(f"  step {step:>2}: loss={total/8:.4f} grad_norm={gn:.3e} |W|={wnorm:.3f}")
        torch.nn.utils.clip_grad_norm_(trainable, 1.0)
        opt.step()
    print(f"  -> {label}: {'broke at step ' + str(broke) if broke is not None else 'survived 25 steps'}")
    model.unload()
print("DIAG5_OK")
