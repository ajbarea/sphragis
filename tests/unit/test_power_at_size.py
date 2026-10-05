"""`scripts/power_at_size.py`: the registered lift read from the simulation, power at a size."""

from __future__ import annotations

import importlib.util
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from sphragis.experiment.partitions import K_MAX
from sphragis.measure.stats import Cluster

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> ModuleType:
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


power_at_size = _load("power_at_size")
partition_sensitivity = sys.modules["partition_sensitivity"]

_CALIBRATION = {"pilot_estimate": 0.012, "sizing_bound_90": 0.015}


def _simulation(**fields: Any) -> dict:
    by_level = {
        "0.95": {"by_cells": {"2": {"lift": 0.04, "minimum_detectable_effect": 0.02}}},
        "0.975": {"by_cells": {"2": {"lift": 0.05, "minimum_detectable_effect": 0.025}}},
    }
    return {
        "org": "openstack",
        "runs": 4,
        "planned_changes": 2_017,
        "trials": 40,
        "resamples": 200,
        "redraw": 0.055,
        "spread_targets": _CALIBRATION,
        "by_target": {
            "sizing_bound_90": {"calibration": {"sigma_run": 0.003}, "by_level": by_level}
        },
        **fields,
    }


def _report(**fields: Any) -> dict:
    return {
        "org": "openstack",
        "window": "test",
        "changes": 1_800,
        "levels": [0.975, 0.95],
        "bounds": {"0.975": 0.025, "0.95": 0.02},
        "sensitivity": {
            "org": "openstack",
            "runs": 4,
            "planned_changes": 2_017,
            "cells": 2,
            "spread_target": "sizing_bound_90",
            "spread_targets": _CALIBRATION,
        },
        **fields,
    }


def test_the_lift_and_run_shift_are_the_ones_that_registered_each_bound() -> None:
    sigma_run, lifts = power_at_size.registered_lifts(_report(), _simulation())
    assert sigma_run == 0.003
    assert lifts == {0.975: {"lift": 0.05, "bound": 0.025}, 0.95: {"lift": 0.04, "bound": 0.02}}


@pytest.mark.parametrize(
    ("report", "simulation", "match"),
    [
        (_report(), _simulation(runs=24), "runs 4"),
        (_report(), _simulation(planned_changes=2_000), "planned_changes 2017"),
        (_report(), _simulation(org="wikimedia"), "org 'openstack'"),
        (_report(bounds={"0.975": 0.03, "0.95": 0.02}), _simulation(), "bound at 0.975"),
        (
            _report(),
            _simulation(spread_targets={**_CALIBRATION, "pilot_estimate": 0.02}),
            "calibrated on another pilot",
        ),
        (_report(org="wikimedia"), _simulation(), "report for 'wikimedia'"),
    ],
)
def test_a_report_read_under_another_simulation_is_refused(
    report: dict, simulation: dict, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        power_at_size.registered_lifts(report, simulation)


@pytest.fixture
def small_pool() -> list:
    """Four projects of binary-outcome changes, and admissible-looking partitions of them."""
    rng = random.Random(5)
    projects = [f"p{i}" for i in range(4)]
    pool = [
        (
            Cluster(f"c{i}", (float(rng.random() < 0.3),) * 2, (float(rng.random() < 0.3),) * 2),
            projects[i % 4],
        )
        for i in range(60)
    ]
    sides = [(0, 0, 1, 1), (0, 1, 0, 1), (0, 1, 1, 0), (1, 0, 0, 1)]
    partitions = [dict(zip(projects, sides[i % 4], strict=True)) for i in range(2 * K_MAX)]
    partition_sensitivity._init(partitions)
    return pool


def test_power_rises_with_the_lift_and_carries_its_monte_carlo_error(small_pool: list) -> None:
    simulation = _simulation()
    lifts = {0.95: {"lift": 0.0, "bound": 0.0}, 0.975: {"lift": 0.6, "bound": 0.3}}
    with ThreadPoolExecutor(2) as executor:
        out = power_at_size.power_at(
            executor,
            small_pool,
            changes=80,
            sigma_run=0.0,
            lifts=lifts,
            simulation=simulation,
        )
    null, lifted = out[0.95]["power"], out[0.975]["power"]
    assert null < 0.3 < 0.9 < lifted
    for at in out.values():
        p = at["power"]
        assert at["mc_se"] == pytest.approx((p * (1 - p) / simulation["trials"]) ** 0.5)
    assert out[0.975]["lift"] == 0.6 and out[0.975]["bound"] == 0.3


def test_a_size_that_is_not_a_whole_count_is_refused(small_pool: list) -> None:
    with pytest.raises(ValueError, match="whole number"):
        power_at_size.power_at(
            None, small_pool, changes=0, sigma_run=0.0, lifts={}, simulation=_simulation()
        )


def test_the_trials_take_the_seed_the_simulation_ran_at() -> None:
    assert power_at_size.simulation_seed(_simulation(seed=7)) == 7
    # Simulations that predate the record ran at partition_sensitivity.py's default.
    assert power_at_size.simulation_seed(_simulation()) == (
        partition_sensitivity.parser.get_default("seed")
    )
