"""The model module's own surfaces: batching, prompt format, and adapter dtype.

Needs torch and peft, which CI does not install, so this skips there and runs wherever the
model stack is. Every test here covers a surface that produced a real bug or enforces an
invariant the pre-registered design depends on.
"""

from __future__ import annotations

from typing import Any

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("transformers")
pytest.importorskip("peft")

from sphragis.experiment import model as model_module  # noqa: E402
from sphragis.experiment.model import (  # noqa: E402
    MAX_NEW_TOKENS,
    HFGenerator,
    _collate,
    cast_trainable_to_fp32,
)
from sphragis.experiment.training import render_chat  # noqa: E402


def _item(input_ids: list[int], labels: list[int]) -> dict[str, list[int]]:
    return {"input_ids": input_ids, "labels": labels, "attention_mask": [1] * len(input_ids)}


class TestCollate:
    def test_each_field_is_padded_with_its_own_filler(self) -> None:
        batch = [_item([1, 2, 3], [-100, 2, 3]), _item([4], [4])]
        out = _collate(batch, pad_token_id=99, device="cpu")
        assert out["input_ids"].tolist() == [[1, 2, 3], [4, 99, 99]]
        # -100, not the pad token: padding must carry no loss, or the adapter learns to
        # emit padding.
        assert out["labels"].tolist() == [[-100, 2, 3], [4, -100, -100]]
        assert out["attention_mask"].tolist() == [[1, 1, 1], [1, 0, 0]]

    def test_every_field_shares_one_width(self) -> None:
        out = _collate([_item([1, 2, 3, 4], [1, 2, 3, 4]), _item([5], [5])], 0, device="cpu")
        assert {tuple(v.shape) for v in out.values()} == {(2, 4)}

    def test_a_uniform_batch_is_untouched(self) -> None:
        out = _collate([_item([1, 2], [1, 2]), _item([3, 4], [3, 4])], 0, device="cpu")
        assert out["input_ids"].tolist() == [[1, 2], [3, 4]]
        assert out["attention_mask"].tolist() == [[1, 1], [1, 1]]


class _FakeEncoding(dict):
    """What a real tokenizer returns: a mapping that also moves to a device."""

    def to(self, device: str) -> _FakeEncoding:
        self.device = device
        return self


class _FakeTokenizer:
    eos_token_id = 7

    def __init__(self) -> None:
        self.seen: list[tuple[str, bool]] = []

    def apply_chat_template(
        self, messages: list[dict[str, str]], *, tokenize: bool, add_generation_prompt: bool
    ) -> str:
        return f"<user>{messages[-1]['content']}</user><assistant>"

    def __call__(
        self, text: str, return_tensors: str | None = None, add_special_tokens: bool = True
    ) -> _FakeEncoding:
        self.seen.append((text, add_special_tokens))
        return _FakeEncoding({"input_ids": torch.tensor([[1, 2, 3]])})

    def decode(self, ids: Any, skip_special_tokens: bool = False) -> str:
        return "generated"


class _FakeModel:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}
        self.dtype: Any = torch.bfloat16

    def eval(self) -> None:
        pass

    def to(self, dtype: Any) -> _FakeModel:
        """The generator upcasts to fp32 for reproducible greedy decoding."""
        self.dtype = dtype
        return self

    def parameters(self) -> Any:
        """The generator reads the precision back from here rather than from what it asked for."""
        return iter([torch.zeros(1, dtype=self.dtype)])

    def generate(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return torch.tensor([[1, 2, 3, 4, 5]])


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[HFGenerator, _FakeTokenizer, _FakeModel]:
    """A generator over fakes, with the fakes returned: reaching through the generator's
    declared types to inspect them is not something a type checker can follow."""
    tokenizer, fake = _FakeTokenizer(), _FakeModel()
    monkeypatch.setattr(model_module, "_require_tokenizer", lambda _: tokenizer)
    monkeypatch.setattr(model_module.AutoModelForCausalLM, "from_pretrained", lambda *a, **k: fake)
    return HFGenerator(device="cpu"), tokenizer, fake


def test_the_generator_sends_the_chat_frame_training_was_fitted_on(
    wired: tuple[HFGenerator, _FakeTokenizer, _FakeModel],
) -> None:
    """Training and evaluation used different formats; that cost a whole pilot's numbers."""
    generator, tokenizer, _ = wired
    generator.generate("fix: x=1")
    text, add_special = tokenizer.seen[-1]
    assert text == render_chat(tokenizer, "fix: x=1")
    assert text.startswith("<user>") and text.endswith("<assistant>")
    # The template already carries the frame; re-adding specials would double it.
    assert add_special is False


def test_decoding_is_greedy_and_capped_at_the_registered_length(
    wired: tuple[HFGenerator, _FakeTokenizer, _FakeModel],
) -> None:
    generator, _, fake = wired
    generator.generate("fix: x=1")
    kwargs = fake.kwargs
    assert kwargs["do_sample"] is False, "exact match needs a deterministic decode"
    assert kwargs["max_new_tokens"] == MAX_NEW_TOKENS


class _Tiny(torch.nn.Module):
    def __init__(self, dtype: Any) -> None:
        super().__init__()
        self.linear = torch.nn.Linear(4, 4).to(dtype)


def test_the_fp32_cast_reports_what_it_changed() -> None:
    """bf16 adapter parameters are a known source of NaN gradients."""
    bf16 = _Tiny(torch.bfloat16)
    # A Linear carries two trainable tensors, weight and bias.
    assert cast_trainable_to_fp32(bf16) == 2
    assert all(p.dtype is torch.float32 for p in bf16.parameters())


def test_the_fp32_cast_is_a_no_op_on_parameters_already_fp32() -> None:
    assert cast_trainable_to_fp32(_Tiny(torch.float32)) == 0


def test_frozen_parameters_are_left_alone() -> None:
    frozen = _Tiny(torch.bfloat16)
    for parameter in frozen.parameters():
        parameter.requires_grad = False
    assert cast_trainable_to_fp32(frozen) == 0
    assert all(p.dtype is torch.bfloat16 for p in frozen.parameters())


class TestRunProvenance:
    def test_without_a_gpu_the_gpu_record_is_null(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        assert model_module.gpu_record() is None

    def test_the_run_record_joins_commit_job_and_gpu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        record = model_module.run_provenance()
        # No compute precision here: it belongs to the model a script loaded, and scripts that
        # load their own in bf16 would otherwise have their results claim fp32.
        assert "inference_dtype" not in record
        assert "git" in record
        assert set(record["slurm"]) >= {"cluster", "job_id", "account"}
        assert record["gpu"] is None

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="needs a CUDA device")
    def test_on_a_gpu_the_record_names_the_device_and_its_peak(self) -> None:
        held = torch.ones(64 * 1024 * 1024, device="cuda")  # 256 MB of float32
        record = model_module.gpu_record()
        del held
        assert record is not None
        assert record["name"]
        assert record["total_gb"] > 0
        assert record["peak_allocated_gb"] >= 0.25
        assert record["cuda"] == torch.version.cuda


def test_the_generator_computes_in_the_registered_precision(
    wired: tuple[HFGenerator, Any, Any],
) -> None:
    """bf16 greedy decoding did not reproduce between jobs; fp32 did."""
    generator, _, fake = wired
    assert fake.dtype is torch.float32
    assert generator.computed_dtype == "float32"


def test_the_precision_can_be_overridden_to_measure_what_it_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The determinism check compares bf16 against fp32; an unoverridable default would have it
    write fp32 under both labels and erase the measurement the registration rests on."""
    tokenizer, fake = _FakeTokenizer(), _FakeModel()
    monkeypatch.setattr(model_module, "_require_tokenizer", lambda *_: tokenizer)
    monkeypatch.setattr(model_module.AutoModelForCausalLM, "from_pretrained", lambda *a, **k: fake)
    generator = HFGenerator(device="cpu", dtype="bfloat16")
    assert fake.dtype is torch.bfloat16
    assert generator.computed_dtype == "bfloat16", "read back from the model, not the request"
