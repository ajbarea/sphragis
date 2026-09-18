"""The grid walk and the RQ1 gate, exercised with no model and no GPU.

February's confirmatory run is the first time this code meets real weights. These tests
are what make that not the first time it runs at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from sphragis.experiment.walk import (
    crossed_gate,
    gate,
    gate_under_each_estimand,
    matched_vs_mismatched,
    walk,
)
from sphragis.measure.stats import change_averaged_difference

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


def test_both_estimands_are_computed_and_neither_is_named_primary() -> None:
    """The choice is a Stage 1 registration, so the run must not quietly make it."""
    results = walk(
        orgs=ORGS, seeds=SEEDS, windows=WINDOWS, trainer=FakeTrainer(), generator_for=_factory([])
    )
    outcome = gate_under_each_estimand(results, orgs=ORGS, seeds=SEEDS, bootstrap_seed=0)
    assert set(outcome["by_estimand"]) == {"pooled", "change_averaged"}
    assert outcome["verdicts"] == {"pooled": "pass", "change_averaged": "pass"}
    assert outcome["agree"] is True
    assert "registered" not in outcome
    assert "primary" not in outcome


def test_the_registered_path_still_reads_the_pooled_estimand() -> None:
    results = walk(
        orgs=ORGS, seeds=SEEDS, windows=WINDOWS, trainer=FakeTrainer(), generator_for=_factory([])
    )
    outcome = gate_under_each_estimand(results, orgs=ORGS, seeds=SEEDS, bootstrap_seed=0)
    assert outcome["by_estimand"]["pooled"] == gate(
        results, orgs=ORGS, seeds=SEEDS, bootstrap_seed=0
    )


def _rows(org: str, per_change: dict[str, list[float]], metric: str = "exact_match") -> list[dict]:
    return [
        {"id": f"{org}:{change}:{i}", "change_id": change, metric: value}
        for change, values in per_change.items()
        for i, value in enumerate(values)
    ]


def _opposed_results() -> dict[str, list[dict]]:
    """Two organizations where the estimands reach opposite conclusions, mirrored.

    On alpha one large change carries the effect and the many small ones do not, so pooling
    supports the hypothesis and averaging over changes refutes it. Beta is the mirror. Each
    estimand is therefore "mixed", but mixed on the opposite organization.
    """
    wide, singletons = 4, 16
    alpha_t = {
        **{f"w{i}": [1.0] * 20 for i in range(wide)},
        **{f"s{i}": [0.0] for i in range(singletons)},
    }
    alpha_c = {
        **{f"w{i}": [0.0] * 20 for i in range(wide)},
        **{f"s{i}": [1.0] for i in range(singletons)},
    }
    results: dict[str, list[dict]] = {}
    for seed in SEEDS:
        results[f"adapter:alpha|alpha|s{seed}"] = _rows("alpha", alpha_t)
        results[f"adapter:beta|alpha|s{seed}"] = _rows("alpha", alpha_c)
        # Beta mirrored: the matched adapter is the one that loses the large change.
        results[f"adapter:beta|beta|s{seed}"] = _rows("beta", alpha_c)
        results[f"adapter:alpha|beta|s{seed}"] = _rows("beta", alpha_t)
    return results


def test_agree_is_false_when_the_estimands_swap_which_org_supports_the_claim() -> None:
    """Two "mixed" verdicts are not agreement when they are mixed on different orgs.

    Comparing the collapsed verdict strings calls this case agreement and records that the
    estimand choice did not matter, in the one situation where it mattered most.
    """
    outcome = gate_under_each_estimand(_opposed_results(), orgs=ORGS, seeds=SEEDS, bootstrap_seed=0)
    assert outcome["verdicts"] == {"pooled": "mixed", "change_averaged": "mixed"}
    assert outcome["supported"]["pooled"] != outcome["supported"]["change_averaged"]
    assert outcome["agree"] is False


def test_the_two_estimands_are_actually_both_run() -> None:
    """A fixture where they differ, so passing one estimator twice cannot pass silently."""
    results = _opposed_results()
    outcome = gate_under_each_estimand(results, orgs=ORGS, seeds=SEEDS, bootstrap_seed=0)
    pooled = outcome["by_estimand"]["pooled"]["binding"]["alpha"]["estimate"]
    averaged = outcome["by_estimand"]["change_averaged"]["binding"]["alpha"]["estimate"]
    assert pooled > 0.0 > averaged
    assert outcome["by_estimand"]["change_averaged"] == gate(
        results, orgs=ORGS, seeds=SEEDS, bootstrap_seed=0, estimator=change_averaged_difference
    )


def test_crossed_gate_passes_when_both_organizations_show_the_effect() -> None:
    results = walk(
        orgs=ORGS, seeds=SEEDS, windows=WINDOWS, trainer=FakeTrainer(), generator_for=_factory([])
    )
    verdict = crossed_gate(results, orgs=ORGS, seeds=SEEDS, bootstrap_seed=0)
    assert verdict["verdict"] == "pass"
    assert {org: verdict["per_org"][org]["seeds"] for org in ORGS} == {org: 3.0 for org in ORGS}


def test_crossed_gate_averages_every_seed_rather_than_binding_one() -> None:
    """Seed 2's adapters learned nothing: the median-seed rule never sees it, this must."""

    def build(adapter: str | None):
        if adapter is None or adapter.endswith("-s2"):
            return WindowAwareGenerator(None)
        return WindowAwareGenerator(adapter.split("-")[1])

    results = walk(
        orgs=ORGS, seeds=SEEDS, windows=WINDOWS, trainer=FakeTrainer(), generator_for=build
    )
    verdict = crossed_gate(results, orgs=ORGS, seeds=SEEDS, bootstrap_seed=0)
    assert verdict["per_org"]["alpha"]["estimate"] == pytest.approx(2 / 3)
    assert gate(results, orgs=ORGS, seeds=SEEDS, bootstrap_seed=0)["binding"]["alpha"][
        "estimate"
    ] == pytest.approx(1.0)


def test_crossed_gate_fails_when_no_adapter_is_organization_specific() -> None:
    def build(adapter: str | None):
        return WindowAwareGenerator("alpha" if adapter else None)

    results = walk(
        orgs=ORGS, seeds=SEEDS, windows=WINDOWS, trainer=FakeTrainer(), generator_for=build
    )
    assert crossed_gate(results, orgs=ORGS, seeds=SEEDS, bootstrap_seed=0)["verdict"] == "fail"


def test_crossed_gate_is_defined_over_exactly_two_organizations() -> None:
    with pytest.raises(ValueError, match="exactly two"):
        crossed_gate({}, orgs=("alpha",), seeds=SEEDS, bootstrap_seed=0)
