"""H1 over repeated partitions: the sizing of K, the common-example guard, the cell."""

from __future__ import annotations

import math
from statistics import NormalDist, stdev

import pytest

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
    reproducibility,
    runs_needed,
    sd_bound,
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
    [(0.10, 21, 13.240), (0.05, 10, 3.940), (0.5, 1, 0.4549), (0.975, 3, 9.348), (0.9, 40, 51.805)],
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
    assert runs_needed([0.001, 0.0011, 0.0009])["runs"] == K_MIN
    assert runs_needed([0.2, -0.2, 0.1, -0.1])["runs"] == K_MAX
    with pytest.raises(ValueError, match="at least two"):
        runs_needed([0.01])


def _results(seed: int, wins: dict[str, set[int]], n: int = 24, missing: int | None = None):
    """Placebo results for one run: each half's own and sibling adapter on each half's changes."""
    results = {}
    for window, first in (("org-a", 0), ("org-b", n)):
        for trained in ("org-a", "org-b"):
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
