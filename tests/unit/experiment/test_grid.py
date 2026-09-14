"""The condition grid: what runs, how often, and what the base arm does not repeat."""

from __future__ import annotations

from sphragis.experiment.grid import EvalRun, eval_runs, run_id, training_runs

ORGS = ("openstack", "qt")
SEEDS = (1, 2, 3)


def test_one_training_run_per_organization_and_seed() -> None:
    runs = training_runs(ORGS, SEEDS)
    assert len(runs) == 6
    assert {(r.org, r.seed) for r in runs} == {(o, s) for o in ORGS for s in SEEDS}


def test_the_base_arm_is_evaluated_once_per_window_not_once_per_seed() -> None:
    base = [r for r in eval_runs(ORGS, SEEDS) if r.condition == "base"]
    assert len(base) == 2
    assert {r.eval_org for r in base} == set(ORGS)
    assert all(r.seed is None for r in base)


def test_each_adapter_is_evaluated_on_every_window_at_every_seed() -> None:
    adapters = [r for r in eval_runs(ORGS, SEEDS) if r.condition != "base"]
    assert len(adapters) == 12
    assert {(r.condition, r.eval_org, r.seed) for r in adapters} == {
        (f"adapter:{o}", e, s) for o in ORGS for e in ORGS for s in SEEDS
    }


def test_the_whole_grid_is_fourteen_evaluations() -> None:
    assert len(eval_runs(ORGS, SEEDS)) == 14


def test_run_ids_are_unique_and_stable() -> None:
    runs = eval_runs(ORGS, SEEDS)
    ids = [run_id(r) for r in runs]
    assert len(set(ids)) == len(ids)
    assert run_id(EvalRun("base", "qt", None)) == "base|qt"
    assert run_id(EvalRun("adapter:qt", "openstack", 2)) == "adapter:qt|openstack|s2"
