"""Supervised example construction. The masking is where the bugs hide."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from sphragis.experiment.training import (
    IGNORE_INDEX,
    build_supervised,
    render_chat,
    step_batches,
    total_steps,
    training_order,
    warmup_steps,
)


class FakeTokenizer:
    """Character-level stand-in, so masking is testable with no model."""

    eos_token_id = 99

    def __call__(self, text: str, add_special_tokens: bool = True) -> dict[str, list[int]]:
        return {"input_ids": [ord(c) for c in text]}

    def apply_chat_template(
        self, messages: list[dict[str, str]], *, tokenize: bool, add_generation_prompt: bool
    ) -> str:
        # Identity, so the character-level masking tests read directly. The frame test
        # below uses a tokenizer whose template actually wraps.
        return messages[-1]["content"]


class NoEosTokenizer(FakeTokenizer):
    """A tokenizer that defines no eos, which some base checkpoints ship with."""

    eos_token_id: int | None = None  # ty: ignore[invalid-assignment]


class FramingTokenizer(FakeTokenizer):
    """A template that wraps the prompt, the way a real chat template does."""

    def apply_chat_template(
        self, messages: list[dict[str, str]], *, tokenize: bool, add_generation_prompt: bool
    ) -> str:
        return f"<sys>be helpful</sys><user>{messages[-1]['content']}</user><assistant>"


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


class TestDeclaredBudgetIsTheRealizedBudget:
    """The training budget is pre-registered, so the code must deliver what it declares.

    Every case here failed against the rolling-cursor loop these functions replaced.
    """

    @pytest.mark.parametrize("n", [1, 7, 16, 32, 156, 201, 1000, 3001])
    def test_every_example_is_seen_exactly_once_per_epoch(self, n: int) -> None:
        counts = Counter(training_order(n_examples=n, epochs=2, seed=1))
        assert set(counts) == set(range(n)), "an example was never trained on"
        assert set(counts.values()) == {2}, "exposure is not uniform across examples"

    @pytest.mark.parametrize("n", [17, 156, 201, 1000])
    def test_the_last_example_is_not_the_one_that_gets_dropped(self, n: int) -> None:
        # The replaced loop advanced `cursor % (n - 1)`, so index n-1 was unreachable at
        # every corpus size. Pinned separately because it was the silent half of the bug.
        assert n - 1 in training_order(n_examples=n, epochs=1, seed=0)

    @pytest.mark.parametrize("n", [16, 156, 201, 1000, 3001])
    def test_realized_epochs_equal_declared_epochs(self, n: int) -> None:
        order = training_order(n_examples=n, epochs=2, seed=0)
        steps = total_steps(n_examples=n, batch_size=1, grad_accum=16, epochs=2)
        groups = step_batches(order, batch_size=1, grad_accum=16, epochs=2)
        assert len(groups) == steps
        assert sum(len(m) for g in groups for m in g) == 2 * n

    def test_the_order_is_reproducible_and_seed_dependent(self) -> None:
        assert training_order(n_examples=50, epochs=2, seed=3) == training_order(
            n_examples=50, epochs=2, seed=3
        )
        assert training_order(n_examples=50, epochs=1, seed=3) != training_order(
            n_examples=50, epochs=1, seed=4
        )

    def test_an_epoch_is_not_a_repeat_of_the_previous_one(self) -> None:
        order = training_order(n_examples=40, epochs=2, seed=5)
        assert order[:40] != order[40:]

    def test_the_short_final_step_is_visible_rather_than_silently_dropped(self) -> None:
        # 20 examples, effective batch 16: the second step carries 4, not 16. The loss
        # average has to use 4 or it reports a number 4x too small.
        groups = step_batches(
            training_order(n_examples=20, epochs=1, seed=0), batch_size=1, grad_accum=16
        )
        assert [len(g) for g in groups] == [16, 4]

    @pytest.mark.parametrize(
        ("n", "ratio"), [(24, 0.03), (100, 0.03), (156, 0.03), (1000, 0.03), (3000, 0.03)]
    )
    def test_realized_warmup_ratio_tracks_the_declared_one(self, n: int, ratio: float) -> None:
        # warmup_steps used to ceil while the loop floored, so the realized ratio was 0.5
        # at n=24 against a declared 0.03. Both now derive from total_steps.
        steps = total_steps(n_examples=n, batch_size=1, grad_accum=16, epochs=2)
        warm = warmup_steps(n_examples=n, batch_size=1, grad_accum=16, epochs=2, ratio=ratio)
        realized = warm / steps
        assert abs(realized - ratio) <= max(ratio, 1.0 / steps), (
            f"declared {ratio}, realized {realized:.4f} at n={n}"
        )


def test_training_sees_exactly_the_prompt_every_generator_sends() -> None:
    """The adapter was fitted on the raw prompt and evaluated on the chat-framed one.

    Pinned by comparing tokens, not strings: the masked prefix of the training item must be
    the token sequence a generator feeds the model for the same example.
    """
    tok = FramingTokenizer()
    builder = lambda e: f"fix: {e['before']}"  # noqa: E731
    item = build_supervised(tok, EXAMPLE, prompt_builder=builder)
    sent = tok(render_chat(tok, builder(EXAMPLE)), add_special_tokens=False)["input_ids"]
    masked = [
        i
        for i, label in zip(item["input_ids"], item["labels"], strict=True)
        if label == IGNORE_INDEX
    ]
    assert masked == sent
    assert "".join(map(chr, masked)).startswith("<sys>")


def test_an_item_that_does_not_fit_is_refused_rather_than_cut() -> None:
    tok = FramingTokenizer()
    with pytest.raises(ValueError, match="exceeds"):
        build_supervised(tok, EXAMPLE, prompt_builder=lambda e: "p" * 500, max_length=64)


def test_a_tokenizer_without_eos_is_refused() -> None:
    tok = NoEosTokenizer()
    with pytest.raises(ValueError, match="eos"):
        build_supervised(tok, EXAMPLE, prompt_builder=lambda e: "PROMPT:")
