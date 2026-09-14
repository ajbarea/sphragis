"""Supervised example construction. The masking is where the bugs hide."""

from __future__ import annotations

from typing import Any

import pytest

from sphragis.experiment.training import IGNORE_INDEX, build_supervised


class FakeTokenizer:
    """Character-level stand-in, so masking is testable with no model."""

    eos_token_id = 99

    def __call__(self, text: str, add_special_tokens: bool = True) -> dict[str, list[int]]:
        return {"input_ids": [ord(c) for c in text]}


EXAMPLE: dict[str, Any] = {
    "before": "x=1",
    "after": "x = 1",
    "comments": ["spaces"],
}


def test_the_prompt_is_masked_and_only_the_target_carries_loss() -> None:
    # If the prompt were not masked the adapter would be trained to reproduce review
    # comments, not to apply them.
    tok = FakeTokenizer()
    item = build_supervised(tok, EXAMPLE, prompt_builder=lambda e: "PROMPT:")
    labels, ids = item["labels"], item["input_ids"]
    assert len(labels) == len(ids)
    assert labels[: len("PROMPT:")] == [IGNORE_INDEX] * len("PROMPT:")
    assert IGNORE_INDEX not in labels[len("PROMPT:") :]


def test_the_target_is_the_after_text_followed_by_eos() -> None:
    tok = FakeTokenizer()
    item = build_supervised(tok, EXAMPLE, prompt_builder=lambda e: "P")
    assert item["input_ids"][1:] == [ord(c) for c in "x = 1"] + [tok.eos_token_id]


def test_eos_is_learned_so_generation_can_stop() -> None:
    tok = FakeTokenizer()
    item = build_supervised(tok, EXAMPLE, prompt_builder=lambda e: "P")
    assert item["labels"][-1] == tok.eos_token_id


def test_an_over_long_example_is_truncated_from_the_left_of_the_prompt() -> None:
    # Truncating the target would teach the model to stop mid-line; the prompt is what
    # can afford to lose its head.
    tok = FakeTokenizer()
    item = build_supervised(tok, EXAMPLE, prompt_builder=lambda e: "P" * 100, max_length=20)
    assert len(item["input_ids"]) == 20
    assert item["input_ids"][-6:] == [ord(c) for c in "x = 1"] + [tok.eos_token_id]


def test_an_example_whose_target_alone_exceeds_the_budget_is_rejected() -> None:
    tok = FakeTokenizer()
    with pytest.raises(ValueError, match="target alone"):
        build_supervised(
            tok,
            {"before": "b", "after": "y" * 50, "comments": []},
            prompt_builder=lambda e: "P",
            max_length=10,
        )


def test_attention_mask_covers_everything_kept() -> None:
    tok = FakeTokenizer()
    item = build_supervised(tok, EXAMPLE, prompt_builder=lambda e: "PROMPT:")
    assert item["attention_mask"] == [1] * len(item["input_ids"])


def test_warmup_steps_are_derived_from_the_declared_ratio() -> None:
    # transformers 5 removed warmup_ratio and keeps only warmup_steps. The ratio stays the
    # declared, pre-registered parameter because it is scale-invariant; the step count is
    # derived at construction so changing the corpus size does not silently change warmup.
    from sphragis.experiment.training import warmup_steps

    # 160 examples, batch 2, accum 8 -> 10 optimiser steps per epoch, 20 over two epochs.
    assert warmup_steps(n_examples=160, batch_size=2, grad_accum=8, epochs=2, ratio=0.03) == 1
    assert warmup_steps(n_examples=8000, batch_size=2, grad_accum=8, epochs=2, ratio=0.03) == 30


def test_warmup_is_at_least_one_step_when_the_ratio_is_positive() -> None:
    from sphragis.experiment.training import warmup_steps

    assert warmup_steps(n_examples=4, batch_size=2, grad_accum=8, epochs=1, ratio=0.03) == 1


def test_a_zero_ratio_means_no_warmup() -> None:
    from sphragis.experiment.training import warmup_steps

    assert warmup_steps(n_examples=1000, batch_size=2, grad_accum=8, epochs=2, ratio=0.0) == 0
