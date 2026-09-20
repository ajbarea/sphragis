"""How much of the pilot's base-model floor is the prompt format?

`build_supervised` tokenizes the raw prompt; `HFGenerator` applies the chat template. The
pilot scored BOTH arms raw, which is internally consistent for the adapted arm and
out-of-distribution for the base arm: Qwen2.5-Coder-7B-Instruct is an instruction-tuned
checkpoint whose own eos is the chat turn terminator. An out-of-distribution base arm
deflates the base score and inflates the measured ADAPTED - BASE difference, in the
direction that flatters the result.

This measures the size of that confound on the same held-out changes the pilot used, so
the number in the Stage 1 report is the honest one. No training: base model only, both
formats, everything else identical.
"""

import argparse
import json
import random
import time
from pathlib import Path
from statistics import median

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from sphragis.experiment.model import MODEL_ID, run_provenance
from sphragis.experiment.runner import build_prompt, evaluate, to_clusters
from sphragis.measure.stats import cluster_bootstrap

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("examples", nargs="?", type=Path, default=Path("pilot-examples.jsonl"))
parser.add_argument("--out", type=Path, default=Path("prompt-format-probe.json"))
parser.add_argument("--split-seed", type=int, default=0)
parser.add_argument("--bootstrap-seed", type=int, default=7)
parser.add_argument("--max-new-tokens", type=int, default=96)
args = parser.parse_args()

rows = [json.loads(line) for line in args.examples.read_text().splitlines() if line]

# The pilot's split, reproduced exactly, so the comparison is on the same 45 examples.
changes = sorted({r["change_id"] for r in rows})
random.Random(args.split_seed).shuffle(changes)
eval_ids = set(changes[int(0.8 * len(changes)) :])
eval_rows = [r for r in rows if r["change_id"] in eval_ids]
print(f"eval {len(eval_rows)} examples over {len(eval_ids)} changes", flush=True)

tok = AutoTokenizer.from_pretrained(MODEL_ID)
model = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.bfloat16, device_map="cuda:0")
model.eval()
print("loaded base", flush=True)


class FormatGenerator:
    """The base model under one prompt format. `chat` is the only thing that varies."""

    def __init__(self, *, chat: bool) -> None:
        self.chat = chat

    def generate(self, prompt: str) -> str:
        text = prompt
        if self.chat:
            text = tok.apply_chat_template(
                [{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True
            )
        ids = tok(text, return_tensors="pt").to("cuda:0")
        with torch.inference_mode():
            out = model.generate(
                **ids,
                max_new_tokens=args.max_new_tokens,
                do_sample=False,
                pad_token_id=tok.eos_token_id,
            )
        return str(tok.decode(out[0][ids["input_ids"].shape[-1] :], skip_special_tokens=True))


# The token counts, before any generation: the claim is that these inputs differ, and by
# how much is the first thing the report has to state.
sample = build_prompt(eval_rows[0])
raw_ids = tok(sample)["input_ids"]
chat_ids = tok(
    tok.apply_chat_template(
        [{"role": "user", "content": sample}], tokenize=False, add_generation_prompt=True
    )
)["input_ids"]
shared = next(
    (i for i, (a, b) in enumerate(zip(raw_ids, chat_ids, strict=False)) if a != b),
    min(len(raw_ids), len(chat_ids)),
)
print(
    f"tokens raw={len(raw_ids)} chat={len(chat_ids)} shared_prefix={shared} "
    f"eos={tok.eos_token_id} ({tok.decode([tok.eos_token_id])!r})",
    flush=True,
)

results = {}
for label, chat in (("raw", False), ("chat", True)):
    t0 = time.time()
    scored = evaluate(FormatGenerator(chat=chat), eval_rows)
    results[label] = scored
    n = len(scored)
    print(
        f"[BASE {label:<4}] "
        f"EM={sum(r['exact_match'] for r in scored) / n:.3f} "
        f"normEM={sum(r['normalized_exact_match'] for r in scored) / n:.3f} "
        f"sim={sum(r['edit_similarity'] for r in scored) / n:.3f} "
        f"pred_chars_med={median(len(r['prediction']) for r in scored):.0f}  "
        f"({time.time() - t0:.0f}s)",
        flush=True,
    )

interval = cluster_bootstrap(to_clusters(results["chat"], results["raw"]), seed=args.bootstrap_seed)
print(
    f"\nCHAT - RAW base exact match: {interval['estimate']:+.3f} "
    f"[{interval['low']:+.3f}, {interval['high']:+.3f}]",
    flush=True,
)
args.out.write_text(
    json.dumps(
        {
            "provenance": run_provenance(),
            "model_id": MODEL_ID,
            "raw_prompt_tokens": len(raw_ids),
            "chat_prompt_tokens": len(chat_ids),
            "shared_prefix_tokens": shared,
            "interval": interval,
            **results,
        },
        indent=2,
    )
)
print(f"wrote {args.out}")
print("PROBE_OK")
