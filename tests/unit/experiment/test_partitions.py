"""H1 over repeated partitions: the sizing of K, the common-example guard, the cell."""

from __future__ import annotations

import json
import math
from statistics import NormalDist, stdev

import pytest

from sphragis.experiment import decomposition
from sphragis.experiment.decomposition import SESOI
from sphragis.experiment.grid import EvalRun, run_id
from sphragis.experiment.partitions import (
    BETA,
    K_MAX,
    K_MIN,
    MAX_DROPPED_SHARE,
    XI,
    chi2_quantile,
    common_runs,
    h1_over_partitions,
    pilot_sizing,
    reproducibility,
    runs_needed,
    sd_bound,
    sensitivity_bounds,
)


def test_reproducibility_is_ritzwoller_and_romanos_criterion() -> None:
    cv = 0.5 * (XI / NormalDist().inv_cdf(1 - BETA / 2)) ** 2
    tight = [0.001 * (i % 2) for i in range(12)]
    out = reproducibility(tight)
    assert out["critical_value"] == pytest.approx(cv)
    assert out["variance_of_mean"] == pytest.approx(stdev(tight) ** 2 / len(tight))
    assert out["holds"]
    assert not reproducibility([(-1) ** i * 0.2 for i in range(K_MAX)])["holds"]


@pytest.mark.parametrize(
    ("p", "df", "expected"),
    # Standard table values.
    [
        (0.10, 21, 13.240),
        (0.05, 10, 3.940),
        (0.5, 1, 0.4549),
        (0.975, 3, 9.348),
        (0.9, 40, 51.805),
        # Past the first bracket of 10 x df: the search must widen, not stop at its edge.
        (0.999, 1, 10.828),
        (1 - 1e-6, 3, 30.664),
    ],
)
def test_chi2_quantile_matches_the_table(p: float, df: int, expected: float) -> None:
    assert chi2_quantile(p, df) == pytest.approx(expected, abs=1e-3)


def test_runs_needed_is_the_sizing_formula_at_the_upper_bound() -> None:
    pilot = [0.02, -0.01, 0.005, 0.03, -0.02, 0.0, 0.01, 0.015, -0.005, 0.025, 0.012, -0.012]
    out = runs_needed(pilot)
    upper = stdev(pilot) * math.sqrt((len(pilot) - 1) / chi2_quantile(0.10, len(pilot) - 1))
    z = NormalDist().inv_cdf(1 - BETA / 2)
    assert out["sd_upper"] == pytest.approx(upper)
    assert out["formula"] == pytest.approx(2 * upper**2 * (z / XI) ** 2)
    assert out["runs"] == min(K_MAX, max(K_MIN, math.ceil(out["formula"])))


def test_sd_bounds_bracket_the_estimate_and_widen_with_confidence() -> None:
    pilot = [0.02, -0.01, 0.005, 0.03, -0.02, 0.0, 0.01, 0.015, -0.005, 0.025]
    s = stdev(pilot)
    lower, upper = sd_bound(pilot, 0.90, upper=False), sd_bound(pilot, 0.90, upper=True)
    assert lower < s < upper < sd_bound(pilot, 0.99, upper=True)
    assert upper == pytest.approx(s * math.sqrt(9 / chi2_quantile(0.10, 9)))
    assert lower == pytest.approx(s * math.sqrt(9 / chi2_quantile(0.90, 9)))


def test_runs_needed_clamps_to_the_burn_in_and_the_list() -> None:
    low, high = runs_needed([0.001, 0.0011, 0.0009]), runs_needed([0.2, -0.2, 0.1, -0.1])
    assert (low["runs"], low["clamped"]) == (K_MIN, "min")
    assert (high["runs"], high["clamped"]) == (K_MAX, "max")
    with pytest.raises(ValueError, match="at least two"):
        runs_needed([0.01])


def _results(
    seed: int, wins: dict[str, set[int]], n: int = 24, missing: int | None = None, org: str = "org"
):
    """Placebo results for one run: each half's own and sibling adapter on each half's changes."""
    results = {}
    halves = (f"{org}-a", f"{org}-b")
    wins = {h: wins[w] for h, w in zip(halves, ("org-a", "org-b"), strict=True)}
    for window, first in ((halves[0], 0), (halves[1], n)):
        for trained in halves:
            rows = [
                {
                    "id": f"x{i}",
                    "change_id": f"c{i}",
                    "exact_match": 1.0 if (trained == window and i in wins[window]) else 0.0,
                }
                for i in range(first, first + n)
                if i != missing
            ]
            results[run_id(EvalRun(f"adapter:{trained}", window, seed))] = rows
    return results


def test_a_cell_reads_the_mean_over_runs_on_common_examples() -> None:
    everything = {"org-a": set(range(24)), "org-b": set(range(24, 48))}
    nothing = {"org-a": set(), "org-b": set()}
    runs = [(_results(1, everything), 1), (_results(2, nothing), 2)]
    cell = h1_over_partitions(
        runs,
        org="org",
        runs_fixed=2,
        levels=[0.95],
        bounds={0.95: 0.5},
        bootstrap_seed=0,
        resamples=500,
    )
    assert cell["estimate"] == pytest.approx(0.5)
    assert cell["per_run"] == pytest.approx([1.0, 0.0])
    assert cell["examples"] == 48 and cell["dropped"] == 0
    assert set(cell["verdicts"]) == {0.95}


def test_runs_scored_on_different_examples_beyond_the_cap_are_refused() -> None:
    wins = {"org-a": set(), "org-b": set()}
    assert MAX_DROPPED_SHARE < 1 / 48
    with pytest.raises(ValueError, match="missing from some run"):
        common_runs([(_results(1, wins), 1), (_results(2, wins, missing=3), 2)], org="org")


def test_a_cell_reads_only_the_first_k_runs() -> None:
    same = {"org-a": set(range(0, 24, 2)), "org-b": set(range(24, 48, 2))}
    runs = [(_results(s, same), s) for s in range(1, K_MIN + 4)]
    cell = h1_over_partitions(
        runs,
        org="org",
        runs_fixed=K_MIN,
        levels=[0.95],
        bounds=None,
        bootstrap_seed=0,
        resamples=50,
    )
    assert cell["runs"] == K_MIN and cell["runs_computed"] == K_MIN + 3
    assert len(cell["per_run"]) == K_MIN and len(cell["runs_left_out"]) == 3
    assert set(cell["reproducibility"]) >= {"holds", "variance_of_mean"}


def test_a_cell_refuses_fewer_runs_than_k() -> None:
    same = {"org-a": set(), "org-b": set()}
    with pytest.raises(ValueError, match="fewer than the 5 fixed"):
        h1_over_partitions(
            [(_results(1, same), 1)],
            org="org",
            runs_fixed=5,
            levels=[0.95],
            bounds=None,
            bootstrap_seed=0,
        )


@pytest.mark.parametrize(("p", "df"), [(0.0, 3), (1.0, 3), (0.5, 0)])
def test_chi2_quantile_refuses_a_probability_or_df_outside_its_domain(p: float, df: int) -> None:
    with pytest.raises(ValueError, match="needs 0 < p < 1"):
        chi2_quantile(p, df)


def test_a_run_past_k_never_changes_the_cell() -> None:
    same = {"org-a": set(range(0, 24, 2)), "org-b": set(range(24, 48, 2))}
    runs = [(_results(s, same), s) for s in range(1, K_MIN + 1)]
    alone = h1_over_partitions(
        runs,
        org="org",
        runs_fixed=K_MIN,
        levels=[0.95],
        bounds=None,
        bootstrap_seed=0,
        resamples=50,
    )
    # A run past K missing examples, far beyond the refusal share, must neither refuse the cell
    # nor shrink the examples it reads.
    extra = (_results(K_MIN + 1, same, missing=3), K_MIN + 1)
    extra[0].update({k: rows[:10] for k, rows in extra[0].items()})
    with_extra = h1_over_partitions(
        [*runs, extra],
        org="org",
        runs_fixed=K_MIN,
        levels=[0.95],
        bounds=None,
        bootstrap_seed=0,
        resamples=50,
    )
    assert with_extra["examples"] == alone["examples"] == 48
    assert with_extra["estimate"] == alone["estimate"]
    assert with_extra["intervals"] == alone["intervals"]
    assert len(with_extra["runs_left_out"]) == 1


def test_a_cell_refuses_fewer_than_two_runs() -> None:
    same = {"org-a": set(), "org-b": set()}
    with pytest.raises(ValueError, match="at least two runs"):
        h1_over_partitions(
            [(_results(1, same), 1)],
            org="org",
            runs_fixed=1,
            levels=[0.95],
            bounds=None,
            bootstrap_seed=0,
        )


def test_sensitivity_bounds_read_the_named_spread_target_and_cell_count() -> None:
    def at(one: float, two: float) -> dict:
        return {
            "by_cells": {
                "1": {"minimum_detectable_effect": one},
                "2": {"minimum_detectable_effect": two},
            }
        }

    artifact = {
        "by_target": {
            "pilot_estimate": {"by_level": {"0.975": at(0.024, 0.026), "0.95": at(0.022, 0.024)}}
        }
    }
    assert sensitivity_bounds(artifact, "pilot_estimate", [0.975, 0.95], cells=1) == {
        0.975: 0.024,
        0.95: 0.022,
    }
    assert sensitivity_bounds(artifact, "pilot_estimate", [0.975, 0.95], cells=2) == {
        0.975: 0.026,
        0.95: 0.024,
    }
    with pytest.raises(ValueError, match="no spread target"):
        sensitivity_bounds(artifact, "sizing_bound_90", [0.975], cells=1)
    with pytest.raises(ValueError, match="no bound for 3"):
        sensitivity_bounds(artifact, "pilot_estimate", [0.975], cells=3)


def test_pilot_sizing_reads_the_pilot_and_refuses_a_reading_at_another_k() -> None:
    assert pilot_sizing({"sizing": {"runs": 24}}, "pilot", org="openstack") == 24
    assert pilot_sizing({"k_source": "all runs", "sizing": {"runs": 24}}, "p", org="qt") == 24
    reading = {"k_source": "pilot.json: sizing.runs", "sizing": {"runs": 22}}
    with pytest.raises(ValueError, match="read its K from"):
        pilot_sizing(reading, "k24", org="openstack")
    with pytest.raises(ValueError, match="no sizing.runs"):
        pilot_sizing({"sizing": {}}, "empty", org="openstack")


def test_sensitivity_bounds_name_a_missing_level() -> None:
    artifact = {"by_target": {"t": {"by_level": {"0.95": {"by_cells": {"1": {}}}}}}}
    with pytest.raises(ValueError, match="no bound at level 0.975"):
        sensitivity_bounds(artifact, "t", [0.975], cells=1)


def test_a_cell_carries_the_inputs_of_the_readings_beside_the_pass_rule() -> None:
    """p-value and standard error from the interval's own draws; meaningful clears the SESOI."""
    everything = {"org-a": set(range(24)), "org-b": set(range(24, 48))}
    some = {"org-a": set(range(0, 24, 2)), "org-b": set(range(24, 48, 2))}
    runs = [(_results(1, everything), 1), (_results(2, some), 2)]
    cell = h1_over_partitions(
        runs,
        org="org",
        runs_fixed=2,
        levels=[0.975, 0.95],
        bounds=None,
        bootstrap_seed=0,
        resamples=500,
    )
    assert 0.0 <= cell["p_one_sided"] <= 1.0 and cell["bootstrap_se"] > 0
    for level, interval in cell["intervals"].items():
        assert cell["meaningful"][level] == (interval["low"] > SESOI)
        if interval["low"] > 0:
            assert cell["p_one_sided"] <= (1 - level) / 2


@pytest.mark.parametrize(
    ("wins", "every_run", "bound", "verdict"),
    [
        ({"org-a": set(range(0, 24, 2)), "org-b": set(range(24, 48, 2))}, True, 0.4, "supported"),
        ({"org-a": {0}, "org-b": set()}, False, 0.4, "bounded"),
        ({"org-a": {0}, "org-b": set()}, False, 0.01, "inconclusive"),
    ],
)
def test_a_cell_written_to_json_reads_the_same_through_the_replication_gate(
    monkeypatch: pytest.MonkeyPatch, wins: dict, every_run: bool, bound: float, verdict: str
) -> None:
    monkeypatch.setattr(decomposition, "REPLICATION_MEMBERS", ("apache",))
    nothing = {"org-a": set(), "org-b": set()}
    runs = [
        (_results(s, wins if every_run or s == 1 else nothing, org="apache"), s)
        for s in range(1, 5)
    ]
    level = decomposition.REPLICATION_CONFIDENCE
    cell = h1_over_partitions(
        runs,
        org="apache",
        runs_fixed=4,
        levels=[level],
        bounds={level: bound},
        bootstrap_seed=7,
        resamples=10_000,
    )
    assert cell["verdicts"][level] == verdict
    report = {
        **cell,
        "window": "test",
        "planted_convention": {"passed": True},
        "k_from": {"file": "partition-pilot-apache.json", "org": "apache", "runs": 4},
        "sensitivity": {"org": "apache", "cells": 1, "runs": 4},
    }
    loaded = json.loads(json.dumps(report))
    out = decomposition.replication_gate({"apache": loaded}, bounds={"apache": {level: bound}})
    assert out["cells"]["apache"]["verdict"] == verdict
    assert out["partial_conjunction"]["at_least"] == (verdict == "supported")


@pytest.mark.parametrize(
    ("artifact", "match"),
    [
        ({"window": "test", "sizing": {"runs": 24}}, "test window"),
        ({"org": "llvm", "sizing": {"runs": 24}}, "llvm's pilot"),
    ],
)
def test_k_comes_from_the_organizations_own_development_pilot(artifact: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        pilot_sizing(artifact, "pilot", org="apache")


def test_a_test_read_sizes_k_only_on_a_pilot_that_names_its_organization() -> None:
    legacy = {"k_source": "all runs", "sizing": {"runs": 24}}
    assert pilot_sizing(legacy, "pilot", org="openstack") == 24
    with pytest.raises(ValueError, match="does not name its organization"):
        pilot_sizing(legacy, "pilot", org="wikimedia", require_org=True)
