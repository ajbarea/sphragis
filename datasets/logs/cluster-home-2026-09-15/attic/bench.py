"""7B throughput on a GH200, with compilation separated from steady state."""
import time, torch
from sphragis.experiment.model import HFGenerator, MODEL_ID

p = torch.cuda.get_device_properties(0)
print(f"gpu {p.name} {p.total_memory/1e9:.1f}GB torch {torch.__version__}")
t0 = time.time()
g = HFGenerator(model_id=MODEL_ID, max_new_tokens=128)
print(f"load_seconds {time.time()-t0:.1f}")
print(f"weights_gb {torch.cuda.memory_allocated()/1e9:.2f}")

PROMPT = ("Revise the code below to address every review comment.\n"
          "Reply with the revised code only.\n\n"
          "Review comments:\n- spaces around the operator\n- use a literal\n\n"
          "Code:\n    x = list()\n    return x+1\n")

torch.cuda.synchronize(); t = time.time()
g.generate(PROMPT)
torch.cuda.synchronize()
print(f"warmup_seconds {time.time()-t:.2f}  (includes triton JIT)")

for n in (32, 128, 256):
    g.max_new_tokens = n
    torch.cuda.synchronize(); t = time.time()
    out = g.generate(PROMPT)
    torch.cuda.synchronize()
    el = time.time() - t
    toks = len(g.tokenizer(out)["input_ids"])
    print(f"steady max_new={n:<4} seconds={el:5.2f} generated_tokens={toks:<4} tok_per_s={toks/el:.1f}")

print(f"peak_gb {torch.cuda.max_memory_allocated()/1e9:.2f}")
print("BENCH_OK")
