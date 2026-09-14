"""The condition grid: the work the experiment has to do, as data."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class TrainingRun:
    """One adapter: an organization's train window at one seed."""

    org: str
    seed: int


@dataclass(frozen=True)
class EvalRun:
    """One condition evaluated on one organization's window. Base carries no seed."""

    condition: str
    eval_org: str
    seed: int | None


def training_runs(orgs: Sequence[str], seeds: Sequence[int]) -> list[TrainingRun]:
    """One adapter per organization per seed."""
    return [TrainingRun(org, seed) for org in orgs for seed in seeds]


def eval_runs(orgs: Sequence[str], seeds: Sequence[int]) -> list[EvalRun]:
    """Every evaluation in the grid.

    The base model is untrained, so it is evaluated once per window rather than once per
    seed: repeating it would inflate its sample without adding information.
    """
    runs = [EvalRun("base", org, None) for org in orgs]
    runs += [
        EvalRun(f"adapter:{trained}", evaluated, seed)
        for trained in orgs
        for evaluated in orgs
        for seed in seeds
    ]
    return runs


def run_id(run: EvalRun) -> str:
    """Stable identifier for one evaluation."""
    suffix = "" if run.seed is None else f"|s{run.seed}"
    return f"{run.condition}|{run.eval_org}{suffix}"
