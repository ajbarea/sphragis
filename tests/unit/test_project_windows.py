"""The dev-window check that decides whether the sealed window may be projected."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "project_windows", ROOT / "scripts" / "project_windows.py"
)
assert _spec is not None and _spec.loader is not None
project_windows = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(project_windows)


@pytest.mark.parametrize(
    ("miss", "reading"),
    [(0.0, "holds"), (0.15, "holds"), (-0.15, "holds"), (0.151, "refuse"), (-0.199, "lower_bound")],
)
def test_only_an_over_prediction_refuses(miss: float, reading: str) -> None:
    """Over-predicting could overstate the window; under-predicting makes it a lower bound."""
    assert project_windows.dev_reading(miss, tolerance=0.15) == reading
