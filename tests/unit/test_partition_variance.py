"""The partition-variance moment estimates in scripts/partition_variance.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "partition_variance", ROOT / "scripts" / "partition_variance.py"
)
assert _spec is not None and _spec.loader is not None
partition_variance = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(partition_variance)
components = partition_variance.components


def test_the_partition_component_is_between_less_within_over_seeds() -> None:
    out = components({"a": {1: 0.0, 2: 0.2}, "b": {1: 0.4, 2: 0.6}, "c": {1: 0.8, 2: 1.0}})
    assert out["within_partition_variance"] == pytest.approx(0.02)
    assert out["variance_of_partition_means"] == pytest.approx(0.16)
    assert out["sigma_partition_squared"] == pytest.approx(0.16 - 0.01)
    assert out["range_of_partition_means"] == pytest.approx([0.1, 0.9])


def test_a_negative_component_is_reported_and_floored_only_in_the_sd() -> None:
    out = components({"a": {1: 0.0, 2: 1.0}, "b": {1: 0.1, 2: 0.9}})
    assert out["sigma_partition_squared"] < 0
    assert out["sigma_partition"] == 0.0


def test_an_unbalanced_layout_is_refused() -> None:
    with pytest.raises(SystemExit):
        components({"a": {1: 0.0, 2: 0.1}, "b": {1: 0.0, 3: 0.1}})
    with pytest.raises(SystemExit):
        components({"a": {1: 0.0}, "b": {1: 0.1}})
