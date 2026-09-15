"""The grid walk and the RQ1 gate, exercised with no model and no GPU.

February's confirmatory run is the first time this code meets real weights. These tests
are what make that not the first time it runs at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from sphragis.experiment.walk import gate, matched_vs_mismatched, walk

ORGS = ("alpha", "beta")
SEEDS = (1, 2, 3)


class FakeTrainer:
    """Names an adapter per (org, seed) and records what it was asked for."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def train(self, org: str, seed: int) -> str:
        self.calls.append((org, seed))
        return f"adapter-{org}-s{seed}"


def _window(org: str, n_changes: int = 12, per_change: int = 2) -> list[dict[str, Any]]:
    return [
        {
            "id": f"{org}:{c}:{e}",
            "change_id": f"{org}-I{c}",
            "before": "x=1",
            "after": f"{org} correct",
            "comments": ["fix it"],
        }
        for c in range(n_changes)
        for e in range(per_change)
    ]


WINDOWS = {org: _window(org) for org in ORGS}


class WindowAwareGenerator:
    """Answers correctly for one organization's window and wrongly for the other."""

    def __init__(self, solves: str | None) -> None:
        self.solves = solves

    def generate(self, prompt: str) -> str:
        return f"{self.solves} correct" if self.solves else "unadapted noise"


def _factory(record: list[str | None]):
    def build(adapter: str | None):
        record.append(adapter)
        return WindowAwareGenerator(None if adapter is None else adapter.split("-")[1])

    return build


def test_walk_trains_each_adapter_once_and_evaluates_every_condition() -> None:
    trainer = FakeTrainer()
    built: list[str | None] = []
    results = walk(
        orgs=ORGS, seeds=SEEDS, windows=WINDOWS, trainer=trainer, generator_for=_factory(built)
    )
    # Two orgs, three seeds: six adapters, each trained exactly once.
    assert len(trainer.calls) == 6
    assert len(set(trainer.calls)) == 6
    # Two base evaluations plus 2 trained x 2 evaluated x 3 seeds.
    assert len(results) == 2 + 12
    assert "base|alpha" in results and "adapter:beta|alpha|s2" in results
    # One generator per distinct adapter, not one per evaluation.
    assert len(built) == 7


def test_walk_evaluates_each_condition_on_the_named_window() -> None:
    results = walk(
        orgs=ORGS, seeds=SEEDS, windows=WINDOWS, trainer=FakeTrainer(), generator_for=_factory([])
    )
    assert {r["id"].split(":")[0] for r in results["adapter:beta|alpha|s1"]} == {"alpha"}
    assert {r["id"].split(":")[0] for r in results["adapter:alpha|beta|s1"]} == {"beta"}


def test_walk_refuses_an_org_with_no_window() -> None:
    with pytest.raises(ValueError, match="no evaluation window"):
        walk(
            orgs=("alpha", "gamma"),
            seeds=(1,),
            windows=WINDOWS,
            trainer=FakeTrainer(),
            generator_for=_factory([]),
        )


def test_matched_beats_mismatched_when_the_fingerprint_is_real() -> None:
    results = walk(
        orgs=ORGS, seeds=SEEDS, windows=WINDOWS, trainer=FakeTrainer(), generator_for=_factory([])
    )
    interval = matched_vs_mismatched(
        results, eval_org="alpha", other_org="beta", seed=1, bootstrap_seed=0
    )
    assert interval["estimate"] == pytest.approx(1.0)
    assert interval["low"] > 0.0


def test_gate_passes_when_both_organizations_show_the_effect() -> None:
    results = walk(
        orgs=ORGS, seeds=SEEDS, windows=WINDOWS, trainer=FakeTrainer(), generator_for=_factory([])
    )
    verdict = gate(results, orgs=ORGS, seeds=SEEDS, bootstrap_seed=0)
    assert verdict["verdict"] == "pass"
    assert set(verdict["binding"]) == set(ORGS)
    assert set(verdict["per_seed"]["alpha"]) == set(SEEDS)


def test_gate_fails_when_no_adapter_is_organization_specific() -> None:
    """Every adapter solves everything: adaptation works, the fingerprint claim does not."""

    def build(adapter: str | None):
        return WindowAwareGenerator("alpha" if adapter else None)

    results = walk(
        orgs=ORGS, seeds=SEEDS, windows=WINDOWS, trainer=FakeTrainer(), generator_for=build
    )
    verdict = gate(results, orgs=ORGS, seeds=SEEDS, bootstrap_seed=0)
    # Alpha's window is solved by both adapters, beta's by neither: no paired difference
    # either way, so neither interval can exclude zero.
    assert verdict["verdict"] == "fail"


def test_gate_binds_to_the_median_seed_and_reports_every_seed() -> None:
    """The registered rule reads one seed's interval; the rest must still be visible."""
    results = walk(
        orgs=ORGS, seeds=SEEDS, windows=WINDOWS, trainer=FakeTrainer(), generator_for=_factory([])
    )
    verdict = gate(results, orgs=ORGS, seeds=SEEDS, bootstrap_seed=0)
    for org in ORGS:
        assert verdict["binding"][org]["binding_seed"] in {float(s) for s in SEEDS}
        assert len(verdict["per_seed"][org]) == len(SEEDS)


def test_gate_is_defined_over_exactly_two_organizations() -> None:
    with pytest.raises(ValueError, match="exactly two"):
        gate({}, orgs=("alpha",), seeds=SEEDS, bootstrap_seed=0)


class SeedlessTrainer(FakeTrainer):
    """Names adapters by organization only, as a trainer keyed on an output dir might."""

    def train(self, org: str, seed: int) -> str:
        self.calls.append((org, seed))
        return f"/scratch/adapters/{org}"


def test_walk_refuses_a_trainer_whose_handles_collapse_the_seed_replication() -> None:
    with pytest.raises(ValueError, match="same adapter handle"):
        walk(
            orgs=ORGS,
            seeds=SEEDS,
            windows=WINDOWS,
            trainer=SeedlessTrainer(),
            generator_for=_factory([]),
        )


@pytest.mark.parametrize("seeds", [(1, 1, 2), (1, 2), (1, 2, 3, 4)])
def test_walk_and_gate_refuse_seed_sets_with_no_single_median(seeds: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="seeds"):
        walk(
            orgs=ORGS,
            seeds=seeds,
            windows=WINDOWS,
            trainer=FakeTrainer(),
            generator_for=_factory([]),
        )
    with pytest.raises(ValueError, match="seeds"):
        gate({}, orgs=ORGS, seeds=seeds, bootstrap_seed=0)


def test_walk_never_holds_two_generators_at_once() -> None:
    """Seven cached 7B generators need about 105 GB against a GH200's 102."""
    import gc
    import weakref

    alive: list[weakref.ref] = []

    def build(adapter: str | None):
        gc.collect()
        live = [ref for ref in alive if ref() is not None]
        assert not live, f"{len(live)} generator(s) still alive when building another"
        generator = WindowAwareGenerator(None if adapter is None else adapter.split("-")[1])
        alive.append(weakref.ref(generator))
        return generator

    results = walk(
        orgs=ORGS, seeds=SEEDS, windows=WINDOWS, trainer=FakeTrainer(), generator_for=build
    )
    assert len(results) == 14
    assert len(alive) == 7
