"""One-off GPU probe: does the base model load on a GH200 and what does it cost?"""
import time, torch, platform
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"
print(f"machine={platform.machine()} torch={torch.__version__} cuda_build={torch.version.cuda}")
print(f"cuda_available={torch.cuda.is_available()}")
if torch.cuda.is_available():
    p = torch.cuda.get_device_properties(0)
    print(f"gpu={p.name} mem={p.total_memory/1e9:.1f}GB capability={p.major}.{p.minor}")

t0 = time.time()
tok = AutoTokenizer.from_pretrained(MODEL)
model = AutoModelForCausalLM.from_pretrained(MODEL, dtype=torch.bfloat16, device_map="cuda:0")
model.eval()
print(f"load_seconds={time.time()-t0:.1f}")
print(f"weights_gb={torch.cuda.memory_allocated()/1e9:.2f}")

prompt = ("Revise the code below to address every review comment.\n"
          "Reply with the revised code only.\n\n"
          "Review comments:\n- spaces around the operator\n\nCode:\n    return x+1\n")
msgs = [{"role": "user", "content": prompt}]
text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
ids = tok(text, return_tensors="pt").to("cuda:0")

torch.cuda.synchronize(); t1 = time.time()
with torch.inference_mode():
    out = model.generate(**ids, max_new_tokens=64, do_sample=False,
                         pad_token_id=tok.eos_token_id)
torch.cuda.synchronize()
gen_s = time.time() - t1
new = out.shape[-1] - ids["input_ids"].shape[-1]
print(f"generate_seconds={gen_s:.2f} new_tokens={new} tok_per_s={new/gen_s:.1f}")
print(f"peak_gb={torch.cuda.max_memory_allocated()/1e9:.2f}")
print("COMPLETION:", repr(tok.decode(out[0][ids['input_ids'].shape[-1]:], skip_special_tokens=True))[:200])
print("PROBE_OK")
