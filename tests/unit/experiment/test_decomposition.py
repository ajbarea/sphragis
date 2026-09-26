"""The granularity decomposition: the half-split contrast and the organization beyond it."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from sphragis.experiment.decomposition import (
    DESIGNS,
    MIN_RESAMPLES,
    SUMMED_CONFIDENCE,
    below_sesoi,
    cell_verdict,
    cpp_supplement,
    decomposition_gate,
    detectable_effects,
    halves,
    holm_levels,
    holm_steps,
    holm_verdicts,
    is_cpp,
    organization_clusters,
    project_clusters,
    reading,
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


# A registered bound for every cell either design reads, at both Holm levels.
BOUND = 0.05
DETECTABLE = {
    name: {org: {0.975: BOUND, 0.95: BOUND} for org in ("openstack", "qt", "chromium")}
    for name in ("H1", "H2")
}


def _gate(score: Score, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("detectable", DETECTABLE)
    return decomposition_gate(
        _results(score), seeds=SEEDS, bootstrap_seed=0, resamples=MIN_RESAMPLES, **kwargs
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


def test_the_registered_design_tests_every_registered_cell_and_nothing_else() -> None:
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


def test_the_fallback_design_tests_h1_alone_at_the_whole_family_level() -> None:
    results = _results(_by_relation(0.75, 0.25, 0.25), orgs=("openstack", "qt"))
    outcome = decomposition_gate(
        results,
        design="without_chromium",
        seeds=SEEDS,
        bootstrap_seed=0,
        resamples=1_000,
        detectable=DETECTABLE,
    )
    assert outcome["holm_levels"] == [0.95]
    assert set(outcome["verdicts"]) == {"H1"}
    assert outcome["reading"] == "within-half"
    assert all(c["role"] == "exploratory" for c in outcome["per_org"]["H2"].values())


def test_an_unregistered_design_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown design"):
        _gate(_by_relation(0.75, 0.25, 0.25), design="qt-only")


def test_the_registered_designs_are_what_the_spec_says() -> None:
    assert DESIGNS["registered"]["H1"] == ("openstack", "qt", "chromium")
    assert set(DESIGNS["registered"]["H2"]) == {("qt", "chromium"), ("chromium", "qt")}
    assert DESIGNS["without_chromium"]["H2"] == ()


def test_the_gate_refuses_an_unregistered_seed_count() -> None:
    with pytest.raises(ValueError, match="odd number"):
        decomposition_gate(
            _results(_by_relation(0.75, 0.25, 0.25)),
            seeds=(1, 2),
            bootstrap_seed=0,
            resamples=MIN_RESAMPLES,
            detectable=DETECTABLE,
        )


def test_the_gate_refuses_too_few_resamples() -> None:
    with pytest.raises(ValueError, match="resamples"):
        decomposition_gate(
            _results(_by_relation(0.75, 0.25, 0.25)),
            seeds=SEEDS,
            bootstrap_seed=0,
            resamples=40,
            detectable=DETECTABLE,
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


def test_the_fallback_design_reads_the_bound_registered_at_its_one_level() -> None:
    results = _results(_by_relation(0.25, 0.75, 0.75), orgs=("openstack", "qt"))
    only_lax = {"H1": {o: {0.95: BOUND} for o in ("openstack", "qt")}}
    outcome = decomposition_gate(
        results,
        design="without_chromium",
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
        _with_paths(score), seeds=SEEDS, bootstrap_seed=0, resamples=MIN_RESAMPLES
    )
    assert outcome["role"] == "supplementary"
    qt = outcome["cells"]["qt"]
    assert qt["estimate"] == pytest.approx(1.0)
    assert qt["clusters_per_half"] == [N_CHANGES, N_CHANGES]
    assert qt["cpp_share"] == {"qt-a": 0.5, "qt-b": 0.5}


def test_a_half_with_too_few_cpp_changes_is_reported_not_raised() -> None:
    results = _results(_by_relation(0.5, 0.5, 0.25))
    for rows in results.values():
        for row in rows:
            row["path"] = "src/f.py"
    outcome = cpp_supplement(results, seeds=SEEDS, bootstrap_seed=0, resamples=MIN_RESAMPLES)
    assert "error" in outcome["cells"]["qt"]


def test_detectable_effects_are_read_from_the_sensitivity_artifact() -> None:
    path = Path(__file__).resolve().parents[3] / "datasets/results/decomposition-sensitivity.json"
    artifact = json.loads(path.read_text())
    bounds = detectable_effects(artifact)
    for org, cell in artifact["cells"].items():
        for level, entry in cell["by_level"].items():
            assert bounds["H1"][org][float(level)] == entry["minimum_detectable_effect"]


def test_the_registered_design_refuses_h2_cells_without_bounds() -> None:
    with pytest.raises(ValueError, match="H2:"):
        _gate(_by_relation(0.75, 0.25, 0.25), detectable={"H1": DETECTABLE["H1"]})


def test_an_exploratory_cell_never_reads_bounded() -> None:
    results = _results(_by_relation(0.75, 0.25, 0.25), orgs=("openstack", "qt"))
    outcome = decomposition_gate(
        results,
        design="without_chromium",
        seeds=SEEDS,
        bootstrap_seed=0,
        resamples=1_000,
        detectable=DETECTABLE,
    )
    for cell in outcome["per_org"]["H2"].values():
        assert cell["role"] == "exploratory"
        assert "bounded" not in cell["verdicts"].values()
