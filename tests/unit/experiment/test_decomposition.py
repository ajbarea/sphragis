"""The granularity decomposition: the half-split contrast and the organization beyond it."""

from __future__ import annotations

import json
from collections.abc import Callable
from fractions import Fraction
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest

from sphragis.corpus.github import GITHUB_ORGS
from sphragis.experiment import decomposition
from sphragis.experiment.cells import same_calibration
from sphragis.experiment.decomposition import (
    H2_PAIR,
    MIN_RESAMPLES,
    ORGANIZATIONS,
    REPLICATION_CONFIDENCE,
    REPLICATION_FAMILY,
    SUMMED_CONFIDENCE,
    below_sesoi,
    cell_verdict,
    cpp_supplement,
    decomposition_gate,
    design,
    detectable_effects,
    file_type_supplement,
    halves,
    holm_levels,
    holm_steps,
    holm_verdicts,
    is_cpp,
    matched_suffix,
    organization_clusters,
    project_clusters,
    reading,
    registered_read,
    replication,
    replication_gate,
    row_path,
)
from sphragis.experiment.grid import EvalRun, run_id

SEEDS = (1, 2, 3)
N_CHANGES = 12
ORGS = ("openstack", "qt", "chromium")

# (trained half, evaluated half, seed, change index) -> exact match of both examples
Score = Callable[[str, str, int, int], float]


def _results(
    score: Score, orgs: tuple[str, ...] = ORGS, n_changes: int = N_CHANGES
) -> dict[str, list[dict[str, Any]]]:
    """Every adapter half scored on every half, two examples a change."""
    all_halves = [h for org in orgs for h in halves(org)]
    return {
        run_id(EvalRun(f"adapter:{trained}", window, seed)): [
            {
                "id": f"{window}:{c}:{e}",
                "change_id": f"{window}-I{c}",
                "exact_match": score(trained, window, seed, c),
            }
            for c in range(n_changes)
            for e in range(2)
        ]
        for trained in all_halves
        for window in all_halves
        for seed in SEEDS
    }


def _org(half: str) -> str:
    return half.rsplit("-", 1)[0]


def _by_relation(own: float, sibling: float, foreign: float) -> Score:
    """Each change right or wrong by a share: the first `share * N_CHANGES` changes are right."""

    def score(trained: str, window: str, seed: int, change: int) -> float:
        if trained == window:
            share = own
        elif _org(trained) == _org(window):
            share = sibling
        else:
            share = foreign
        return 1.0 if change < round(N_CHANGES * share) else 0.0

    return score


# A registered bound for every cell of every organization, at both Holm levels.
BOUND = 0.05
DETECTABLE = {
    name: {org: {0.975: BOUND, 0.95: BOUND} for org in ORGANIZATIONS} for name in ("H1", "H2")
}


def _gate(score: Score, admitted: tuple[str, ...] = ORGS, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("detectable", DETECTABLE)
    return decomposition_gate(
        _results(score, orgs=admitted),
        seeds=SEEDS,
        bootstrap_seed=0,
        resamples=MIN_RESAMPLES,
        admitted=admitted,
        **kwargs,
    )


# The contrasts themselves


def test_halves_are_the_placebo_split_names() -> None:
    assert halves("qt") == ("qt-a", "qt-b")


def test_project_clusters_keep_the_halves_apart_with_own_as_treatment() -> None:
    per_half = project_clusters(_results(_by_relation(0.75, 0.25, 0.25)), org="qt", seed=1)
    assert [{c.change_id.split("-I")[0] for c in h} for h in per_half] == [{"qt-a"}, {"qt-b"}]
    treatment = [v for h in per_half for c in h for v in c.treatment]
    control = [v for h in per_half for c in h for v in c.control]
    assert sum(treatment) / len(treatment) == pytest.approx(0.75)
    assert sum(control) / len(control) == pytest.approx(0.25)


def test_organization_clusters_average_the_foreign_halves_per_example() -> None:
    def score(trained: str, window: str, seed: int, change: int) -> float:
        return {"chromium-a": 1.0, "chromium-b": 0.0}.get(trained, 0.5)

    per_half = organization_clusters(_results(score), org="qt", foreign="chromium", seed=1)
    clusters = [c for h in per_half for c in h]
    assert len(clusters) == 2 * N_CHANGES, "each change once, not once per foreign half"
    assert all(v == 0.5 for c in clusters for v in c.control)


def test_a_change_in_both_halves_is_refused() -> None:
    results = _results(_by_relation(0.75, 0.25, 0.25))
    for seed in SEEDS:
        for trained in halves("qt"):
            key = run_id(EvalRun(f"adapter:{trained}", "qt-b", seed))
            results[key] = [
                {**r, "change_id": r["change_id"].replace("qt-b", "qt-a")} for r in results[key]
            ]
    with pytest.raises(ValueError, match="both halves"):
        project_clusters(results, org="qt", seed=1)


def test_a_duplicated_id_in_the_second_foreign_half_is_refused() -> None:
    results = _results(_by_relation(0.5, 0.5, 0.5))
    key = run_id(EvalRun("adapter:chromium-b", "qt-a", 1))
    results[key] = [*results[key], {**results[key][0], "exact_match": 0.0}]
    with pytest.raises(ValueError, match="foreign arm 1"):
        organization_clusters(results, org="qt", foreign="chromium", seed=1)


def test_foreign_halves_disagreeing_on_a_change_id_are_refused() -> None:
    results = _results(_by_relation(0.5, 0.5, 0.5))
    key = run_id(EvalRun("adapter:chromium-b", "qt-a", 1))
    results[key] = [{**r, "change_id": "BOGUS"} for r in results[key]]
    with pytest.raises(ValueError, match="different changes"):
        organization_clusters(results, org="qt", foreign="chromium", seed=1)


def test_a_missing_cell_names_the_adapter_and_window() -> None:
    results = _results(_by_relation(0.5, 0.5, 0.5))
    del results[run_id(EvalRun("adapter:chromium-b", "qt-a", 1))]
    with pytest.raises(ValueError, match="chromium-b adapter was not scored on qt-a"):
        organization_clusters(results, org="qt", foreign="chromium", seed=1)


# The registered cells


def test_admitting_all_three_tests_every_confirmatory_cell_and_nothing_else() -> None:
    outcome = _gate(_by_relation(0.75, 0.25, 0.25))
    h1 = outcome["per_org"]["H1"]
    h2 = outcome["per_org"]["H2"]
    assert {o for o, c in h1.items() if c["role"] == "confirmatory"} == set(ORGS)
    assert {(o, c["foreign"]) for o, c in h2.items() if c["role"] == "confirmatory"} == {
        ("qt", "chromium"),
        ("chromium", "qt"),
    }
    assert h2["openstack"]["role"] == "exploratory"


def test_an_exploratory_cell_never_decides_a_verdict() -> None:
    def score(trained: str, window: str, seed: int, change: int) -> float:
        # H2 holds for Qt and Chromium; OpenStack's sibling half loses to Qt.
        if _org(window) == "openstack" and _org(trained) == "openstack" and trained != window:
            return 0.0
        return _by_relation(0.75, 0.5, 0.25)(trained, window, seed, change)

    outcome = _gate(score)
    assert outcome["per_org"]["H2"]["openstack"]["verdicts"][0.975] != "supported"
    assert outcome["verdicts"]["H2"] == "pass"


def test_admitting_openstack_and_qt_tests_h1_alone_at_the_whole_family_level() -> None:
    admitted = ("openstack", "qt")
    results = _results(_by_relation(0.75, 0.25, 0.25), orgs=admitted)
    outcome = decomposition_gate(
        results,
        admitted=admitted,
        seeds=SEEDS,
        bootstrap_seed=0,
        resamples=1_000,
        detectable=DETECTABLE,
    )
    assert outcome["holm_levels"] == [0.95]
    assert set(outcome["verdicts"]) == {"H1"}
    assert outcome["reading"] == "within-half"
    assert all(c["role"] == "exploratory" for c in outcome["per_org"]["H2"].values())


def test_the_gate_refuses_an_unregistered_seed_count() -> None:
    with pytest.raises(ValueError, match="odd number"):
        decomposition_gate(
            _results(_by_relation(0.75, 0.25, 0.25)),
            admitted=ORGS,
            seeds=(1, 2),
            bootstrap_seed=0,
            resamples=MIN_RESAMPLES,
            detectable=DETECTABLE,
        )


def test_the_gate_refuses_too_few_resamples() -> None:
    with pytest.raises(ValueError, match="resamples"):
        decomposition_gate(
            _results(_by_relation(0.75, 0.25, 0.25)),
            admitted=ORGS,
            seeds=SEEDS,
            bootstrap_seed=0,
            resamples=40,
            detectable=DETECTABLE,
        )


# The registered rule over admitted organizations


def test_the_registration_order_and_h2_pair_are_what_the_spec_says() -> None:
    assert ORGANIZATIONS == ("openstack", "wikimedia", "qt", "chromium")
    assert H2_PAIR == ("qt", "chromium")


_OPTIONAL = ("wikimedia", "qt", "chromium")

# Every admitted subset containing openstack, keyed by which of the other three are admitted.
_TRUTH_TABLE: dict[frozenset[str], dict[str, tuple[Any, ...]]] = {
    frozenset(): {
        "H1": ("openstack",),
        "H2": (),
        "exploratory_h2": (),
    },
    frozenset({"wikimedia"}): {
        "H1": ("openstack", "wikimedia"),
        "H2": (),
        "exploratory_h2": (("openstack", "wikimedia"), ("wikimedia", "openstack")),
    },
    frozenset({"qt"}): {
        "H1": ("openstack", "qt"),
        "H2": (),
        "exploratory_h2": (("openstack", "qt"), ("qt", "openstack")),
    },
    frozenset({"chromium"}): {
        "H1": ("openstack", "chromium"),
        "H2": (),
        "exploratory_h2": (("openstack", "chromium"), ("chromium", "openstack")),
    },
    frozenset({"wikimedia", "qt"}): {
        "H1": ("openstack", "wikimedia", "qt"),
        "H2": (),
        "exploratory_h2": (
            ("openstack", "wikimedia"),
            ("wikimedia", "openstack"),
            ("qt", "openstack"),
        ),
    },
    frozenset({"wikimedia", "chromium"}): {
        "H1": ("openstack", "wikimedia", "chromium"),
        "H2": (),
        "exploratory_h2": (
            ("openstack", "wikimedia"),
            ("wikimedia", "openstack"),
            ("chromium", "openstack"),
        ),
    },
    frozenset({"qt", "chromium"}): {
        "H1": ("openstack", "qt", "chromium"),
        "H2": (("qt", "chromium"), ("chromium", "qt")),
        "exploratory_h2": (("openstack", "qt"),),
    },
    frozenset({"wikimedia", "qt", "chromium"}): {
        "H1": ("openstack", "wikimedia", "qt", "chromium"),
        "H2": (("qt", "chromium"), ("chromium", "qt")),
        "exploratory_h2": (("openstack", "wikimedia"), ("wikimedia", "openstack")),
    },
}


@pytest.mark.parametrize(
    "optional",
    [frozenset(c) for n in range(len(_OPTIONAL) + 1) for c in combinations(_OPTIONAL, n)],
    ids=lambda s: "+".join(sorted(s)) or "openstack-only",
)
def test_design_matches_the_registered_rule_for_every_admitted_subset(
    optional: frozenset[str],
) -> None:
    expected = _TRUTH_TABLE[optional]
    outcome = design({"openstack", *optional})
    assert outcome["H1"] == expected["H1"]
    assert outcome["H2"] == expected["H2"]
    assert outcome["exploratory_h2"] == expected["exploratory_h2"]
    assert outcome["admitted"] == expected["H1"]


def test_design_refuses_an_unknown_organization() -> None:
    with pytest.raises(ValueError, match="unknown organization"):
        design(["openstack", "gitlab"])


def test_design_refuses_a_duplicate() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        design(["openstack", "qt", "qt"])


def test_design_refuses_an_admitted_set_without_openstack() -> None:
    with pytest.raises(ValueError, match="openstack"):
        design(["qt", "chromium"])


def test_design_orders_by_registration_order_never_input_order() -> None:
    expected = ("openstack", "qt", "chromium")
    assert design(["chromium", "qt", "openstack"])["H1"] == expected
    assert design(("qt", "openstack", "chromium"))["H1"] == expected
    assert design({"chromium", "qt", "openstack"})["H1"] == expected
    assert design(frozenset({"chromium", "openstack"}))["H1"] == ("openstack", "chromium")


def test_design_refuses_a_replication_member() -> None:
    with pytest.raises(ValueError, match="unknown organization"):
        design(["openstack", "apache"])


def test_replication_orders_members_and_reports_the_rest_as_not_collected() -> None:
    family = replication(["grafana", "apache"])
    assert family["members"] == ("apache", "grafana")
    assert family["not_collected"] == ("llvm", "dotnet")
    assert replication([])["not_collected"] == REPLICATION_FAMILY


@pytest.mark.parametrize(
    ("members", "match"),
    [
        (["apache", "openstack"], "unknown organization"),
        (["apache", "openjdk"], "unknown organization"),
        (["llvm", "llvm"], "duplicate"),
    ],
)
def test_replication_refuses_a_gerrit_late_or_repeated_member(members: list, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        replication(members)


# A development pilot's spread targets, as both its report and its simulation record them.
_CALIBRATION = {
    "pilot_lower_90": 0.010,
    "pilot_estimate": 0.012,
    "sizing_bound_90": 0.015,
    "pilot_upper_99": 0.020,
}


def _replication_cell(
    low: float,
    high: float,
    p: float,
    *,
    bound: float | None = 0.03,
    resamples: int = 10_000,
    org: str = "apache",
) -> dict:
    """A member's test-window report as `partition_pilot.py --replication` writes it."""
    return {
        "org": org,
        "estimate": (low + high) / 2,
        "levels": [REPLICATION_CONFIDENCE],
        "intervals": {REPLICATION_CONFIDENCE: {"low": low, "high": high}},
        "p_one_sided": p,
        "resamples": resamples,
        "bounds": {REPLICATION_CONFIDENCE: bound},
        "window": "test",
        "planted_convention": {"passed": True},
        "runs": 24,
        "k_from": {
            "file": f"partition-pilot-{org}.json",
            "org": org,
            "runs": 24,
            "spread_targets": _CALIBRATION,
        },
        "sensitivity": {
            "org": org,
            "cells": 1,
            "runs": 24,
            "spread_target": "sizing_bound_90",
            "spread_targets": _CALIBRATION,
        },
        "bootstrap_seed": 7,
    }


def _sim(bound: Any, org: str = "apache", *, runs: int = 24, by_level: Any = None) -> dict:
    """A member's partition-simulation artifact, its bound at the registered target and one cell."""
    levels = by_level or {"0.975": {"by_cells": {"1": {"minimum_detectable_effect": bound}}}}
    return {
        "org": org,
        "runs": runs,
        "spread_targets": _CALIBRATION,
        "by_target": {"sizing_bound_90": {"by_level": levels}},
    }


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    def freeze(*members: str) -> None:
        monkeypatch.setattr(decomposition, "REPLICATION_MEMBERS", members)

    return freeze


def test_replication_gate_reads_each_member_and_the_partial_conjunction(frozen) -> None:
    frozen("dotnet", "llvm", "apache")
    cells = {
        "apache": _replication_cell(0.02, 0.05, 0.0001),
        "llvm": _replication_cell(-0.004, 0.006, 0.3, bound=0.008, org="llvm"),
        "dotnet": _replication_cell(0.005, 0.03, 0.002, org="dotnet"),
    }
    simulations = {
        "apache": _sim(0.03),
        "llvm": _sim(0.008, "llvm"),
        "dotnet": _sim(0.03, "dotnet"),
    }
    out = replication_gate(cells, simulations=simulations)
    assert out["members"] == ("apache", "llvm", "dotnet")
    assert out["not_collected"] == ("grafana",)
    assert {o: c["verdict"] for o, c in out["cells"].items()} == {
        "apache": "supported",
        "llvm": "bounded",
        "dotnet": "supported",
    }
    assert out["cells"]["llvm"]["bound"] == 0.008
    assert out["cells"]["apache"]["meaningful"] and not out["cells"]["dotnet"]["meaningful"]
    # Adjusted: 3 * 0.0001, then 2 * 0.002, then 0.3: r = 2 at 0.0125.
    assert out["partial_conjunction"]["alpha"] == pytest.approx(0.0125)
    assert out["partial_conjunction"]["at_least"] == 2


def test_replication_gate_refuses_to_read_before_the_members_are_frozen() -> None:
    assert decomposition.REPLICATION_MEMBERS is None
    with pytest.raises(ValueError, match="not frozen"):
        replication_gate({}, simulations={})


def test_replication_gate_reads_a_report_and_simulation_back_from_json(frozen) -> None:
    frozen("llvm")
    cell = _replication_cell(-0.004, 0.006, 0.3, bound=0.008, org="llvm")
    loaded = json.loads(
        json.dumps({"cells": {"llvm": cell}, "sims": {"llvm": _sim(0.008, "llvm")}})
    )
    out = replication_gate(loaded["cells"], simulations=loaded["sims"])
    assert out["cells"]["llvm"]["verdict"] == "bounded"


def test_an_empty_family_has_no_partial_conjunction(frozen) -> None:
    frozen()
    out = replication_gate({}, simulations={})
    assert out["cells"] == {} and out["partial_conjunction"] is None
    assert out["not_collected"] == REPLICATION_FAMILY


def test_a_frozen_member_outside_the_family_is_refused(frozen) -> None:
    frozen("apache", "openjdk")
    with pytest.raises(ValueError, match="unknown organization"):
        replication_gate({}, simulations={})


_GOOD = _replication_cell(0.01, 0.02, 0.001)
_SIM = {"apache": _sim(0.03)}


@pytest.mark.parametrize(
    ("cells", "simulations", "match"),
    [
        ({"apache": _GOOD, "llvm": _GOOD}, _SIM, "cells for organization"),
        ({"apache": _GOOD, "openstack": _GOOD}, _SIM, "cells for organization"),
        ({"apache": _GOOD}, {**_SIM, "grafana": _sim(0.03, "grafana")}, "simulations for organ"),
        ({}, _SIM, "no cell and simulation"),
        ({"apache": _GOOD}, {}, "no cell and simulation"),
        ({"apache": _GOOD}, {"apache": 0.03}, "not its own"),
        ({"apache": _GOOD}, {"apache": _sim(0.03, "openstack")}, "not its own"),
        ({"apache": _GOOD}, {"apache": _sim(0.03, runs=40)}, "simulation is at K = 40"),
        (
            {"apache": _GOOD},
            {"apache": _sim(0.03, by_level={"0.95": {"by_cells": {}}})},
            "simulation: no bound at level",
        ),
        (
            {"apache": _GOOD},
            {"apache": _sim(0.03, by_level={"0.975": {"by_cells": {"3": {}}}})},
            "simulation: no bound for 1",
        ),
        (
            {"apache": _GOOD},
            {"apache": _sim(0.03, by_level={"0.975": {}, 0.975: {}})},
            "simulation: two keys name level",
        ),
        ({"apache": _GOOD}, {"apache": _sim(float("nan"))}, "no registered detectable effect"),
        ({"apache": _GOOD}, {"apache": _sim(None)}, "no registered detectable effect"),
        ({"apache": _GOOD}, {"apache": _sim(True)}, "no registered detectable effect"),
        ({"apache": _GOOD}, {"apache": _sim(0.0)}, "no registered detectable effect"),
        ({"apache": _GOOD}, {"apache": _sim(-0.3)}, "no registered detectable effect"),
        (
            {"apache": _replication_cell(0.01, 0.02, 0.001, bound=None)},
            _SIM,
            "computed under bound None",
        ),
        (
            {"apache": _replication_cell(0.01, 0.02, 0.001, bound=0.05)},
            _SIM,
            "computed under bound 0.05",
        ),
        (
            {"apache": _replication_cell(0.01, 0.02, 0.001, resamples=80)},
            _SIM,
            "at least 1000 resamples",
        ),
        (
            {"apache": _replication_cell(0.01, 0.02, 0.001, resamples=1_010)},
            _SIM,
            "between draws",
        ),
        (
            {"apache": _GOOD | {"intervals": {0.95: {"low": 0.0, "high": 1.0}}}},
            _SIM,
            "no interval at 0.975",
        ),
        ({"apache": _replication_cell(0.01, 0.02, 0.5)}, _SIM, "disagree"),
        ({"apache": _replication_cell(-0.01, 0.02, 0.001)}, _SIM, "disagree"),
        (
            {"apache": {k: v for k, v in _GOOD.items() if k != "resamples"}},
            _SIM,
            r"apache: the cell has no \['resamples'\]",
        ),
        ({"apache": _replication_cell(0.01, 0.02, 0.001, org="llvm")}, _SIM, "computed for 'llvm'"),
        ({"apache": _GOOD | {"bounds": []}}, _SIM, "apache: bounds"),
        ({"apache": _GOOD | {"window": "development"}}, _SIM, "development window"),
        ({"apache": _GOOD | {"planted_convention": {"passed": False}}}, _SIM, "check 5"),
        ({"apache": _GOOD | {"planted_convention": None}}, _SIM, "check 5 did not pass"),
        (
            {
                "apache": _GOOD
                | {"intervals": {**_GOOD["intervals"], 0.95: {"low": 0.0, "high": 0.01}}}
            },
            _SIM,
            "intervals are not at the levels it records",
        ),
        ({"apache": _GOOD | {"levels": [0.975, 0.95]}}, _SIM, "read at levels"),
        ({"apache": _GOOD | {"estimate": float("nan")}}, _SIM, "estimate"),
        ({"apache": _GOOD | {"k_from": None}}, _SIM, "K are not from"),
        (
            {"apache": _GOOD | {"k_from": {**_GOOD["k_from"], "org": "openstack"}}},
            _SIM,
            "K are not from its own k_from",
        ),
        (
            {"apache": _GOOD | {"k_from": {"org": "apache", "runs": 16}}},
            _SIM,
            "k_from is at K = 16",
        ),
        (
            {"apache": _GOOD | {"sensitivity": {**_GOOD["sensitivity"], "org": "openstack"}}},
            _SIM,
            "bounds are not from its own sensitivity",
        ),
        (
            {"apache": _GOOD | {"sensitivity": {**_GOOD["sensitivity"], "runs": 40}}},
            _SIM,
            "sensitivity is at K = 40",
        ),
        (
            {"apache": _GOOD | {"sensitivity": {**_GOOD["sensitivity"], "cells": 3}}},
            _SIM,
            "bounds at cells 3, registered 1",
        ),
        (
            {"apache": _GOOD | {"sensitivity": {**_GOOD["sensitivity"], "spread_target": "x"}}},
            _SIM,
            "bounds at spread_target 'x'",
        ),
        ({"apache": _GOOD | {"bootstrap_seed": 8}}, _SIM, "registered 10000"),
        (
            {"apache": _replication_cell(0.01, 0.02, 0.001, resamples=1_040)},
            _SIM,
            "registered 10000",
        ),
        (
            {"apache": _GOOD},
            {"apache": _sim(0.03) | {"spread_targets": {**_CALIBRATION, "pilot_estimate": 0.5}}},
            "calibrated on another pilot",
        ),
        (
            {
                "apache": _GOOD
                | {"k_from": {**_GOOD["k_from"], "spread_targets": {"pilot_estimate": 0.012}}}
            },
            _SIM,
            "calibrated on another pilot than K's",
        ),
        ({"apache": _GOOD | {"levels": None}}, _SIM, "not a list of levels"),
        (
            {"apache": _GOOD},
            {"apache": _sim(0.03) | {"by_target": {"sizing_bound_90": []}}},
            "simulation:",
        ),
        ({"apache": _GOOD}, None, "simulations must map"),
        ([], _SIM, "cells must map"),
        ({"apache": _GOOD | {"intervals": {"by_cells": {}}}}, _SIM, "apache: intervals"),
    ],
)
def test_replication_gate_refuses(frozen, cells: Any, simulations: Any, match: str) -> None:
    frozen("apache")
    with pytest.raises(ValueError, match=match):
        replication_gate(cells, simulations=simulations)


@pytest.mark.parametrize(
    ("admitted", "org", "levels", "cells"),
    [
        (("openstack", "wikimedia"), "openstack", [0.95], 2),
        (("openstack",), "openstack", [0.95], 1),
        (("openstack", "qt", "chromium"), "qt", [0.975, 0.95], 3),
    ],
)
def test_a_gerrit_read_is_registered_by_the_frozen_design(
    monkeypatch: pytest.MonkeyPatch, admitted: tuple, org: str, levels: list, cells: int
) -> None:
    monkeypatch.setattr(decomposition, "ADMITTED_ORGANIZATIONS", admitted)
    read = registered_read(org)
    assert read["levels"] == pytest.approx(levels)
    assert (read["cells"], read["spread_target"]) == (cells, "sizing_bound_90")


def test_a_read_waits_for_its_organizations_to_be_frozen(
    monkeypatch: pytest.MonkeyPatch, frozen
) -> None:
    for org in ("openstack", "apache"):
        with pytest.raises(ValueError, match="frozen"):
            registered_read(org)
    monkeypatch.setattr(decomposition, "ADMITTED_ORGANIZATIONS", ("openstack",))
    frozen("apache")
    with pytest.raises(ValueError, match="frozen admitted"):
        registered_read("wikimedia")
    with pytest.raises(ValueError, match="frozen replication member"):
        registered_read("llvm")
    assert registered_read("apache") == {
        "levels": [REPLICATION_CONFIDENCE],
        "cells": 1,
        "spread_target": "sizing_bound_90",
    }


@pytest.mark.parametrize("bad", [0.0, -0.01, True, float("nan")])
def test_the_gerrit_gate_refuses_a_bound_that_is_not_a_detectable_effect(bad: Any) -> None:
    detectable = {"H1": {"openstack": {0.95: bad}}}
    with pytest.raises(ValueError, match="H1:openstack"):
        _gate(_by_relation(0.75, 0.25, 0.25), admitted=("openstack",), detectable=detectable)


def test_the_replication_family_is_registered_on_the_github_route() -> None:
    assert set(REPLICATION_FAMILY) <= set(GITHUB_ORGS)
    assert not set(REPLICATION_FAMILY) & set(ORGANIZATIONS)


def test_the_gate_refuses_a_confirmatory_wikimedia_cell_with_no_registered_bound() -> None:
    admitted = ("openstack", "wikimedia")
    results = _results(_by_relation(0.75, 0.25, 0.25), orgs=admitted)
    partial = {"H1": {"openstack": DETECTABLE["H1"]["openstack"]}}
    with pytest.raises(ValueError, match="H1:wikimedia"):
        decomposition_gate(
            results,
            admitted=admitted,
            seeds=SEEDS,
            bootstrap_seed=0,
            resamples=MIN_RESAMPLES,
            detectable=partial,
        )


# Verdicts and readings


def test_a_half_split_effect_alone_reads_as_within_half() -> None:
    outcome = _gate(_by_relation(0.75, 0.25, 0.25))
    assert outcome["verdicts"] == {"H1": "pass", "H2": "bounded"}
    assert outcome["reading"] == "within-half"


def test_an_organization_effect_alone_reads_as_the_organization() -> None:
    outcome = _gate(_by_relation(0.5, 0.5, 0.0))
    assert outcome["verdicts"] == {"H1": "bounded", "H2": "pass"}
    assert outcome["reading"] == "organization"


def test_one_organization_without_the_effect_leaves_the_hypothesis_unpassed() -> None:
    def score(trained: str, window: str, seed: int, change: int) -> float:
        if _org(window) == "openstack" and _org(trained) == "openstack":
            return 1.0 if change < 6 else 0.0
        return _by_relation(0.75, 0.25, 0.25)(trained, window, seed, change)

    outcome = _gate(score)
    assert outcome["verdicts"]["H1"] == "inconclusive"
    assert outcome["per_org"]["H1"]["qt"]["verdicts"][0.975] == "supported"
    assert outcome["per_org"]["H1"]["openstack"]["verdicts"][0.975] == "bounded"


def test_a_reversed_effect_is_bounded_not_passed() -> None:
    # H1 predicts own above sibling; a clear reversal rules out a transfer as large as detectable.
    assert _gate(_by_relation(0.25, 0.75, 0.75))["verdicts"]["H1"] == "bounded"


def test_a_cell_without_a_registered_bound_is_refused() -> None:
    partial = {
        "H1": {o: b for o, b in DETECTABLE["H1"].items() if o != "chromium"},
        "H2": DETECTABLE["H2"],
    }
    with pytest.raises(ValueError, match="H1:chromium"):
        _gate(_by_relation(0.75, 0.25, 0.25), detectable=partial)


def test_a_bound_missing_at_the_laxer_holm_level_is_refused() -> None:
    lax_missing = {n: {o: {0.975: BOUND} for o in c} for n, c in DETECTABLE.items()}
    with pytest.raises(ValueError, match="no registered detectable effect"):
        _gate(_by_relation(0.75, 0.25, 0.25), detectable=lax_missing)


def test_a_design_with_no_confirmatory_h2_reads_the_bound_registered_at_its_one_level() -> None:
    admitted = ("openstack", "qt")
    results = _results(_by_relation(0.25, 0.75, 0.75), orgs=admitted)
    only_lax = {"H1": {o: {0.95: BOUND} for o in admitted}}
    outcome = decomposition_gate(
        results,
        admitted=admitted,
        seeds=SEEDS,
        bootstrap_seed=0,
        resamples=1_000,
        detectable=only_lax,
    )
    assert outcome["verdicts"] == {"H1": "bounded"}
    assert outcome["reading"] == "no within-half transfer"


def test_within_sesoi_is_reported_beside_the_verdict() -> None:
    cell = _gate(_by_relation(0.75, 0.25, 0.25))["per_org"]["H2"]["qt"]
    assert set(cell["within_sesoi"]) == {0.975, 0.95}


@pytest.mark.parametrize(
    ("low", "high", "bound", "expected"),
    [
        (0.001, 0.05, 0.01, "supported"),
        (0.002, 0.008, 0.01, "supported"),
        (-0.009, 0.009, 0.01, "bounded"),
        (0.0, 0.0, 0.01, "bounded"),
        (0.0, 0.05, 0.01, "inconclusive"),
        (-0.01, 0.0, 0.01, "bounded"),
        (-0.005, 0.01, 0.01, "inconclusive"),
        (-0.011, 0.005, 0.01, "bounded"),
        (-0.05, -0.02, 0.01, "bounded"),
        (-0.02, 0.026, 0.0273, "bounded"),
        (-0.02, 0.0273, 0.0273, "inconclusive"),
        (-0.02, 0.005, None, "inconclusive"),
        (0.001, 0.05, None, "supported"),
    ],
)
def test_cell_verdicts(low: float, high: float, bound: float | None, expected: str) -> None:
    assert cell_verdict(low, high, bound=bound) == expected


@pytest.mark.parametrize(
    ("h1", "h2", "expected"),
    [
        ("pass", "bounded", "within-half"),
        ("pass", "inconclusive", "within-half, organization unresolved"),
        ("bounded", "pass", "organization"),
        ("inconclusive", "pass", "organization"),
        ("pass", "pass", "nested"),
        ("bounded", "bounded", "neither"),
        ("inconclusive", "bounded", "unresolved"),
        ("inconclusive", "inconclusive", "unresolved"),
    ],
)
def test_every_combination_has_a_pre_committed_reading(h1: str, h2: str, expected: str) -> None:
    assert reading(h1, h2) == expected


# Holm


def test_holm_levels_for_two_hypotheses_and_for_one() -> None:
    assert holm_levels(2) == pytest.approx([0.975, 0.95])
    assert holm_levels(1) == pytest.approx([0.95])


def _cells(*verdicts: tuple[str, str]) -> dict[str, Any]:
    """Cells with a verdict at the strict and the lax Holm level."""
    return {
        f"org{i}": {"verdicts": {0.975: strict, 0.95: lax}}
        for i, (strict, lax) in enumerate(verdicts)
    }


def test_holm_reads_the_second_hypothesis_at_the_laxer_level_once_one_passes() -> None:
    per = {"H1": _cells(("supported", "supported")), "H2": _cells(("inconclusive", "supported"))}
    assert holm_verdicts(per) == {"H1": "pass", "H2": "pass"}


def test_holm_does_not_relax_when_nothing_passes_at_the_strict_level() -> None:
    per = {
        "H1": _cells(("inconclusive", "supported")),
        "H2": _cells(("inconclusive", "supported")),
    }
    assert holm_verdicts(per) == {"H1": "inconclusive", "H2": "inconclusive"}


def test_holm_never_reads_absence_at_the_laxer_level() -> None:
    per = {"H1": _cells(("supported", "supported")), "H2": _cells(("inconclusive", "bounded"))}
    assert holm_verdicts(per)["H2"] == "inconclusive"


def test_holm_refuses_a_hypothesis_with_no_cells() -> None:
    with pytest.raises(ValueError, match="no confirmatory cells"):
        holm_verdicts({"H1": {}, "H2": {}})


def test_the_strict_interval_is_wider_than_the_lax_one() -> None:
    def score(trained: str, window: str, seed: int, change: int) -> float:
        base = _by_relation(0.5, 0.5, 0.5)(trained, window, seed, change)
        return 1.0 if trained == window and change % 3 == 0 else base

    cell = _gate(score)["per_org"]["H1"]["qt"]["intervals"]
    assert cell[0.975]["low"] < cell[0.95]["low"]
    assert cell[0.975]["high"] > cell[0.95]["high"]


def test_a_passing_cell_clear_of_the_sesoi_is_not_flagged() -> None:
    assert _gate(_by_relation(0.75, 0.25, 0.25))["below_sesoi"] == []


def _interval_cells(**by_level: tuple[float, float]) -> dict[str, Any]:
    """One cell with an interval at each Holm level, keyed '975' and '95'."""
    intervals = {0.975: by_level["strict"], 0.95: by_level["lax"]}
    return {
        "org0": {
            "intervals": {c: {"low": lo, "high": hi} for c, (lo, hi) in intervals.items()},
            "verdicts": {c: cell_verdict(lo, hi, bound=BOUND) for c, (lo, hi) in intervals.items()},
        }
    }


def test_below_sesoi_is_read_at_the_level_the_hypothesis_passed_at() -> None:
    per = {
        "H1": _interval_cells(strict=(0.02, 0.09), lax=(0.03, 0.08)),
        "H2": _interval_cells(strict=(-0.001, 0.008), lax=(0.001, 0.007)),
    }
    verdicts, passed_at = holm_steps(per)
    assert verdicts == {"H1": "pass", "H2": "pass"}
    assert passed_at == {"H1": 0.975, "H2": 0.95}
    assert below_sesoi(per, passed_at) == ["H2:org0"]


def test_below_sesoi_ignores_hypotheses_that_did_not_pass() -> None:
    per = {
        "H1": _interval_cells(strict=(-0.02, 0.005), lax=(-0.01, 0.004)),
        "H2": _interval_cells(strict=(0.02, 0.09), lax=(0.03, 0.08)),
    }
    _, passed_at = holm_steps(per)
    assert passed_at["H1"] is None
    assert below_sesoi(per, passed_at) == []


def test_the_fallback_readings_are_the_registered_ones() -> None:
    assert reading("pass", None) == "within-half"
    assert reading("bounded", None) == "no within-half transfer"
    assert reading("inconclusive", None) == "unresolved"


def test_a_broken_exploratory_cell_does_not_withhold_the_verdicts() -> None:
    results = _results(_by_relation(0.75, 0.25, 0.25))
    del results[run_id(EvalRun("adapter:qt-b", "openstack-a", 1))]
    outcome = decomposition_gate(
        results,
        admitted=ORGS,
        seeds=SEEDS,
        bootstrap_seed=0,
        resamples=MIN_RESAMPLES,
        detectable=DETECTABLE,
    )
    assert outcome["verdicts"] == {"H1": "pass", "H2": "bounded"}
    assert "not scored" in outcome["per_org"]["H2"]["openstack"]["error"]


def test_a_broken_confirmatory_cell_is_still_refused() -> None:
    results = _results(_by_relation(0.75, 0.25, 0.25))
    del results[run_id(EvalRun("adapter:chromium-b", "qt-a", 1))]
    with pytest.raises(ValueError, match="not scored"):
        decomposition_gate(
            results,
            admitted=ORGS,
            seeds=SEEDS,
            bootstrap_seed=0,
            resamples=MIN_RESAMPLES,
            detectable=DETECTABLE,
        )


# The bootstrap behind the verdicts


def _seed_shift_on_sibling(trained: str, window: str, seed: int, change: int) -> float:
    """Own and foreign fixed; the sibling adapter's accuracy moves with the seed only."""
    if trained == window or _org(trained) != _org(window):
        return 1.0 if change < 6 else 0.0
    return 1.0 if change < {1: 3, 2: 6, 3: 9}[seed] else 0.0


def test_seed_variance_reaches_the_interval() -> None:
    cell = _gate(_seed_shift_on_sibling)["per_org"]["H1"]["qt"]
    assert cell["estimate"] == pytest.approx(0.0)
    assert cell["intervals"][0.95]["low"] < -0.1 < 0.1 < cell["intervals"][0.95]["high"]


def test_the_two_contrasts_are_read_on_the_same_draws() -> None:
    # d_org = -d_proj on every draw when only the sibling moves, so both can never be positive.
    joint = _gate(_seed_shift_on_sibling)["joint"]["qt"]
    assert joint["shares"]["H1>0,H2>0"] == 0.0
    assert joint["shares"]["H1>0,H2<=0"] > 0.2
    assert joint["summed"]["low"] == pytest.approx(0.0)
    assert joint["summed"]["high"] == pytest.approx(0.0)


def test_joint_readings_exist_only_where_an_organization_carries_both_contrasts() -> None:
    joint = _gate(_by_relation(0.75, 0.5, 0.25))["joint"]
    assert set(joint) == {"openstack", "qt", "chromium"}
    assert sum(joint["qt"]["shares"].values()) == pytest.approx(1.0)
    assert joint["qt"]["summed"]["estimate"] == pytest.approx(0.5)


def test_the_summed_interval_is_read_at_its_registered_level() -> None:
    # Sibling and foreign agree on every example, so the organization contrast is zero on
    # every draw and the sum's interval must be the half-split's interval at the same level.
    def score(trained: str, window: str, seed: int, change: int) -> float:
        if trained == window:
            return 1.0 if change % 3 == 0 else 0.0
        return 1.0 if change < 3 else 0.0

    outcome = _gate(score)
    summed = outcome["joint"]["qt"]["summed"]
    h1 = outcome["per_org"]["H1"]["qt"]["intervals"][0.95]
    assert SUMMED_CONFIDENCE == 0.95
    assert (summed["low"], summed["high"]) == (h1["low"], h1["high"])
    assert summed["high"] > summed["low"]


def test_cells_report_their_clusters_and_seeds() -> None:
    cell = _gate(_by_relation(0.75, 0.25, 0.25))["per_org"]["H2"]["qt"]
    assert cell["clusters_per_half"] == [N_CHANGES, N_CHANGES]
    assert cell["seeds"] == len(SEEDS)


# The C++-restricted supplement


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"id": "qt:I1:src/corelib/qstring.cpp:3:120"}, "src/corelib/qstring.cpp"),
        ({"id": "chromium:I2:base/a:b.cc:1:4"}, "base/a:b.cc"),
        ({"id": "x:I3:ignored:1:1", "path": "real/path.h"}, "real/path.h"),
    ],
)
def test_row_path_reads_the_path_out_of_the_id(row: dict[str, str], expected: str) -> None:
    assert row_path(row) == expected


def test_row_path_refuses_an_id_with_no_path() -> None:
    with pytest.raises(ValueError, match="carries no path"):
        row_path({"id": "qt:I1"})


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("a/b.cpp", True),
        ("a/b.cc", True),
        ("a/b.H", True),
        ("a/b.py", False),
        ("a.cc/Makefile", False),
        ("a/b", False),
    ],
)
def test_is_cpp_by_suffix(path: str, expected: bool) -> None:
    assert is_cpp({"id": "x", "path": path}) is expected


def _with_paths(score: Score) -> dict[str, list[dict[str, Any]]]:
    """Even changes are C++ and carry the organization effect; odd changes are Python and do not."""
    results = _results(score, n_changes=2 * N_CHANGES)
    for rows in results.values():
        for row in rows:
            change = int(row["change_id"].rsplit("I", 1)[1])
            row["path"] = f"src/f{change}.{'cc' if change % 2 == 0 else 'py'}"
    return results


def test_the_cpp_supplement_reads_only_cpp_examples() -> None:
    def score(trained: str, window: str, seed: int, change: int) -> float:
        if change % 2 == 1:
            return 0.5 * (change % 4 == 1)
        return 1.0 if _org(trained) == _org(window) else 0.0

    outcome = cpp_supplement(
        _with_paths(score), admitted=ORGS, seeds=SEEDS, bootstrap_seed=0, resamples=MIN_RESAMPLES
    )
    assert outcome["role"] == "supplementary"
    qt = outcome["cells"]["qt"]
    assert qt["estimate"] == pytest.approx(1.0)
    assert qt["clusters_per_half"] == [N_CHANGES, N_CHANGES]
    assert qt["cpp_share"] == {"qt-a": 0.5, "qt-b": 0.5}


def test_the_gate_reports_the_design_it_read_and_refuses_a_set_without_openstack() -> None:
    admitted = ("openstack", "wikimedia", "qt", "chromium")
    outcome = _gate(_by_relation(0.75, 0.25, 0.25), admitted=admitted)
    assert outcome["design"] == design(admitted)
    assert set(outcome["per_org"]["H1"]) == set(admitted)
    assert {o: c["role"] for o, c in outcome["per_org"]["H2"].items()} == {
        "openstack": "exploratory",
        "wikimedia": "exploratory",
        "qt": "confirmatory",
        "chromium": "confirmatory",
    }
    assert outcome["joint"]["openstack"]["h2"] == {"foreign": "wikimedia", "role": "exploratory"}
    assert outcome["joint"]["qt"]["h2"] == {"foreign": "chromium", "role": "confirmatory"}
    with pytest.raises(ValueError, match="openstack must always be admitted"):
        _gate(_by_relation(0.75, 0.25, 0.25), admitted=("qt", "chromium"))


@pytest.mark.parametrize(
    ("admitted", "cells"),
    [
        (("openstack", "wikimedia", "qt"), set()),
        (("openstack", "chromium"), set()),
        (("openstack", "wikimedia", "qt", "chromium"), {"qt", "chromium"}),
    ],
)
def test_the_cpp_supplement_reads_only_confirmatory_h2_cells(
    admitted: tuple[str, ...], cells: set[str]
) -> None:
    def score(trained: str, window: str, seed: int, change: int) -> float:
        return 1.0 if _org(trained) == _org(window) else 0.0

    results = _results(score, orgs=admitted, n_changes=2 * N_CHANGES)
    for rows in results.values():
        for row in rows:
            row["path"] = "src/f.cc"
    outcome = cpp_supplement(
        results, admitted=admitted, seeds=SEEDS, bootstrap_seed=0, resamples=MIN_RESAMPLES
    )
    assert outcome["design"] == design(admitted)
    assert set(outcome["cells"]) == cells
    assert ("note" in outcome) is (not cells)


def test_a_half_with_too_few_cpp_changes_is_reported_not_raised() -> None:
    results = _results(_by_relation(0.5, 0.5, 0.25))
    for rows in results.values():
        for row in rows:
            row["path"] = "src/f.py"
    outcome = cpp_supplement(
        results, admitted=ORGS, seeds=SEEDS, bootstrap_seed=0, resamples=MIN_RESAMPLES
    )
    assert "error" in outcome["cells"]["qt"]


def test_detectable_effects_are_read_from_the_sensitivity_artifact() -> None:
    path = Path(__file__).resolve().parents[3] / "datasets/results/decomposition-sensitivity.json"
    artifact = json.loads(path.read_text())
    bounds = detectable_effects(artifact)
    for org, cell in artifact["cells"].items():
        for level, entry in cell["by_level"].items():
            assert bounds["H1"][org][float(level)] == entry["minimum_detectable_effect"]


def test_a_confirmatory_h2_cell_without_a_bound_is_refused() -> None:
    with pytest.raises(ValueError, match="H2:"):
        _gate(_by_relation(0.75, 0.25, 0.25), detectable={"H1": DETECTABLE["H1"]})


def test_an_exploratory_cell_never_reads_bounded() -> None:
    admitted = ("openstack", "qt")
    results = _results(_by_relation(0.75, 0.25, 0.25), orgs=admitted)
    outcome = decomposition_gate(
        results,
        admitted=admitted,
        seeds=SEEDS,
        bootstrap_seed=0,
        resamples=1_000,
        detectable=DETECTABLE,
    )
    for cell in outcome["per_org"]["H2"].values():
        assert cell["role"] == "exploratory"
        assert "bounded" not in cell["verdicts"].values()


def _artifact(a: dict[str, int], b: dict[str, int]) -> dict[str, Any]:
    return {"halves": [{"suffixes": a}, {"suffixes": b}]}


def test_matched_suffix_is_the_one_the_smaller_half_holds_most_of() -> None:
    # Pooled, .rst (100 + 1) would win; the smaller half holds .py far more.
    assert matched_suffix(_artifact({".rst": 100, ".py": 20}, {".rst": 1, ".py": 30})) == ".py"
    assert matched_suffix(_artifact({".b": 3, ".a": 3}, {".a": 3, ".b": 3})) == ".a"
    with pytest.raises(ValueError, match="share no file suffix"):
        matched_suffix(_artifact({".py": 1}, {".rst": 1}))


OPENSTACK_PY = _artifact({".py": 10, ".rst": 30}, {".py": 20, ".rst": 5})
WIKIMEDIA_RST = _artifact({".py": 1, ".rst": 30}, {".py": 2, ".rst": 30})


def _typed(score: Score, orgs: tuple[str, ...], py_changes: dict[str, int]) -> dict[str, Any]:
    """Changes below `py_changes[window]` are Python in that window, the rest reStructuredText."""
    results = _results(score, orgs=orgs, n_changes=2 * N_CHANGES)
    for key, rows in results.items():
        window = key.split("|")[1]
        for row in rows:
            change = int(row["change_id"].rsplit("I", 1)[1])
            row["path"] = f"src/f{change}.{'py' if change < py_changes[window] else 'rst'}"
    return results


def _supplement(results: dict[str, Any], admitted: tuple[str, ...], **artifacts: Any) -> dict:
    return file_type_supplement(
        results,
        admitted=admitted,
        split_criteria=artifacts,
        seeds=SEEDS,
        bootstrap_seed=0,
        resamples=MIN_RESAMPLES,
    )


def test_the_file_type_supplement_reads_each_organization_on_its_own_matched_suffix() -> None:
    admitted = ("openstack", "wikimedia")
    # Asymmetric shares: openstack-a holds four more Python changes than openstack-b.
    py = {
        "openstack-a": N_CHANGES + 4,
        "openstack-b": N_CHANGES,
        "wikimedia-a": 5,
        "wikimedia-b": 5,
    }

    def score(trained: str, window: str, seed: int, change: int) -> float:
        rst = change >= py[window]
        # OpenStack's effect lives in its docs, Wikimedia's in its Python.
        if (_org(window) == "openstack") == rst:
            return 1.0 if trained == window else 0.0
        return 0.5 * (change % 4 == 0)

    outcome = _supplement(
        _typed(score, admitted, py), admitted, openstack=OPENSTACK_PY, wikimedia=WIKIMEDIA_RST
    )
    cells = outcome["cells"]
    assert cells["openstack"]["suffix"] == ".py"
    assert cells["wikimedia"]["suffix"] == ".rst"
    assert cells["openstack"]["share"] == {
        "openstack-a": (N_CHANGES + 4) / (2 * N_CHANGES),
        "openstack-b": 0.5,
    }
    assert cells["wikimedia"]["estimate"] == pytest.approx(0.0)
    assert cells["openstack"]["estimate"] == pytest.approx(0.0)


def test_a_half_without_the_matched_suffix_is_an_error_cell_with_no_share() -> None:
    admitted = ("openstack",)
    py = {"openstack-a": N_CHANGES, "openstack-b": 0}
    outcome = _supplement(
        _typed(_by_relation(0.75, 0.25, 0.25), admitted, py), admitted, openstack=OPENSTACK_PY
    )
    cell = outcome["cells"]["openstack"]
    assert "error" in cell and "share" not in cell and cell["suffix"] == ".py"


def test_the_file_type_supplement_refuses_a_missing_artifact_few_resamples_and_seeds() -> None:
    admitted = ("openstack", "wikimedia")
    results = _typed(
        _by_relation(0.75, 0.25, 0.25),
        admitted,
        dict.fromkeys(("openstack-a", "openstack-b", "wikimedia-a", "wikimedia-b"), N_CHANGES),
    )
    with pytest.raises(ValueError, match="no split-criteria artifact"):
        _supplement(results, admitted, openstack=OPENSTACK_PY)
    with pytest.raises(ValueError, match="resamples"):
        file_type_supplement(
            results,
            admitted=("openstack",),
            split_criteria={"openstack": OPENSTACK_PY},
            seeds=SEEDS,
            bootstrap_seed=0,
            resamples=MIN_RESAMPLES - 1,
        )
    with pytest.raises(ValueError):
        file_type_supplement(
            results,
            admitted=("openstack",),
            split_criteria={"openstack": OPENSTACK_PY},
            seeds=SEEDS[:2],
            bootstrap_seed=0,
            resamples=MIN_RESAMPLES,
        )


def test_a_missing_results_cell_is_an_error_cell_in_the_cpp_supplement() -> None:
    admitted = ("openstack", "qt", "chromium")
    results = _with_paths(_by_relation(0.75, 0.25, 0.25))
    del results["adapter:chromium-b|qt-a|s1"]
    outcome = cpp_supplement(
        results, admitted=admitted, seeds=SEEDS, bootstrap_seed=0, resamples=MIN_RESAMPLES
    )
    assert "no results" in outcome["cells"]["qt"]["error"]
    assert "cpp_share" not in outcome["cells"]["qt"]


def test_replication_gate_accepts_any_real_number_type(frozen) -> None:
    """A numpy scalar is a `numbers.Real` and not a float; Fraction stands in for it here."""
    frozen("apache")
    cell = _GOOD | {"p_one_sided": Fraction(1, 1000), "bounds": {0.975: Fraction(3, 100)}}
    out = replication_gate({"apache": cell}, simulations={"apache": _sim(Fraction(3, 100))})
    assert out["cells"]["apache"]["verdict"] == "supported"


def test_once_frozen_the_gate_reads_only_the_frozen_admitted_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(decomposition, "ADMITTED_ORGANIZATIONS", ("openstack", "wikimedia"))
    with pytest.raises(ValueError, match="not the frozen set"):
        _gate(_by_relation(0.75, 0.25, 0.25), admitted=("openstack",))


def test_same_calibration_allows_libm_noise_and_nothing_more() -> None:
    noisy = {k: v * (1 + 1e-12) for k, v in _CALIBRATION.items()}
    assert same_calibration(_CALIBRATION, noisy)
    assert not same_calibration(_CALIBRATION, {**_CALIBRATION, "pilot_estimate": 0.0121})
    assert not same_calibration(_CALIBRATION, {"pilot_estimate": 0.012})
    assert not same_calibration(_CALIBRATION, None)
