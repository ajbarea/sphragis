"""The memory probe's synthetic items, the one part of it that runs without a GPU."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("memory_probe", ROOT / "scripts" / "memory_probe.py")
assert _spec is not None and _spec.loader is not None
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


def test_items_have_the_supervised_layout() -> None:
    items = probe.synthetic_items(3, 10, 4, seed=0)
    assert len(items) == 3
    for item in items:
        assert len(item["input_ids"]) == len(item["labels"]) == len(item["attention_mask"]) == 10
        assert item["labels"][:6] == [-100] * 6
        assert item["labels"][6:] == item["input_ids"][6:]
        assert all(probe.TOKEN_RANGE[0] <= t < probe.TOKEN_RANGE[1] for t in item["input_ids"])


def test_items_replay_by_seed() -> None:
    assert probe.synthetic_items(2, 8, 2, seed=5) == probe.synthetic_items(2, 8, 2, seed=5)


@pytest.mark.parametrize("target", [0, 10, 11])
def test_target_must_leave_a_prompt(target: int) -> None:
    with pytest.raises(ValueError):
        probe.synthetic_items(1, 10, target, seed=0)


def test_cells_are_checked_before_a_model_loads() -> None:
    assert probe.check_lengths([512, 2048], 256, 2048) is None
    assert "bound" in probe.check_lengths([512, 4096], 256, 2048)
    assert "exceed" in probe.check_lengths([256, 2048], 256, 2048)


def test_the_defaults_pass_their_own_check() -> None:
    args = probe.parser.parse_args(["--out", "x.json"])
    assert probe.check_lengths(probe._ints(args.lengths), args.target_tokens, 2048) is None
