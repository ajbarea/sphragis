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
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from peft import LoraConfig, PeftMixedModel, PeftModel, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer
from transformers.tokenization_utils_base import PreTrainedTokenizerBase

from sphragis.experiment.training import (
    decoding_kwargs,
    lr_multiplier,
    render_chat,
    step_batches,
    total_steps,
    training_order,
    warmup_steps,
)
from sphragis.provenance import provenance_header, slurm_record

MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"
DEV_MODEL_ID = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
# The checkpoint the membership probes read. The registered model is an instruction-tuned
# fine-tune of this base (same 7,615,616,512 parameters), and detection methods are reported
# degraded by instruction fine-tuning (Samuel, Zhou and Zou, COLING 2025), so Min-K%++ reads
# the base, whose likelihoods reflect pretraining exposure. A registered choice.
MEMBERSHIP_MODEL_ID = "Qwen/Qwen2.5-Coder-7B"

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


@dataclass(frozen=True)
class TrainingReport:
    """What one training run actually did, as opposed to what it was asked to do.

    Returned instead of a bare loss list because the differences between asked and did are
    exactly the ones that break a pre-registered budget: a skipped step is a step the
    adapter did not take, and two conditions that skip differently were not trained
    identically however identical their configuration was.
    """

    losses: list[float]
    steps: int
    skipped_steps: int
    skipped_micro_batches: int
    examples_seen: int
    # Norm of every LoRA B matrix together. PEFT initializes B at zero (checked on peft 0.20),
    # so a positive value is direct evidence the adapter moved: the second half of the
    # pre-registered manipulation check.
    adapter_weight_norm: float = math.nan

    @property
    def applied_steps(self) -> int:
        """Optimiser updates that actually landed."""
        return self.steps - self.skipped_steps


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

# Greedy decoding stops here, so a reference longer than this can never be an exact match.
# The pilot ran at 96, below 4 of 172 OpenStack 2024-10 references (99, 114, 143 and 154
# tokens), which made 1 of its 27 held-out examples unwinnable for either arm. A registered
# decoding parameter, identical for every condition.
MAX_NEW_TOKENS = 256


@dataclass
class HFGenerator:
    """A `Generator` backed by a base model plus an optional LoRA adapter."""

    model_id: str = MODEL_ID
    adapter_path: str | None = None
    device: str = "cuda:0"
    max_new_tokens: int = MAX_NEW_TOKENS
    # 0 is greedy, the registered decoder. Non-zero only for the decoding check.
    temperature: float = 0.0
    seed: int = 0

    def __post_init__(self) -> None:
        self.tokenizer = _require_tokenizer(self.model_id)
        self._decoding = decoding_kwargs(self.temperature)
        if self.temperature:
            torch.manual_seed(self.seed)
        model = AutoModelForCausalLM.from_pretrained(
            self.model_id, dtype=torch.bfloat16, device_map=self.device
        )
        if self.adapter_path:
            model = PeftModel.from_pretrained(model, self.adapter_path)
        model.eval()
        self.model = model

    def generate(self, prompt: str) -> str:
        """Greedy by default: the output is the model's single most likely refinement.

        Greedy is not reproducible on the GPU. bf16 arithmetic is not associative, so the
        base model alone changed 23 to 28 of about 440 predictions between two identical runs.
        """
        text = render_chat(self.tokenizer, prompt)
        inputs = self.tokenizer(text, return_tensors="pt", add_special_tokens=False).to(self.device)
        with torch.inference_mode():
            # ty: the transformers stub types generate() on GenerativePreTrainedModel,
            # which a PeftModel wrapper does not satisfy structurally. Runtime is fine.
            # Only visible where the model stack is installed; CI resolves neither.
            out = self.model.generate(  # ty: ignore[invalid-argument-type]
                **inputs,
                max_new_tokens=self.max_new_tokens,
                pad_token_id=self.tokenizer.eos_token_id,
                **self._decoding,
            )
        generated = out[0][inputs["input_ids"].shape[-1] :]
        return str(self.tokenizer.decode(generated, skip_special_tokens=True))


def token_statistics(
    model: Any, tokenizer: PreTrainedTokenizerBase, text: str, device: str = "cuda:0"
) -> list[tuple[float, float, float]]:
    """Per-position inputs to Min-K%++: log p(x_t | x_<t), and the mean and variance of
    log p(z | x_<t) under the model's own next-token distribution over the vocabulary.

    Mirrors the Min-K%++ reference implementation: log-softmax over the logits, the first
    token skipped because nothing predicts it, mu = sum(p * log p), and
    sigma^2 = sum(p * (log p)^2) - mu^2. Logits are cast to float32 first; in bf16 the
    variance of a peaked distribution underflows. Terms with p = 0 are zeroed explicitly,
    since 0 * -inf is NaN rather than the 0 the expectation needs. The reduction to a score
    lives in `sphragis.measure.contamination`, where it is tested without a model.
    """
    ids = tokenizer(text, return_tensors="pt", add_special_tokens=False)["input_ids"].to(device)
    if ids.shape[-1] < 2:
        raise ValueError("token_statistics needs at least two tokens: the first has no prediction")
    with torch.inference_mode():
        logits = model(input_ids=ids).logits[0, :-1].float()
    log_probs = torch.log_softmax(logits, dim=-1)
    probs = log_probs.exp()
    finite = torch.isfinite(log_probs)
    weighted = torch.where(finite, probs * log_probs, torch.zeros_like(log_probs))
    weighted_sq = torch.where(finite, probs * log_probs.square(), torch.zeros_like(log_probs))
    mean = weighted.sum(-1)
    variance = weighted_sq.sum(-1) - mean.square()
    targets = ids[0, 1:]
    token_logprob = log_probs.gather(-1, targets[:, None]).squeeze(-1)
    return list(zip(token_logprob.tolist(), mean.tolist(), variance.tolist(), strict=True))


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
    # Seed BEFORE get_peft_model: the LoRA initialization draws from the global RNG, so
    # seeding afterwards leaves it dependent on whatever ran before. scripts/pilot.py had
    # exactly that bug, which made its "seed" control nothing but the dropout mask.
    torch.manual_seed(seed)
    tokenizer = _require_tokenizer(model_id)
    base = AutoModelForCausalLM.from_pretrained(model_id, dtype=torch.bfloat16, device_map="cuda:0")
    adapted = get_peft_model(base, LORA)
    # Not dead code: PEFT returns fp32 adapter parameters on a bf16 base under its current
    # default, so this casts nothing and returns 0. Calling it keeps that invariant enforced
    # rather than assumed, and bf16 adapter parameters are a known source of NaN gradients.
    cast_trainable_to_fp32(adapted)
    return adapted, tokenizer


def train_adapter(
    model: Any,
    items: Sequence[Mapping[str, Any]],
    *,
    pad_token_id: int,
    seed: int,
    budget: Mapping[str, Any] = TRAINING,
    micro_batch: int = 1,
    grad_accum: int = 16,
) -> TrainingReport:
    """Train a LoRA adapter with an explicit loop, and report what the run actually did.

    Deliberately not `transformers.Trainer`. Trainer diverged on this corpus: loss fell
    1.00 to 0.52 over two healthy steps, then grad_norm went NaN and loss collapsed to
    exactly 0, leaving an adapter that emitted garbage. Four diagnostic jobs eliminated
    every hypothesis that would have implicated the data or the configuration rather than
    Trainer: sequence lengths are unremarkable (median 80, max 678), no item lacks
    supervised tokens, the adapter is already fp32, and a hand-written loop is stable at
    batch 1, at batch 2 with padding, with and without autocast, and with accumulation of
    8 at both 2e-4 and 5e-5.

    `micro_batch` defaults to 1, with accumulation raised to keep the effective batch at
    16. Two examples from one change, both version bumps in the same file differing only
    in digits, produce a NaN gradient **only when batched together** -- deterministically
    over five repeats, in either order, while each is fine alone and fine paired with
    anything else. Batching one example at a time removes padding and pairing as variables
    entirely, and the cost is throughput this pilot does not need.

    This loop is that configuration, made permanent. For a pre-registered study a training
    procedure that can be read in full is worth more than one whose failure mode resisted
    four attempts to reproduce.
    """
    torch.manual_seed(seed)
    trainable = [p for p in model.parameters() if p.requires_grad]
    optimiser = torch.optim.AdamW(trainable, lr=float(budget["learning_rate"]))
    epochs = int(budget["epochs"])
    # The order and the step count are pure functions in `training`, so the realized
    # budget is asserted in CI rather than inferred from a loop no test can reach.
    order = training_order(n_examples=len(items), epochs=epochs, seed=seed)
    groups = step_batches(order, batch_size=micro_batch, grad_accum=grad_accum, epochs=epochs)
    steps = total_steps(
        n_examples=len(items), batch_size=micro_batch, grad_accum=grad_accum, epochs=epochs
    )
    # Linear warmup then cosine, so the schedule matches the declared budget. A declared
    # parameter the code ignores is worse than either choice, and this one is
    # pre-registered.
    warm = warmup_steps(
        n_examples=len(items),
        batch_size=micro_batch,
        grad_accum=grad_accum,
        epochs=epochs,
        ratio=float(budget["warmup_ratio"]),
    )

    schedule = torch.optim.lr_scheduler.LambdaLR(
        optimiser, lambda step: lr_multiplier(step, warmup=warm, total=steps)
    )

    model.train()
    losses: list[float] = []
    skipped_micro_batches = 0
    skipped_steps = 0
    for group in groups:
        optimiser.zero_grad()
        total = 0.0
        finite = 0
        for micro in group:
            batch = _collate([items[i] for i in micro], pad_token_id)
            # Scaled by the group's own size, not the nominal accumulation: the final
            # group of a run is short whenever the corpus does not divide evenly.
            loss = model(**batch).loss / len(group)
            if not torch.isfinite(loss):
                skipped_micro_batches += 1
                continue
            loss.backward()
            total += float(loss.detach()) * len(group)
            finite += 1
        norm = torch.nn.utils.clip_grad_norm_(trainable, 1.0)
        if finite == 0 or not torch.isfinite(norm):
            # Skip the step rather than poison the adapter, and count it. Silently
            # stepping on a NaN gradient is what produced an adapter emitting garbage.
            #
            # `finite == 0` is checked first and separately: with no gradient at all,
            # clip_grad_norm_ returns a finite 0.0, so the norm guard passes, the
            # optimiser takes a no-op step, and the step records a loss of exactly 0.0 --
            # which is the signature this loop exists to distinguish itself from.
            skipped_steps += 1
            optimiser.zero_grad()
            schedule.step()
            continue
        optimiser.step()
        schedule.step()
        # Averaged over the micro-batches that ran. Dividing by the nominal count instead
        # deflates the reported loss in proportion to how many were skipped, which makes
        # a degrading run look like an improving one.
        losses.append(total / finite)
    report = TrainingReport(
        losses=losses,
        steps=steps,
        skipped_steps=skipped_steps,
        skipped_micro_batches=skipped_micro_batches,
        examples_seen=len(order),
        adapter_weight_norm=math.sqrt(
            sum(
                float(p.detach().float().norm()) ** 2
                for name, p in model.named_parameters()
                if "lora_B" in name
            )
        ),
    )
    if skipped_steps or skipped_micro_batches:
        print(
            f"train_adapter: skipped {skipped_steps} step(s) of {steps} and "
            f"{skipped_micro_batches} micro-batch(es)"
        )
    # Logged now: a run that dies later writes no result file to carry the peak.
    print(f"train_adapter: gpu {json.dumps(gpu_record())}", flush=True)
    return report


def gpu_record() -> dict[str, Any] | None:
    """The device a run used and its peak memory, or None without CUDA.

    Peak is over the whole process, so call this when the run is done.
    """
    if not torch.cuda.is_available():
        return None
    props = torch.cuda.get_device_properties(0)
    return {
        "name": props.name,
        "total_gb": round(props.total_memory / 1e9, 2),
        "peak_allocated_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2),
        "peak_reserved_gb": round(torch.cuda.max_memory_reserved() / 1e9, 2),
        "cuda": torch.version.cuda,
    }


def run_provenance() -> dict[str, Any]:
    """Commit, packages, Slurm job and GPU: what a result needs to be attributed to hardware."""
    return {**provenance_header(), "slurm": slurm_record(), "gpu": gpu_record()}


def _collate(
    batch: Sequence[Mapping[str, Any]], pad_token_id: int, device: str = "cuda"
) -> dict[str, Any]:
    """Pad a micro-batch to its widest item. `device` is a parameter so CPU tests can reach it.

    Each field gets its own filler, and they are not interchangeable: labels pad with -100 so
    the padding carries no loss, and attention_mask with 0 so it is not attended to. Padding
    labels with `pad_token_id` would train the model to emit padding.
    """
    width = max(len(item["input_ids"]) for item in batch)

    def pad(key: str, filler: int) -> Any:
        return torch.tensor(
            [list(item[key]) + [filler] * (width - len(item[key])) for item in batch]
        ).to(device)

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
