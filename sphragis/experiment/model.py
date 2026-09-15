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
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from peft import LoraConfig, PeftMixedModel, PeftModel, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer
from transformers.tokenization_utils_base import PreTrainedTokenizerBase

from sphragis.experiment.training import warmup_steps

MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"
DEV_MODEL_ID = "Qwen/Qwen2.5-Coder-1.5B-Instruct"

# research(2026-09): rank rises with performance to about 32 and flattens; alpha = 2r,
# because a fixed low alpha at high rank is unstable; attention plus MLP beats attention
# alone and coverage matters more than rank. Current tooling defaults lower (r=16); 32 is
# kept because the rank-versus-performance evidence supports it and the adapter's capacity
# is the thing a null result would otherwise be blamed on.
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


# research(2026-09). These are pre-registration items, not tuning knobs: the Stage 1
# report states the training budget and it must be identical across conditions, or the
# comparison between adapters measures the budget rather than the organization.
#
# - 2e-4 is the standard LoRA starting point; the usable band is 1e-4 to 2e-4.
# - 2 epochs, deliberately. The evidence is that accuracy *falls* as epochs rise and that
#   models converge within tens of steps and then memorise. With a corpus in the low
#   thousands per organization, overtraining is the likelier failure than undertraining.
# - Batch 16 is the largest that comfortably fits a 7B plus optimiser state on one GH200.
TRAINING = {
    "learning_rate": 2e-4,
    "epochs": 2,
    "batch_size": 16,
    "warmup_ratio": 0.03,
    "lr_scheduler": "cosine",
    "max_seq_length": 2048,
}


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
            # ty: the transformers stub types generate() on GenerativePreTrainedModel,
            # which a PeftModel wrapper does not satisfy structurally. Runtime is fine.
            # Only visible where the model stack is installed; CI resolves neither.
            out = self.model.generate(  # ty: ignore[invalid-argument-type]
                **inputs,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        generated = out[0][inputs["input_ids"].shape[-1] :]
        return str(self.tokenizer.decode(generated, skip_special_tokens=True))


def cast_trainable_to_fp32(model: Any) -> int:
    """Keep the adapter in fp32 while the frozen base stays bf16.

    research(2026-09): bf16 LoRA parameters are a known source of NaN gradients, and the
    documented workaround is to accumulate in fp32 for the adapter only. PEFT issue #3073
    records the same failure, attributing it to LoRA gradients not being normalised by
    input norm, so they explode where activations vary across layers.

    Observed here on job 143313: loss fell 1.00 -> 0.52 over two healthy steps, then
    grad_norm went NaN at step 3 and loss collapsed to exactly 0. The adapted model then
    emitted garbage, edit similarity 0.001 against the base model's 0.157. Reported
    failures of this shape appear "after 2-3 steps", which matches.

    Returns the number of tensors cast, so a caller can assert it did something.
    """
    cast = 0
    for parameter in model.parameters():
        if parameter.requires_grad and parameter.dtype != torch.float32:
            parameter.data = parameter.data.to(torch.float32)
            cast += 1
    return cast


def attach_adapter(
    model_id: str, seed: int
) -> tuple[PeftModel | PeftMixedModel, PreTrainedTokenizerBase]:
    """A fresh LoRA adapter on the base model, seeded so the init replays."""
    torch.manual_seed(seed)
    tokenizer = _require_tokenizer(model_id)
    base = AutoModelForCausalLM.from_pretrained(model_id, dtype=torch.bfloat16, device_map="cuda:0")
    return get_peft_model(base, LORA), tokenizer


def train_adapter(
    model: Any,
    items: Sequence[Mapping[str, Any]],
    *,
    pad_token_id: int,
    seed: int,
    budget: Mapping[str, Any] = TRAINING,
    micro_batch: int = 2,
    grad_accum: int = 8,
) -> list[float]:
    """Train a LoRA adapter with an explicit loop. Returns the loss at each optimiser step.

    Deliberately not `transformers.Trainer`. Trainer diverged on this corpus: loss fell
    1.00 to 0.52 over two healthy steps, then grad_norm went NaN and loss collapsed to
    exactly 0, leaving an adapter that emitted garbage. Four diagnostic jobs eliminated
    every hypothesis that would have implicated the data or the configuration rather than
    Trainer: sequence lengths are unremarkable (median 80, max 678), no item lacks
    supervised tokens, the adapter is already fp32, and a hand-written loop is stable at
    batch 1, at batch 2 with padding, with and without autocast, and with accumulation of
    8 at both 2e-4 and 5e-5.

    This loop is that configuration, made permanent. For a pre-registered study a training
    procedure that can be read in full is worth more than one whose failure mode resisted
    four attempts to reproduce.
    """
    torch.manual_seed(seed)
    trainable = [p for p in model.parameters() if p.requires_grad]
    optimiser = torch.optim.AdamW(trainable, lr=float(budget["learning_rate"]))
    per_step = micro_batch * grad_accum
    steps = max(1, len(items) // per_step) * int(budget["epochs"])
    # Linear warmup then cosine, so the schedule matches the declared budget. A declared
    # parameter the code ignores is worse than either choice, and this one is
    # pre-registered.
    warm = warmup_steps(
        n_examples=len(items),
        batch_size=micro_batch,
        grad_accum=grad_accum,
        epochs=int(budget["epochs"]),
        ratio=float(budget["warmup_ratio"]),
    )

    def factor(step: int) -> float:
        if warm and step < warm:
            return (step + 1) / warm
        progress = (step - warm) / max(steps - warm, 1)
        return 0.5 * (1.0 + math.cos(math.pi * min(progress, 1.0)))

    schedule = torch.optim.lr_scheduler.LambdaLR(optimiser, factor)

    model.train()
    losses: list[float] = []
    cursor = 0
    for _ in range(steps):
        optimiser.zero_grad()
        total = 0.0
        for _ in range(grad_accum):
            batch = _collate(items[cursor : cursor + micro_batch], pad_token_id)
            cursor = (cursor + micro_batch) % max(len(items) - micro_batch, 1)
            loss = model(**batch).loss / grad_accum
            if not torch.isfinite(loss):
                raise RuntimeError("non-finite loss; refusing to train on a diverged step")
            loss.backward()
            total += float(loss) * grad_accum
        norm = torch.nn.utils.clip_grad_norm_(trainable, 1.0)
        if not torch.isfinite(norm):
            raise RuntimeError("non-finite gradient norm; refusing to step")
        optimiser.step()
        schedule.step()
        losses.append(total / grad_accum)
    return losses


def _collate(batch: Sequence[Mapping[str, Any]], pad_token_id: int) -> dict[str, Any]:
    width = max(len(item["input_ids"]) for item in batch)

    def pad(key: str, filler: int) -> Any:
        return torch.tensor(
            [list(item[key]) + [filler] * (width - len(item[key])) for item in batch]
        ).cuda()

    return {
        "input_ids": pad("input_ids", pad_token_id),
        "labels": pad("labels", -100),
        "attention_mask": pad("attention_mask", 0),
    }


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
