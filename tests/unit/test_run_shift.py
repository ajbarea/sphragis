"""`scripts/run_shift.py`: the run-wide shift a pilot carries beyond its change-by-run noise."""

from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("run_shift", ROOT / "scripts" / "run_shift.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_shift = _load()
SHARED, PER_EXAMPLE = 0.03, 0.10


def _runs(shift_sd: float, *, seed: int = 3, n_runs: int = 400) -> list[tuple[dict, int]]:
    """Runs of 300 examples in 150 changes: change-by-run noise at known sds, plus a shift every
    example of a run shares, drawn at `shift_sd`."""
    rng = random.Random(seed)
    sizes = {f"c{i}": 1 + i % 3 for i in range(150)}
    halves = ("o-a", "o-b")
    runs = []
    for run in range(1, n_runs + 1):
        shift = rng.gauss(0.0, shift_sd)
        results: dict = {f"adapter:{x}|{y}|s{run}": [] for x in halves for y in halves}
        for change, size in sizes.items():
            common, half = rng.gauss(0.0, SHARED), rng.choice(halves)
            sibling = halves[1 - halves.index(half)]
            for j in range(size):
                value = -1.0 + shift + common + rng.gauss(0.0, PER_EXAMPLE)
                example = {"id": f"{change}-{j}", "change_id": change}
                results[f"adapter:{half}|{half}|s{run}"].append(example | {"lp": value})
                results[f"adapter:{sibling}|{half}|s{run}"].append(example | {"lp": -1.0})
        runs.append((results, run))
    return runs


@pytest.mark.parametrize("shift_sd", [0.0, 0.01])
def test_the_run_wide_part_is_what_the_change_noise_does_not_explain(shift_sd: float) -> None:
    read = run_shift.run_wide(
        _runs(shift_sd), pair=("o-a", "o-b"), metric="lp", noise=(SHARED, PER_EXAMPLE)
    )
    assert (read["examples"], read["changes"]) == (300, 150)
    assert read["sum_squared_change_sizes"] == 50 * (1 + 4 + 9)
    # Within Monte Carlo error of the shift's variance over 400 runs, and of zero without one.
    assert read["run_wide_variance"] == pytest.approx(shift_sd**2, abs=3e-5)
    # The upper bound sits above the estimate, and what it moves onto a run's mean stays below
    # the worst case in which all of the shared part were run-wide.
    assert read["run_wide_variance_upper"] > read["run_wide_variance"]
    assert 0.0 <= read["misplaced_on_run_mean_upper"] <= read["worst_case_on_run_mean"]
    assert read["run_wide_share_of_shared"] == pytest.approx(read["run_wide_variance"] / SHARED**2)
