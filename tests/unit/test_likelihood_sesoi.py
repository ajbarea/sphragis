"""The registered rule between the positive control's exchange rate and the planted one."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "likelihood_sesoi.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("likelihood_sesoi", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _planted(rate: float | None, interval: list[float] | None) -> dict:
    return {"rate": rate, "rate_95": interval}


@pytest.mark.parametrize(
    ("planted", "rate", "source"),
    [
        (_planted(1.6, [1.5, 1.7]), 1.6, "planted"),
        (_planted(2.1, [1.9, 2.4]), 2.1, "planted"),
        (_planted(1.7, [1.5, 1.9]), 1.8, "positive_control"),
        (_planted(1.8, [1.8, 2.0]), 1.8, "positive_control"),
        (_planted(None, None), 1.8, "positive_control"),
        (_planted(1.6, None), 1.8, "positive_control"),
    ],
)
def test_the_planted_rate_replaces_the_control_only_when_its_interval_excludes_it(
    planted: dict, rate: float, source: str
) -> None:
    assert _module().chosen_rate(1.8, planted) == (rate, source)
