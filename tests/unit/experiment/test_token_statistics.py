"""Min-K%++ token statistics from logits, checked against a direct computation.

Needs torch, which CI does not install; it skips there and runs wherever the model stack is.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("transformers")
pytest.importorskip("peft")

from sphragis.experiment.model import token_statistics  # noqa: E402

LOGITS = [[2.0, 0.0, -1.0], [0.5, 0.5, 3.0], [-2.0, 4.0, 0.0]]


class FixedLogitsModel:
    def __call__(self, input_ids):
        return SimpleNamespace(logits=torch.tensor([LOGITS], dtype=torch.float32))


class ThreeTokenTokenizer:
    def __call__(self, text, return_tensors=None, add_special_tokens=True):
        return {"input_ids": torch.tensor([[0, 2, 1]])}


def _expected(row: list[float], target: int) -> tuple[float, float, float]:
    top = max(row)
    norm = top + math.log(sum(math.exp(x - top) for x in row))
    logp = [x - norm for x in row]
    p = [math.exp(x) for x in logp]
    mean = sum(pi * li for pi, li in zip(p, logp, strict=True))
    variance = sum(pi * li * li for pi, li in zip(p, logp, strict=True)) - mean * mean
    return logp[target], mean, variance


def test_statistics_match_a_direct_computation_and_skip_the_first_token() -> None:
    stats = token_statistics(
        FixedLogitsModel(),
        ThreeTokenTokenizer(),  # ty: ignore[invalid-argument-type]
        "x",
        device="cpu",
    )
    # Positions 0 and 1 predict tokens 2 and 1; the last logit row predicts nothing.
    assert len(stats) == 2
    for got, want in zip(stats, [_expected(LOGITS[0], 2), _expected(LOGITS[1], 1)], strict=True):
        assert got == pytest.approx(want, abs=1e-5)


def test_a_single_token_has_no_prediction_to_score() -> None:
    class OneToken(ThreeTokenTokenizer):
        def __call__(self, text, return_tensors=None, add_special_tokens=True):
            return {"input_ids": torch.tensor([[0]])}

    with pytest.raises(ValueError, match="at least two tokens"):
        token_statistics(
            FixedLogitsModel(),
            OneToken(),  # ty: ignore[invalid-argument-type]
            "x",
            device="cpu",
        )
