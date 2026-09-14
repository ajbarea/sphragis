"""The model stack. The only module in this repository permitted to import it.

Everything above this file talks to `Generator` and `Trainer` protocols, so the
orchestration, the metrics and the statistics all run with no GPU and no model. That
matters because the pilot runs in November and the confirmatory grid in February: without
the seam, February would be the first end-to-end execution.

`MODEL_ID` is the registered base model and is not a tuning knob. A smaller model is for
developing this file on a local card; changing what the *study* measures is a
pre-registration decision, not a deployment one.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import torch
from peft import LoraConfig, PeftMixedModel, PeftModel, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer
from transformers.tokenization_utils_base import PreTrainedTokenizerBase

MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"
DEV_MODEL_ID = "Qwen/Qwen2.5-Coder-1.5B-Instruct"

# research(2026-09): rank rises with performance to about 32 and flattens; alpha = 2r,
# because a fixed low alpha at high rank is unstable; attention plus MLP beats attention
# alone and coverage matters more than rank.
LORA = LoraConfig(
    r=32,
    lora_alpha=64,
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM",
    target_modules=[
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
        "gate_proj",
        "up_proj",
        "down_proj",
    ],
)


def _require_tokenizer(model_id: str) -> PreTrainedTokenizerBase:
    """Load a tokenizer, failing loudly rather than returning None downstream."""
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    if tokenizer is None:
        raise RuntimeError(f"no tokenizer for {model_id}")
    return tokenizer


@dataclass
class HFGenerator:
    """A `Generator` backed by a base model plus an optional LoRA adapter."""

    model_id: str = MODEL_ID
    adapter_path: str | None = None
    device: str = "cuda:0"
    max_new_tokens: int = 256

    def __post_init__(self) -> None:
        self.tokenizer = _require_tokenizer(self.model_id)
        model = AutoModelForCausalLM.from_pretrained(
            self.model_id, dtype=torch.bfloat16, device_map=self.device
        )
        if self.adapter_path:
            model = PeftModel.from_pretrained(model, self.adapter_path)
        model.eval()
        self.model = model

    def generate(self, prompt: str) -> str:
        """Greedy decoding, because exact match needs the output to be deterministic."""
        text = self.tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True
        )
        inputs = self.tokenizer(text, return_tensors="pt").to(self.device)
        with torch.inference_mode():
            out = self.model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        generated = out[0][inputs["input_ids"].shape[-1] :]
        return str(self.tokenizer.decode(generated, skip_special_tokens=True))


def attach_adapter(
    model_id: str, seed: int
) -> tuple[PeftModel | PeftMixedModel, PreTrainedTokenizerBase]:
    """A fresh LoRA adapter on the base model, seeded so the init replays."""
    torch.manual_seed(seed)
    tokenizer = _require_tokenizer(model_id)
    base = AutoModelForCausalLM.from_pretrained(model_id, dtype=torch.bfloat16, device_map="cuda:0")
    return get_peft_model(base, LORA), tokenizer


def _smoke(model_id: str) -> int:
    """Load the model, generate once, and report what it cost. Needs a GPU."""
    import time

    if not torch.cuda.is_available():
        print("no CUDA device; this module needs a GPU")
        return 1
    props = torch.cuda.get_device_properties(0)
    print(f"gpu {props.name} {props.total_memory / 1e9:.1f}GB torch {torch.__version__}")

    start = time.time()
    generator = HFGenerator(model_id=model_id, max_new_tokens=64)
    print(f"load_seconds {time.time() - start:.1f}")
    print(f"weights_gb {torch.cuda.memory_allocated() / 1e9:.2f}")

    prompt = (
        "Revise the code below to address every review comment.\n"
        "Reply with the revised code only.\n\n"
        "Review comments:\n- spaces around the operator\n\nCode:\n    return x+1\n"
    )
    torch.cuda.synchronize()
    start = time.time()
    completion = generator.generate(prompt)
    torch.cuda.synchronize()
    elapsed = time.time() - start
    print(f"generate_seconds {elapsed:.2f}")
    print(f"peak_gb {torch.cuda.max_memory_allocated() / 1e9:.2f}")
    print(f"completion {completion[:160]!r}")

    adapted, _ = attach_adapter(model_id, seed=1)
    trainable = sum(p.numel() for p in adapted.parameters() if p.requires_grad)
    total = sum(p.numel() for p in adapted.parameters())
    print(f"lora_trainable {trainable:,} of {total:,} ({100 * trainable / total:.3f}%)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="sphragis.experiment.model")
    parser.add_argument("--condition", default="base")
    parser.add_argument("--eval-org", default="openstack")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--model-id", default=MODEL_ID)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--smoke", action="store_true", help="load, generate once, report cost")
    parser.add_argument("--grid", action="store_true", help="walk the whole grid in one job")
    parser.add_argument("--orgs", default="openstack,qt")
    parser.add_argument("--seeds", default="1,2,3")
    args = parser.parse_args()
    if args.smoke:
        return _smoke(args.model_id)
    print("evaluation over frozen windows is not wired yet; see plan A2")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
