"""Supervised examples for adapter training.

Pure over a tokenizer, so the masking is testable without a model. The masking is the part
worth testing: train on the prompt as well as the target and the adapter learns to
reproduce review comments rather than to apply them.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence
from typing import Any

IGNORE_INDEX = -100


def render_chat(tokenizer: Any, prompt: str) -> str:
    """The exact text the model receives: the prompt as a user turn, ready for an answer.

    The single definition of prompt format for training and for every generator.
    Qwen2.5-Coder-7B-Instruct is instruction-tuned, and its eos is the chat turn
    terminator, so the raw prompt is out of distribution for it. Measured on the pilot's
    45 held-out examples with the base model: raw prompt exact match 0.000, edit similarity
    0.193, median answer 344 characters; chat template 0.022, 0.754 and 64 characters
    against a 63-character reference.
    """
    return str(
        tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True
        )
    )


def build_supervised(
    tokenizer: Any,
    example: Mapping[str, Any],
    *,
    prompt_builder: Callable[[Mapping[str, Any]], str],
    max_length: int = 2048,
) -> dict[str, list[int]]:
    """One training item: prompt tokens masked, target tokens supervised.

    The prompt goes through `render_chat`, the same function every generator uses, so the
    adapter is fitted on exactly the input it is evaluated on. It was not: training
    tokenized the raw prompt while `HFGenerator` applied the chat template, two sequences
    that shared no prefix at all (61 tokens against 90 on a real example).

    An item that does not fit is refused rather than truncated. Cutting the target teaches
    the model to stop mid-line; cutting the prompt's head removes the chat frame and the
    instruction, leaving a fragment of code with no request attached. Neither is the
    registered task, and on the pilot corpus no item comes within 1,300 tokens of the limit.
    """
    if tokenizer.eos_token_id is None:
        raise ValueError("tokenizer has no eos token; the target would end in None")
    prompt_ids = list(
        tokenizer(render_chat(tokenizer, prompt_builder(example)), add_special_tokens=False)[
            "input_ids"
        ]
    )
    target_ids = list(tokenizer(str(example["after"]), add_special_tokens=False)["input_ids"])
    target_ids.append(tokenizer.eos_token_id)

    if len(target_ids) >= max_length:
        raise ValueError(
            f"target alone is {len(target_ids)} tokens against a budget of {max_length}; "
            "this example cannot be trained on without cutting the answer"
        )
    if len(prompt_ids) + len(target_ids) > max_length:
        raise ValueError(
            f"prompt {len(prompt_ids)} + target {len(target_ids)} tokens exceeds {max_length}; "
            "truncating either side would train on a different task than the one evaluated"
        )

    input_ids = prompt_ids + target_ids
    labels = [IGNORE_INDEX] * len(prompt_ids) + target_ids
    return {
        "input_ids": input_ids,
        "labels": labels,
        "attention_mask": [1] * len(input_ids),
    }


def total_steps(*, n_examples: int, batch_size: int, grad_accum: int, epochs: int) -> int:
    """Optimiser steps for the declared budget: every example seen once per epoch.

    Ceiling, not floor. Flooring drops the tail of each epoch, so the realized budget was
    1.85 epochs at 156 examples and 1.98 at 1000 -- a shortfall that varies with corpus
    size, which is exactly the thing the conditions are supposed to hold constant.

    This is the single definition of the step count. `warmup_steps` derives from it and
    the training loop consumes it, so the declared ratio cannot drift from the realized
    one; they did drift, by a factor of nearly three at pilot scale.
    """
    per_epoch = max(1, math.ceil(n_examples / max(batch_size * grad_accum, 1)))
    return per_epoch * max(epochs, 1)


def training_order(*, n_examples: int, epochs: int, seed: int) -> list[int]:
    """The exact index sequence the training loop consumes.

    A fresh permutation per epoch, so every example is seen exactly `epochs` times. The
    loop this replaces walked a rolling cursor modulo `n - 1`, which never reached the
    last example at any corpus size and gave a prefix two exposures against the suffix's
    one, with the split point depending on `n`. Two organizations of different sizes
    therefore trained under differently shaped budgets.

    Pure, and returned rather than consumed in place, so the order a run used can be
    asserted without a model.
    """
    order: list[int] = []
    for epoch in range(max(epochs, 1)):
        indices = list(range(n_examples))
        # Seeded from a string, not from hash(): Random derives a str seed through
        # SHA-512, which is stable across processes and Python builds, where hash() of a
        # container is an implementation detail. A per-epoch stream so epoch 2 is not a
        # repeat of epoch 1 and two seeds never share an ordering.
        random.Random(f"sphragis/{seed}/{epoch}").shuffle(indices)
        order.extend(indices)
    return order


def step_batches(
    order: Sequence[int], *, batch_size: int, grad_accum: int, epochs: int = 1
) -> list[list[list[int]]]:
    """`order` grouped into optimiser steps, each a list of micro-batches.

    An epoch boundary is a step boundary. Chunking the concatenated order instead lets one
    optimiser step straddle two epochs, which both disagrees with `total_steps` whenever
    the corpus does not divide evenly and can put the same example in one step twice.

    The last step of each epoch is short whenever the corpus does not divide evenly. That
    is why the loop must average its loss over the micro-batches it actually ran rather
    than over `grad_accum`: dividing by the nominal count deflates the reported loss in
    proportion to how much of the step was missing.
    """
    per_step = max(batch_size * grad_accum, 1)
    epochs = max(epochs, 1)
    span = len(order) // epochs
    steps: list[list[list[int]]] = []
    for epoch in range(epochs):
        chunk = list(order[epoch * span : (epoch + 1) * span])
        for start in range(0, len(chunk), per_step):
            window = chunk[start : start + per_step]
            steps.append(
                [window[i : i + batch_size] for i in range(0, len(window), max(batch_size, 1))]
            )
    return steps


def lr_multiplier(step: int, *, warmup: int, total: int) -> float:
    """Linear warmup into cosine decay, as a fraction of the declared learning rate.

    Pure and public so CI checks the shape of the pre-registered schedule; it lived as a
    closure inside `train_adapter`, in the one module CI cannot import. Never negative and
    never above 1: a multiplier outside that range would silently train at a rate the report
    does not state.
    """
    if warmup and step < warmup:
        return (step + 1) / warmup
    progress = (step - warmup) / max(total - warmup, 1)
    return 0.5 * (1.0 + math.cos(math.pi * min(progress, 1.0)))


def warmup_steps(
    *, n_examples: int, batch_size: int, grad_accum: int, epochs: int, ratio: float
) -> int:
    """Optimiser warmup steps from the declared ratio.

    transformers 5 removed `warmup_ratio` and keeps only `warmup_steps`. The ratio stays
    the declared parameter because it is scale-invariant: pinning steps instead would mean
    a change in corpus size silently changed the warmup fraction, and the training budget
    is a pre-registration item.
    """
    if ratio <= 0:
        return 0
    total = total_steps(
        n_examples=n_examples, batch_size=batch_size, grad_accum=grad_accum, epochs=epochs
    )
    return max(1, round(total * ratio))


def decoding_kwargs(temperature: float) -> dict[str, Any]:
    """The sampling arguments for `generate`, greedy unless a temperature is asked for.

    Greedy is the registered decoder: exact match needs a deterministic output. Sampling exists
    to test one hypothesis, that greedy decoding is what turned a convention present in 25% of
    training refinements into one the adapter emitted 64% of the time. At temperature 1 with
    top-k and top-p disabled the model samples from its own distribution unaltered, so if
    greedy is the amplifier, the emission rate should fall back toward the training rate.
    Anything other than exactly that unaltered distribution would test something else.
    """
    if temperature < 0:
        raise ValueError(f"temperature must be non-negative, got {temperature}")
    if temperature == 0:
        return {"do_sample": False}
    return {"do_sample": True, "temperature": temperature, "top_k": 0, "top_p": 1.0}
