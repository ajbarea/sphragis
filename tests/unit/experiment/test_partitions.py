"""H1 over repeated partitions: the stopping rule, the common-example guard, the cell."""

from __future__ import annotations

from statistics import NormalDist

import pytest

from sphragis.experiment.grid import EvalRun, run_id
from sphragis.experiment.partitions import (
    BETA,
    K_INIT,
    K_MAX,
    MAX_DROPPED_SHARE,
    XI,
    common_runs,
    h1_over_partitions,
    stopping_rule,
)


def test_the_stopping_rule_is_algorithm_one() -> None:
    cv = 0.5 * (XI / NormalDist().inv_cdf(1 - BETA / 2)) ** 2
    tight = [0.001 * (i % 2) for i in range(K_INIT)]
    out = stopping_rule(tight)
    assert out["critical_value"] == pytest.approx(cv)
    assert out["variance_of_mean"] == pytest.approx(
        sum((x - sum(tight) / len(tight)) ** 2 for x in tight) / (len(tight) - 1) / len(tight)
    )
    assert out["stop"]


def test_the_rule_never_stops_before_the_burn_in_and_flags_the_cap() -> None:
    assert not stopping_rule([0.01, 0.01])["stop"]
    wide = [(-1) ** i * 0.2 for i in range(K_MAX)]
    out = stopping_rule(wide)
    assert not out["stop"] and out["at_cap"]


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
        runs, org="org", levels=[0.95], bounds={0.95: 0.5}, bootstrap_seed=0, resamples=500
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


def test_the_first_stop_is_the_earliest_prefix_that_satisfies_the_rule() -> None:
    from sphragis.experiment.partitions import first_stop

    wide = [(-1) ** i * 0.05 for i in range(K_INIT)]
    estimates = wide + [0.0] * 60
    k, trace = first_stop(estimates)
    assert k is not None and k > K_INIT
    assert stopping_rule(estimates[:k])["stop"] and not stopping_rule(estimates[: k - 1])["stop"]
    assert len(trace) == k - K_INIT + 1


def test_no_prefix_stops_below_the_cap_leaves_k_undecided() -> None:
    from sphragis.experiment.partitions import first_stop

    k, trace = first_stop([(-1) ** i * 0.2 for i in range(K_INIT + 2)])
    assert k is None and len(trace) == 3


def test_a_cell_reads_only_the_runs_up_to_the_first_stop() -> None:
    same = {"org-a": set(range(0, 24, 2)), "org-b": set(range(24, 48, 2))}
    runs = [(_results(s, same), s) for s in range(1, K_INIT + 4)]
    cell = h1_over_partitions(
        runs, org="org", levels=[0.95], bounds=None, bootstrap_seed=0, resamples=50
    )
    assert cell["stopped_at"] == K_INIT
    assert len(cell["per_run"]) == K_INIT and len(cell["runs_left_out"]) == 3
