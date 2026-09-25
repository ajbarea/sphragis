"""Shared plumbing for the adapter-instance cut: reading sources, freezing one adapter while
another trains, and writing an adapter where the geometry and attribution scripts expect to find
it. FedDPA (`scripts/dual_adapter_updates.py`) and FDLoRA (`scripts/fdlora_schedule.py`) both use
this; the schedule that moves parameters between the two adapters is what differs, and lives in
`sphragis.experiment.dual_adapter`'s sibling, `sphragis.experiment.fdlora`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from sphragis.experiment.dual_adapter import (
    check_label_collisions,
    client_label,
    freeze,
    save_adapter,
    sources,
    unfreeze,
)


def _args(
    *, source: list[str] | None = None, sources_file: Path | None = None
) -> argparse.Namespace:
    return argparse.Namespace(source=source or [], sources_file=sources_file)


class TestSources:
    def test_reads_repeated_source_flags(self) -> None:
        assert sources(_args(source=["a=x.jsonl", "b=y.jsonl"])) == ["a=x.jsonl", "b=y.jsonl"]

    def test_reads_a_sources_file_stripping_comments_and_blanks(self, tmp_path: Path) -> None:
        path = tmp_path / "sources.txt"
        path.write_text("a=x.jsonl  # a comment\n\n  # a full-line comment\nb=y.jsonl\n")
        assert sources(_args(sources_file=path)) == ["a=x.jsonl", "b=y.jsonl"]

    def test_flags_and_file_combine(self, tmp_path: Path) -> None:
        path = tmp_path / "sources.txt"
        path.write_text("b=y.jsonl\n")
        assert sources(_args(source=["a=x.jsonl"], sources_file=path)) == ["a=x.jsonl", "b=y.jsonl"]

    def test_no_sources_at_all_is_refused(self) -> None:
        with pytest.raises(SystemExit, match="no sources"):
            sources(_args())


class TestClientLabel:
    def test_a_colon_and_a_slash_both_become_path_safe(self) -> None:
        assert client_label("a:x-cpp/p", 0) == "a-x-cpp_p-c0"


class TestCheckLabelCollisions:
    def test_distinct_sources_that_munge_to_the_same_label_are_refused(self) -> None:
        plan = {"a-x:cpp/p": [["e1"]], "a:x-cpp/p": [["e2"]]}
        with pytest.raises(SystemExit, match="a-x-cpp_p-c0"):
            check_label_collisions(plan)

    def test_distinct_labels_pass_silently(self) -> None:
        check_label_collisions({"a": [["e1"], ["e2"]], "b": [["e3"]]})

    def test_a_source_alone_never_collides_with_itself(self) -> None:
        check_label_collisions({"a": [["e1"], ["e2"], ["e3"]]})


class _Param:
    def __init__(self, requires_grad: bool = True) -> None:
        self.requires_grad = requires_grad

    def requires_grad_(self, value: bool) -> None:
        self.requires_grad = value


class _Model:
    """A duck-typed stand-in for a PEFT model: named parameters, nothing else."""

    def __init__(self, names: list[str]) -> None:
        self._params = {name: _Param() for name in names}

    def named_parameters(self):
        return list(self._params.items())


class TestFreezeAndUnfreeze:
    def _model(self) -> _Model:
        return _Model(
            [
                "base_model.model.lora_A.global.weight",
                "base_model.model.lora_B.global.weight",
                "base_model.model.lora_A.local.weight",
                "base_model.model.lora_B.local.weight",
                "base_model.model.other.weight",
            ]
        )

    def test_freeze_stops_only_the_named_adapters_lora_parameters(self) -> None:
        model = self._model()
        freeze(model, "global")
        grads = {name: p.requires_grad for name, p in model.named_parameters()}
        assert grads["base_model.model.lora_A.global.weight"] is False
        assert grads["base_model.model.lora_B.global.weight"] is False
        assert grads["base_model.model.lora_A.local.weight"] is True
        assert grads["base_model.model.other.weight"] is True

    def test_unfreeze_undoes_freeze(self) -> None:
        model = self._model()
        freeze(model, "global")
        unfreeze(model, "global")
        assert all(p.requires_grad for _, p in model.named_parameters())


def test_save_adapter_lifts_the_nested_directory_up_one_level(tmp_path: Path) -> None:
    class _SaveableModel:
        def save_pretrained(self, destination: Path, selected_adapters: list[str]) -> None:
            (destination, adapter) = destination, selected_adapters[0]
            nested = destination / adapter
            nested.mkdir(parents=True)
            (nested / "adapter_model.safetensors").write_bytes(b"weights")
            (nested / "adapter_config.json").write_text("{}")

    destination = tmp_path / "client"
    save_adapter(_SaveableModel(), "global", destination)
    assert (destination / "adapter_model.safetensors").read_bytes() == b"weights"
    assert (destination / "adapter_config.json").exists()
    assert not (destination / "global").exists()
