"""scripts/fdlora_schedule.py's and scripts/dual_adapter_updates.py's own wiring, run for real.

peft and sphragis.experiment.model need torch, which this environment does not have (see
test_model_stack.py), so both are replaced in `sys.modules` before the real script is loaded and
executed: an adapter's state is one float, and training sums a client's own signal into it, scaled
by the epoch count it was called with. What is under test is not the arithmetic -- that belongs to
sphragis.experiment.fdlora and its own tests -- but whether the script wires validate_schedule,
check_label_collisions, round0_seed and the output fields together in the order and shape it
claims to.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from sphragis.corpus.load import write_derived_file
from sphragis.experiment.fdlora import local_provenance

_ROOT = Path(__file__).resolve().parents[3]
_FDLORA = _ROOT / "scripts" / "fdlora_schedule.py"
_DUAL = _ROOT / "scripts" / "dual_adapter_updates.py"

_TRAINING = {"epochs": 2, "learning_rate": 2e-4, "warmup_ratio": 0.03, "max_seq_length": 2048}


class _Tensor:
    """A scalar standing in for an adapter's parameters: mutable, detach/clone/+/ /."""

    def __init__(self, value: float) -> None:
        self.value = float(value)

    def detach(self) -> _Tensor:
        return self

    def clone(self) -> _Tensor:
        return _Tensor(self.value)

    def __add__(self, other: _Tensor) -> _Tensor:
        return _Tensor(self.value + other.value)

    def __truediv__(self, n: float) -> _Tensor:
        return _Tensor(self.value / n)


class _Param:
    def requires_grad_(self, value: bool) -> None:
        return None


class _FakeModel:
    """One adapter, one float. `save_pretrained` writes it out where `save_adapter` expects it."""

    def __init__(self) -> None:
        self.adapters: dict[str, dict[str, _Tensor]] = {"default": {"w": _Tensor(0.0)}}
        self.active = "default"

    def add_adapter(self, name: str, cfg: object) -> None:
        self.adapters[name] = {"w": _Tensor(0.0)}

    def set_adapter(self, name: str | list[str]) -> None:
        # `base_model.set_adapter([GLOBAL, LOCAL])` activates both; FedDPA's feddpa-t freezes the
        # first and trains the second, so the last name given is the one that actually moves.
        self.active = name if isinstance(name, str) else name[-1]

    @property
    def base_model(self) -> _FakeModel:
        return self

    def named_parameters(self):
        return [(f"base.lora_A.{adapter}.weight", _Param()) for adapter in self.adapters]

    def save_pretrained(self, destination: Path, selected_adapters: list[str]) -> None:
        (adapter,) = selected_adapters
        nested = Path(destination) / adapter
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "adapter_model.safetensors").write_text(
            json.dumps(self.adapters[adapter]["w"].value)
        )


class _Tok:
    pad_token_id = 0


@dataclass
class _Report:
    losses: list[float] = field(default_factory=lambda: [1.0, 0.5])
    skipped_steps: int = 0
    skipped_micro_batches: int = 0


TRAIN_CALLS: list[str] = []


def _train_adapter(model: _FakeModel, batch, *, pad_token_id, seed, budget=_TRAINING):
    TRAIN_CALLS.append(model.active)
    epochs = budget["epochs"]
    signal = sum(item["signal"] for item in batch) / len(batch)
    model.adapters[model.active]["w"].value += epochs * signal
    return _Report()


def _get_peft_model_state_dict(model: _FakeModel, adapter_name: str) -> dict[str, _Tensor]:
    return model.adapters[adapter_name]  # live, like real PEFT


def _set_peft_model_state_dict(model: _FakeModel, state, adapter_name: str) -> None:
    for key, value in state.items():
        model.adapters[adapter_name][key].value = value.value


@pytest.fixture
def fake_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install fake `peft` and `sphragis.experiment.model` modules for a script to import."""
    TRAIN_CALLS.clear()
    peft = types.ModuleType("peft")
    peft.get_peft_model_state_dict = _get_peft_model_state_dict  # ty: ignore[unresolved-attribute]
    peft.set_peft_model_state_dict = _set_peft_model_state_dict  # ty: ignore[unresolved-attribute]
    monkeypatch.setitem(sys.modules, "peft", peft)

    model_module = types.ModuleType("sphragis.experiment.model")
    model_module.LORA = object()  # ty: ignore[unresolved-attribute]
    model_module.MODEL_ID = "fake-model"  # ty: ignore[unresolved-attribute]
    model_module.TRAINING = _TRAINING  # ty: ignore[unresolved-attribute]
    model_module._require_tokenizer = lambda model_id: _Tok()  # ty: ignore[unresolved-attribute]
    model_module.attach_adapter = (  # ty: ignore[unresolved-attribute]
        lambda model_id, seed, device: (_FakeModel(), _Tok())
    )
    model_module.run_provenance = lambda: {}  # ty: ignore[unresolved-attribute]
    model_module.train_adapter = _train_adapter  # ty: ignore[unresolved-attribute]
    monkeypatch.setitem(sys.modules, "sphragis.experiment.model", model_module)


def _load(script: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(f"_under_test_{script.stem}", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.build_supervised = (  # ty: ignore[unresolved-attribute]
        lambda tok, row, prompt_builder, max_length: {"signal": row["signal"]}
    )
    return mod


def _write_corpus(root: Path, spec: Mapping[str, Sequence[float]]) -> list[str]:
    """spec: source name -> one signal a client. Each file is a validly recorded derived corpus,
    cut from a source root that does not exist, which `derived_file_rows` reads as unchanged
    (`stale_derived_file` -> `_stale_source`: a source root that is not a directory is not
    stale), so no real refined tree has to be built for it to pass.
    """
    root.mkdir(parents=True, exist_ok=True)
    specs = []
    for sidx, (name, signals) in enumerate(spec.items()):
        rows = [
            {"id": f"{sidx}-{c}-{j}", "change_id": f"{sidx}-{c}", "signal": s}
            for c, s in enumerate(signals)
            for j in range(4)
        ]
        path = root / f"src{sidx}.jsonl"
        write_derived_file(path, rows, sources=[(root / "no-such-source", f"org{sidx}")])
        specs.append(f"{name}={path.name}")
    return specs


def _prepare(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    script: Path,
    spec: Mapping[str, Sequence[float]],
    extra_args: Sequence[str] = (),
) -> types.ModuleType:
    corpus_root = tmp_path / "corpus"
    specs = _write_corpus(corpus_root, spec)
    mod = _load(script)
    argv = ["x"]
    for one in specs:
        argv += ["--source", one]
    argv += [
        "--corpus-root",
        str(corpus_root),
        "--client-size",
        "4",
        "--max-per-change",
        "4",
        "--adapters",
        str(tmp_path / "adapters"),
        "--out",
        str(tmp_path / "out.json"),
        "--device",
        "cpu",
        *extra_args,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    return mod


def _read_adapters(directory: Path) -> dict[str, float]:
    return {
        d.name: json.loads((d / "adapter_model.safetensors").read_text())
        for d in sorted(directory.iterdir())
    }


def _client_signal(entry: dict, spec: Mapping[str, Sequence[float]]) -> float:
    """The signal a saved client trained on, read back from the ids the report itself names."""
    cidx = int(entry["ids"][0].split("-")[1])
    return spec[entry["source"]][cidx]


# --- (a) the round-0 seed averages over every client, not per source -------------------------


def test_round0_seed_averages_every_client_across_every_source(
    fake_stack: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two sources with unequal client counts (2 and 3): a per-source average would seed each
    client's global module from its own source's mean, not the true mean of all five, and the
    two would then imply two different seeds rather than one shared one."""
    spec = {"a:cpp/x": [1.0, 3.0], "b:cpp/y": [10.0, 20.0, 30.0]}
    rounds, inner_steps = 2, 3
    mod = _prepare(
        monkeypatch,
        tmp_path,
        _FDLORA,
        spec,
        ["--rounds", str(rounds), "--inner-steps", str(inner_steps), "--sync-period", "10"],
    )
    mod.main()
    out = _read_adapters(tmp_path / "adapters")
    report = json.loads((tmp_path / "out.json").read_text())

    p0 = {label: out[f"{label}-p0"] for label in report["clients"]}
    true_seed = sum(p0.values()) / len(p0)
    implied_seeds = {
        label: out[label] - rounds * inner_steps * _client_signal(entry, spec)
        for label, entry in report["clients"].items()
    }
    for label, implied in implied_seeds.items():
        assert implied == pytest.approx(true_seed), label


# --- (b) validate_schedule runs before Stage 1 trains anything -------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param(["--inner-steps", "0"], id="bad-inner-steps"),
        pytest.param(["--rounds", "6", "--sync-period", "3"], id="final-sync-collision"),
    ],
)
def test_validate_schedule_runs_before_any_training(
    fake_stack: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, extra: list[str]
) -> None:
    mod = _prepare(monkeypatch, tmp_path, _FDLORA, {"a:cpp/x": [1.0]}, extra)
    with pytest.raises(SystemExit):
        mod.main()
    assert not (tmp_path / "adapters").exists()
    assert not (tmp_path / "out.json").exists()
    # Stage 1 trains in memory before anything is saved to disk, so a validation moved to just
    # before the round-0 average would still leave no adapters directory behind; only a record of
    # whether training itself ran catches that move.
    assert TRAIN_CALLS == [], "Stage 1 trained a client before validate_schedule ran"


# --- (c) the output records local_equals and legacy_corpus -----------------------------------


def test_output_records_local_equals_and_legacy_corpus(
    fake_stack: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rounds, sync_period = 6, 5
    mod = _prepare(
        monkeypatch,
        tmp_path,
        _FDLORA,
        {"a:cpp/x": [1.0, 2.0]},
        [
            "--rounds",
            str(rounds),
            "--inner-steps",
            "3",
            "--sync-period",
            str(sync_period),
            "--legacy-corpus",
        ],
    )
    mod.main()
    report = json.loads((tmp_path / "out.json").read_text())
    assert report["local_equals"] == local_provenance(rounds, sync_period)
    assert report["legacy_corpus"] is True


def test_legacy_corpus_actually_gates_which_corpora_are_read(
    fake_stack: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`legacy=args.legacy_corpus`, not `legacy=True` unconditionally: a corpus file with no
    record must be refused by default and only accepted when `--legacy-corpus` asks for it."""
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir(parents=True)
    rows = [{"id": "0-0-0", "change_id": "0-0", "signal": 1.0} for _ in range(4)]
    unrecorded = corpus_root / "src0.jsonl"
    unrecorded.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    mod = _load(_FDLORA)
    argv = [
        "x",
        "--source",
        "a:cpp/x=src0.jsonl",
        "--corpus-root",
        str(corpus_root),
        "--client-size",
        "4",
        "--max-per-change",
        "4",
        "--adapters",
        str(tmp_path / "adapters"),
        "--out",
        str(tmp_path / "out.json"),
        "--device",
        "cpu",
        "--rounds",
        "6",
        "--sync-period",
        "5",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="no record"):
        mod.main()


# --- (d) a label collision is refused before any adapter is written, in both scripts ---------


@pytest.mark.parametrize("script", [_FDLORA, _DUAL], ids=lambda p: p.name)
def test_a_label_collision_is_refused_before_any_adapter_is_written(
    fake_stack: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, script: Path
) -> None:
    spec = {"a-x:cpp/p": [1.0], "a:x-cpp/p": [9.0]}
    extra = ["--rounds", "6", "--sync-period", "5"] if script is _FDLORA else []
    mod = _prepare(monkeypatch, tmp_path, script, spec, extra)
    with pytest.raises(SystemExit, match="both yield client label"):
        mod.main()
    assert not (tmp_path / "adapters").exists()


# --- a repeated source name is refused before any client trains, in both scripts -------------


@pytest.mark.parametrize("script", [_FDLORA, _DUAL], ids=lambda p: p.name)
def test_a_duplicate_source_name_is_refused_before_any_client_trains(
    fake_stack: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, script: Path
) -> None:
    corpus_root = tmp_path / "corpus"
    first = _write_corpus(corpus_root, {"a:cpp/x": [1.0]})
    second_file = _write_corpus(corpus_root, {"unused": [9.0]})[0].split("=")[1]
    duplicate = f"a:cpp/x={second_file}"
    mod = _load(script)
    argv = [
        "x",
        "--source",
        first[0],
        "--source",
        duplicate,
        "--corpus-root",
        str(corpus_root),
        "--client-size",
        "4",
        "--max-per-change",
        "4",
        "--adapters",
        str(tmp_path / "adapters"),
        "--out",
        str(tmp_path / "out.json"),
        "--device",
        "cpu",
    ]
    if script is _FDLORA:
        argv += ["--rounds", "6", "--sync-period", "5"]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="given twice"):
        mod.main()
    assert not (tmp_path / "adapters").exists()
