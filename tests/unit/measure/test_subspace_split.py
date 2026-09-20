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
