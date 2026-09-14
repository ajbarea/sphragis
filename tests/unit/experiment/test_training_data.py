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
