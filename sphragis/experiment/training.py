"""Supervised examples for adapter training.

Pure over a tokenizer, so the masking is testable without a model. The masking is the part
worth testing: train on the prompt as well as the target and the adapter learns to
reproduce review comments rather than to apply them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

IGNORE_INDEX = -100


def build_supervised(
    tokenizer: Any,
    example: Mapping[str, Any],
    *,
    prompt_builder: Callable[[Mapping[str, Any]], str],
    max_length: int = 2048,
) -> dict[str, list[int]]:
    """One training item: prompt tokens masked, target tokens supervised.

    Truncation drops the head of the *prompt* rather than the tail of the target. Cutting
    the target would teach the model to stop mid-line, which is exactly the failure exact
    match punishes hardest.
    """
    prompt_ids = list(tokenizer(prompt_builder(example))["input_ids"])
    target_ids = list(tokenizer(str(example["after"]), add_special_tokens=False)["input_ids"])
    target_ids.append(tokenizer.eos_token_id)

    if len(target_ids) >= max_length:
        raise ValueError(
            f"target alone is {len(target_ids)} tokens against a budget of {max_length}; "
            "this example cannot be trained on without cutting the answer"
        )
    room = max_length - len(target_ids)
    prompt_ids = prompt_ids[-room:]

    input_ids = prompt_ids + target_ids
    labels = [IGNORE_INDEX] * len(prompt_ids) + target_ids
    return {
        "input_ids": input_ids,
        "labels": labels,
        "attention_mask": [1] * len(input_ids),
    }
