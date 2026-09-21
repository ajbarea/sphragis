"""The subspace split's held-out attribution and its null."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "subspace_split.py"
_spec = importlib.util.spec_from_file_location("subspace_split", _SCRIPT)
assert _spec and _spec.loader
split = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(split)


def _clients(rng):
    projects = [p for p in ["a0", "a1", "a2", "q0", "q1", "q2", "q3"] for _ in range(3)]
    labels = ["aosp" if p.startswith("a") else "qt" for p in projects]
    return rng.normal(size=(len(projects), 40)), labels, projects


def test_the_rank_one_shared_half_is_degenerate_and_not_scored() -> None:
    vectors, _, projects = _clients(np.random.default_rng(0))
    assert split.held_out_rows(vectors, projects, rank=1, residual=False) is None


def test_the_held_out_basis_leaves_the_whole_project_out() -> None:
    """A client's row must not change when its own project's updates change."""
    rng = np.random.default_rng(1)
    vectors, _, projects = _clients(rng)
    before = split.held_out_rows(vectors, projects, rank=3, residual=True)
    moved = vectors.copy()
    moved[1] += 5 * rng.normal(size=40)  # a sibling of client 0 in project a0
    after = split.held_out_rows(moved, projects, rank=3, residual=True)
    # Client 0's basis excludes a0 entirely, so its similarity to clients outside a0 is unchanged.
    outside = [j for j, p in enumerate(projects) if p != "a0"]
    assert np.allclose(before[0][outside], after[0][outside])


def test_the_null_is_every_choice_of_the_first_organizations_projects() -> None:
    _, labels, projects = _clients(np.random.default_rng(2))
    null = split.groupings(labels, projects)
    assert len(null) == math.comb(7, 3)
    assert labels in null, "the truth is one of the relabelings"


def test_balanced_accuracy_does_not_reward_answering_the_larger_class() -> None:
    labels = ["a"] * 2 + ["b"] * 8
    projects = [f"p{i}" for i in range(10)]
    rows = np.zeros((10, 10))
    rows[:, 2:] = 1.0  # everyone is closest to the larger class
    accuracy, balanced = split.attribute(rows, labels, projects)
    assert accuracy == pytest.approx(0.8)
    assert balanced == pytest.approx(0.5)


def _cell(accuracy: float, null: list[float]) -> dict:
    return {"accuracy": accuracy, "null_accuracies": null}


def test_the_family_p_prices_the_choice_of_rank_not_one_cell() -> None:
    """Each relabeling picks its own best rank, as the reported cell did.

    Cell A alone reads p = 0.25. Pricing the search doubles it, because a second rank gives the
    relabelings a second chance to beat 0.9.
    """
    cells = [_cell(0.9, [0.2, 0.95, 0.1, 0.1]), _cell(0.8, [0.95, 0.2, 0.1, 0.1])]
    family = split.max_over_ranks(cells, null_size=4)
    assert family == {"accuracy": 0.9, "p": pytest.approx(0.5)}


def test_the_family_p_is_never_below_the_best_cells_own() -> None:
    cells = [_cell(0.9, [0.2, 0.95, 0.1, 0.1]), _cell(0.8, [0.95, 0.2, 0.1, 0.1])]
    own = float(np.mean([a >= 0.9 for a in cells[0]["null_accuracies"]]))
    assert split.max_over_ranks(cells, null_size=4)["p"] >= own


def test_a_degenerate_cell_is_not_part_of_the_family() -> None:
    """A rank that was refused as degenerate has no accuracy to win with, and no null to lose to."""
    cells = [
        _cell(0.9, [0.2, 0.95, 0.1, 0.1]),
        _cell(0.8, [0.95, 0.2, 0.1, 0.1]),
        {"degenerate": True},
    ]
    assert split.max_over_ranks(cells, null_size=4) == {"accuracy": 0.9, "p": pytest.approx(0.5)}


def test_a_family_of_only_degenerate_cells_is_not_reported() -> None:
    assert split.max_over_ranks([{"degenerate": True}], null_size=4) is None


def test_the_truth_counts_itself_so_the_family_p_keeps_its_floor() -> None:
    """The true grouping is one of the relabelings, so it ties the reported cell exactly.

    A strict comparison would drop that tie and report p = 0, a value the null cannot reach.
    """
    cells = [_cell(0.9, [0.9, 0.2, 0.1, 0.1]), _cell(0.8, [0.8, 0.2, 0.1, 0.1])]
    assert split.max_over_ranks(cells, null_size=4)["p"] == pytest.approx(0.25)


def test_a_cell_missing_relabelings_is_refused_rather_than_truncating_the_family() -> None:
    """A short cell would drop relabelings from the maximum and read as more significant."""
    cells = [_cell(0.9, [0.2, 0.95, 0.1, 0.1]), _cell(0.8, [0.95, 0.2])]
    with pytest.raises(ValueError, match="all 4 relabelings"):
        split.max_over_ranks(cells, null_size=4)


def test_a_null_size_the_cells_do_not_carry_is_refused() -> None:
    """The size is the caller's claim about the null, and it has to match the rows."""
    cells = [_cell(0.9, [0.2, 0.95, 0.1, 0.1])]
    with pytest.raises(ValueError, match="all 5 relabelings"):
        split.max_over_ranks(cells, null_size=5)


def test_a_family_with_no_null_is_refused_rather_than_scored_as_nan() -> None:
    with pytest.raises(ValueError, match="needs a null"):
        split.max_over_ranks([_cell(0.9, [])], null_size=0)
