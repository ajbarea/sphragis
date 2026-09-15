"""7B throughput on a GH200, with compilation separated from steady state.

Backs the grid `--time` sizing in ROADMAP.md. Steady state, not the first call: the first
generate() pays triton JIT and reports a throughput several times too low, which would size
every walltime in the grid off a compilation artefact.
"""

import time

import torch

from sphragis.experiment.model import MODEL_ID, HFGenerator
from sphragis.experiment.runner import build_prompt

p = torch.cuda.get_device_properties(0)
print(f"gpu {p.name} {p.total_memory / 1e9:.1f}GB torch {torch.__version__}")
t0 = time.time()
g = HFGenerator(model_id=MODEL_ID, max_new_tokens=128)
print(f"load_seconds {time.time() - t0:.1f}")
print(f"weights_gb {torch.cuda.memory_allocated() / 1e9:.2f}")

# Through build_prompt, so the benchmark measures the prompt the study actually sends.
PROMPT = build_prompt(
    {
        "comments": ["spaces around the operator", "use a literal"],
        "before": "    x = list()\n    return x+1\n",
    }
)

torch.cuda.synchronize()
t = time.time()
g.generate(PROMPT)
torch.cuda.synchronize()
print(f"warmup_seconds {time.time() - t:.2f}  (includes triton JIT)")

for n in (32, 128, 256):
    g.max_new_tokens = n
    torch.cuda.synchronize()
    t = time.time()
    out = g.generate(PROMPT)
    torch.cuda.synchronize()
    el = time.time() - t
    toks = len(g.tokenizer(out)["input_ids"])
    print(
        f"steady max_new={n:<4} seconds={el:5.2f} "
        f"generated_tokens={toks:<4} tok_per_s={toks / el:.1f}"
    )

print(f"peak_gb {torch.cuda.max_memory_allocated() / 1e9:.2f}")
print("BENCH_OK")
