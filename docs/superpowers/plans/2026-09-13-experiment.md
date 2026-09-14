# Experiment Implementation Plan (Plan C)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Run the 3 by 2 condition grid over the frozen windows and hand its outcomes to the plan B instruments, plus the pilot power analysis the Stage 1 report needs.

**Architecture:** The model is a seam, exactly as the Gerrit transport is in plan A. `runner.py` orchestrates against `Trainer` and `Generator` protocols; `model.py` is the only module in the repo permitted to import torch, transformers or peft. Everything else is testable offline against fakes.

**Tech Stack:** Python 3.12-3.14, standard library for orchestration. The model stack arrives as a pinned `experiment` extra and is never imported by the orchestration.

**Spec:** `docs/superpowers/specs/2026-09-13-gerrit-review-corpus-harness-design.md` sections 4, 5.

## Global Constraints

- `make lint` and `make test` pass before every commit.
- **Only `sphragis/experiment/model.py` may import a GPU stack.** A test enforces it, the same way `measure` is enforced.
- Every random draw takes an explicit seed.
- The grid is data, not control flow: `grid.runs()` returns the work to do, and the runner does not decide what to run.
- No stage reads a Gerrit server. The experiment consumes frozen windows only.

## Why the seam matters more here than anywhere else

The pilot power analysis has to run before 2026-11-20 and the confirmatory grid cannot run until after in-principle acceptance in February. Those are four months apart. If the orchestration can only be exercised by loading a 7B model on TIGRIS, nothing in between is testable and the February run is the first time the code is exercised end to end. The seam is what makes that gap safe.


> **Status: tasks 1-4 executed.** `model.py` and `slurm.py` remain, and wait on a
> confirmed TIGRIS allocation because both are only exercisable there.

---

## File Structure

| File | Responsibility |
|---|---|
| `sphragis/experiment/grid.py` | the condition grid: what runs, with which seed, on which window |
| `sphragis/experiment/runner.py` | orchestration against the protocols; emits `measure.stats.Cluster` |
| `sphragis/experiment/power.py` | pilot power analysis, minimum detectable difference |
| `sphragis/experiment/model.py` | the only GPU-importing module; implements the protocols |
| `sphragis/experiment/slurm.py` | sbatch script generation for TIGRIS |

---

### Task 1: The condition grid

The grid encodes one non-obvious fact: the base model has no training, so it is evaluated **once per window**, not once per seed. Getting this wrong inflates the base arm's sample and quietly biases every comparison against it.

**Files:** Create `sphragis/experiment/__init__.py`, `sphragis/experiment/grid.py`; Test `tests/unit/experiment/test_grid.py`

**Interfaces:**
- `TrainingRun` — frozen dataclass: `org: str`, `seed: int`
- `EvalRun` — frozen dataclass: `condition: str`, `eval_org: str`, `seed: int | None`
- `training_runs(orgs: Sequence[str], seeds: Sequence[int]) -> list[TrainingRun]`
- `eval_runs(orgs: Sequence[str], seeds: Sequence[int]) -> list[EvalRun]`
- `run_id(run: EvalRun) -> str`

- [x] **Step 1: Write the failing test**

```python
"""The condition grid: what runs, how often, and what the base arm does not repeat."""

from __future__ import annotations

from sphragis.experiment.grid import EvalRun, eval_runs, run_id, training_runs

ORGS = ("openstack", "qt")
SEEDS = (1, 2, 3)


def test_one_training_run_per_organization_and_seed() -> None:
    runs = training_runs(ORGS, SEEDS)
    assert len(runs) == 6
    assert {(r.org, r.seed) for r in runs} == {(o, s) for o in ORGS for s in SEEDS}


def test_the_base_arm_is_evaluated_once_per_window_not_once_per_seed() -> None:
    base = [r for r in eval_runs(ORGS, SEEDS) if r.condition == "base"]
    assert len(base) == 2
    assert {r.eval_org for r in base} == set(ORGS)
    assert all(r.seed is None for r in base)


def test_each_adapter_is_evaluated_on_every_window_at_every_seed() -> None:
    adapters = [r for r in eval_runs(ORGS, SEEDS) if r.condition != "base"]
    assert len(adapters) == 12
    assert {(r.condition, r.eval_org, r.seed) for r in adapters} == {
        (f"adapter:{o}", e, s) for o in ORGS for e in ORGS for s in SEEDS
    }


def test_the_whole_grid_is_fourteen_evaluations() -> None:
    assert len(eval_runs(ORGS, SEEDS)) == 14


def test_run_ids_are_unique_and_stable() -> None:
    runs = eval_runs(ORGS, SEEDS)
    ids = [run_id(r) for r in runs]
    assert len(set(ids)) == len(ids)
    assert run_id(EvalRun("base", "qt", None)) == "base|qt"
    assert run_id(EvalRun("adapter:qt", "openstack", 2)) == "adapter:qt|openstack|s2"
```

- [x] **Step 2: Run test to verify it fails** — `uv run pytest tests/unit/experiment/test_grid.py -q`

- [x] **Step 3: Write minimal implementation**

```python
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
```

- [x] **Step 4: Run test to verify it passes** — expected 5 passed
- [x] **Step 5: Lint and commit**

---

### Task 2: Pilot power analysis

This is the task the Stage 1 deadline actually needs. Section 5 of the report has to state the minimum detectable difference at 80% power for the planned test window, derived from pilot variance.

**Files:** Create `sphragis/experiment/power.py`; Test `tests/unit/experiment/test_power.py`

**Interfaces:**
- `simulate_power(clusters: Sequence[Cluster], *, effect: float, seed: int, trials: int = 200, resamples: int = 200) -> float`
- `minimum_detectable_effect(clusters: Sequence[Cluster], *, seed: int, target_power: float = 0.8, trials: int = 200, resamples: int = 200, tolerance: float = 0.01) -> float`

Method: take the pilot clusters as the variance model, add a candidate effect to the treatment arm, bootstrap the interval, and count the fraction of trials whose interval excludes zero. That fraction is the power. Bisect on the effect until it reaches the target.

- [x] **Step 1: Write the failing test**

```python
"""Pilot power analysis: the minimum effect the planned window could detect."""

from __future__ import annotations

from sphragis.experiment.power import minimum_detectable_effect, simulate_power
from sphragis.measure.stats import Cluster


def _pilot(n: int, seed: int = 0) -> list[Cluster]:
    import random

    rng = random.Random(seed)
    return [
        Cluster(
            f"I{i}",
            tuple(float(rng.random() < 0.3) for _ in range(rng.randint(1, 4))),
            tuple(float(rng.random() < 0.3) for _ in range(rng.randint(1, 4))),
        )
        for i in range(n)
    ]


def test_zero_effect_gives_power_near_the_false_positive_rate() -> None:
    power = simulate_power(_pilot(40), effect=0.0, seed=1, trials=100, resamples=100)
    assert power < 0.25


def test_a_large_effect_is_detected_almost_always() -> None:
    power = simulate_power(_pilot(40), effect=0.6, seed=1, trials=100, resamples=100)
    assert power > 0.9


def test_power_rises_with_the_effect() -> None:
    small = simulate_power(_pilot(40), effect=0.1, seed=1, trials=100, resamples=100)
    large = simulate_power(_pilot(40), effect=0.4, seed=1, trials=100, resamples=100)
    assert large > small


def test_power_rises_with_the_sample() -> None:
    few = simulate_power(_pilot(15), effect=0.2, seed=1, trials=100, resamples=100)
    many = simulate_power(_pilot(120), effect=0.2, seed=1, trials=100, resamples=100)
    assert many > few


def test_simulate_power_is_deterministic_for_a_seed() -> None:
    a = simulate_power(_pilot(30), effect=0.2, seed=5, trials=60, resamples=60)
    b = simulate_power(_pilot(30), effect=0.2, seed=5, trials=60, resamples=60)
    assert a == b


def test_minimum_detectable_effect_is_in_range_and_achieves_the_target() -> None:
    pilot = _pilot(60)
    mde = minimum_detectable_effect(pilot, seed=2, trials=60, resamples=60, tolerance=0.02)
    assert 0.0 < mde < 1.0
    achieved = simulate_power(pilot, effect=mde, seed=2, trials=60, resamples=60)
    assert achieved >= 0.7


def test_a_bigger_pilot_detects_a_smaller_effect() -> None:
    small = minimum_detectable_effect(_pilot(20), seed=3, trials=60, resamples=60, tolerance=0.02)
    large = minimum_detectable_effect(_pilot(200), seed=3, trials=60, resamples=60, tolerance=0.02)
    assert large < small
```

- [x] **Step 2: Run test to verify it fails**

- [x] **Step 3: Write minimal implementation**

```python
"""Pilot power analysis: the smallest organization-specific gain the window could detect.

The pilot supplies the variance model. A candidate effect is added to the treatment arm,
the interval is bootstrapped, and power is the fraction of trials whose interval excludes
zero. Bisection on the effect gives the minimum detectable difference.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from sphragis.measure.stats import Cluster, cluster_bootstrap, excludes_zero


def _shift(cluster: Cluster, effect: float, rng: random.Random) -> Cluster:
    lifted = tuple(
        1.0 if value < 1.0 and rng.random() < effect else value for value in cluster.treatment
    )
    return Cluster(cluster.change_id, lifted, cluster.control)


def simulate_power(
    clusters: Sequence[Cluster],
    *,
    effect: float,
    seed: int,
    trials: int = 200,
    resamples: int = 200,
) -> float:
    """Fraction of simulated studies whose interval excludes zero at this effect."""
    if not clusters:
        raise ValueError("simulate_power needs at least one pilot cluster")
    rng = random.Random(seed)
    n = len(clusters)
    detected = 0
    for trial in range(trials):
        sample = [_shift(clusters[rng.randrange(n)], effect, rng) for _ in range(n)]
        interval = cluster_bootstrap(sample, seed=seed + trial, resamples=resamples)
        detected += excludes_zero(interval)
    return detected / trials


def minimum_detectable_effect(
    clusters: Sequence[Cluster],
    *,
    seed: int,
    target_power: float = 0.8,
    trials: int = 200,
    resamples: int = 200,
    tolerance: float = 0.01,
) -> float:
    """Bisect on the effect until simulated power reaches ``target_power``."""
    low, high = 0.0, 1.0
    while high - low > tolerance:
        mid = (low + high) / 2.0
        power = simulate_power(clusters, effect=mid, seed=seed, trials=trials, resamples=resamples)
        if power >= target_power:
            high = mid
        else:
            low = mid
    return high
```

- [x] **Step 4: Run test to verify it passes** — expected 7 passed
- [x] **Step 5: Lint and commit**

---

### Task 3: The runner and its protocols

**Files:** Create `sphragis/experiment/runner.py`; Test `tests/unit/experiment/test_runner.py`

**Interfaces:**
- `Generator` — `Protocol` with `generate(self, prompt: str) -> str`
- `Trainer` — `Protocol` with `train(self, org: str, seed: int) -> str` returning an adapter id
- `evaluate(generator: Generator, examples: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]`
- `to_clusters(treatment: Sequence[Mapping[str, Any]], control: Sequence[Mapping[str, Any]], *, metric: str = "exact_match") -> list[Cluster]`
- `build_prompt(example: Mapping[str, Any]) -> str`

`to_clusters` is where a silent bias could enter: it must pair on example id and raise if the arms disagree, rather than zipping two lists that might be ordered differently.

- [x] **Step 1: Write the failing test**

```python
"""Orchestration against the model seam, and the pairing that feeds the statistics."""

from __future__ import annotations

from typing import Any

import pytest

from sphragis.experiment.runner import build_prompt, evaluate, to_clusters


class FakeGenerator:
    """Returns a canned completion per prompt, so the runner is testable with no model."""

    def __init__(self, answers: dict[str, str], default: str = "") -> None:
        self.answers = answers
        self.default = default
        self.seen: list[str] = []

    def generate(self, prompt: str) -> str:
        self.seen.append(prompt)
        for key, value in self.answers.items():
            if key in prompt:
                return value
        return self.default


def _ex(example_id: str, change_id: str, before: str, after: str) -> dict[str, Any]:
    return {
        "id": example_id,
        "change_id": change_id,
        "before": before,
        "after": after,
        "comments": ["fix it"],
    }


EXAMPLES = [_ex("a", "I1", "x=1", "x = 1"), _ex("b", "I1", "y=2", "y = 2")]


def test_build_prompt_carries_the_code_and_every_comment() -> None:
    prompt = build_prompt(_ex("a", "I1", "x=1", "x = 1"))
    assert "x=1" in prompt and "fix it" in prompt


def test_evaluate_scores_every_example_with_the_whole_ladder() -> None:
    gen = FakeGenerator({"x=1": "x = 1"}, default="nope")
    results = evaluate(gen, EXAMPLES)
    assert [r["id"] for r in results] == ["a", "b"]
    assert results[0]["exact_match"] == 1.0
    assert results[1]["exact_match"] == 0.0
    assert "normalized_exact_match" in results[0] and "edit_similarity" in results[0]
    assert len(gen.seen) == 2


def test_to_clusters_groups_by_change_and_pairs_by_example_id() -> None:
    treatment = [
        {"id": "a", "change_id": "I1", "exact_match": 1.0},
        {"id": "b", "change_id": "I1", "exact_match": 0.0},
    ]
    control = [
        {"id": "b", "change_id": "I1", "exact_match": 1.0},
        {"id": "a", "change_id": "I1", "exact_match": 0.0},
    ]
    clusters = to_clusters(treatment, control)
    assert len(clusters) == 1
    # Pairing is by id, so reversing the control order must not reorder the outcomes.
    assert clusters[0].treatment == (1.0, 0.0)
    assert clusters[0].control == (0.0, 1.0)


def test_to_clusters_rejects_arms_evaluated_on_different_examples() -> None:
    treatment = [{"id": "a", "change_id": "I1", "exact_match": 1.0}]
    control = [{"id": "z", "change_id": "I1", "exact_match": 1.0}]
    with pytest.raises(ValueError, match="same examples"):
        to_clusters(treatment, control)


def test_to_clusters_separates_changes() -> None:
    treatment = [
        {"id": "a", "change_id": "I1", "exact_match": 1.0},
        {"id": "c", "change_id": "I2", "exact_match": 0.0},
    ]
    clusters = to_clusters(treatment, treatment)
    assert {c.change_id for c in clusters} == {"I1", "I2"}
```

- [x] **Step 2: Run test to verify it fails**

- [x] **Step 3: Write minimal implementation**

```python
"""Orchestration: run a condition over a window and shape the outcomes for statistics."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from sphragis.measure.score import score
from sphragis.measure.stats import Cluster

_PROMPT = (
    "Revise the code below to address every review comment.\n"
    "Reply with the revised code only.\n\n"
    "Review comments:\n{comments}\n\n"
    "Code:\n{before}\n"
)


class Generator(Protocol):
    """Anything that turns a prompt into a completion."""

    def generate(self, prompt: str) -> str: ...


class Trainer(Protocol):
    """Anything that trains one adapter and names it."""

    def train(self, org: str, seed: int) -> str: ...


def build_prompt(example: Mapping[str, Any]) -> str:
    """The refinement prompt: the reviewed hunk plus the comments anchored in it."""
    comments = "\n".join(f"- {c}" for c in example.get("comments", []))
    return _PROMPT.format(comments=comments, before=example["before"])


def evaluate(generator: Generator, examples: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Score one condition over one window with the whole metric ladder."""
    results: list[dict[str, Any]] = []
    for example in examples:
        prediction = generator.generate(build_prompt(example))
        results.append(
            {
                "id": example["id"],
                "change_id": example["change_id"],
                "prediction": prediction,
                **score(prediction, str(example["after"])),
            }
        )
    return results


def to_clusters(
    treatment: Sequence[Mapping[str, Any]],
    control: Sequence[Mapping[str, Any]],
    *,
    metric: str = "exact_match",
) -> list[Cluster]:
    """Group paired outcomes by change, pairing on example id rather than position."""
    by_id_control = {r["id"]: r for r in control}
    if {r["id"] for r in treatment} != set(by_id_control):
        raise ValueError("both arms must be evaluated on the same examples")
    grouped: dict[str, tuple[list[float], list[float]]] = defaultdict(lambda: ([], []))
    for result in treatment:
        arms = grouped[str(result["change_id"])]
        arms[0].append(float(result[metric]))
        arms[1].append(float(by_id_control[result["id"]][metric]))
    return [Cluster(change_id, tuple(t), tuple(c)) for change_id, (t, c) in sorted(grouped.items())]
```

- [x] **Step 4: Run test to verify it passes** — expected 5 passed
- [x] **Step 5: Lint and commit**

---

### Task 4: Purity enforcement for the experiment

**Files:** Test `tests/unit/experiment/test_experiment_purity.py`

Mirror `tests/unit/measure/test_measurement_purity.py`: parse the AST of `grid.py`, `power.py` and `runner.py` and assert none imports torch, transformers, peft or datasets, then confirm it in a fresh interpreter. `model.py` is exempt by name and is the only exemption.

- [x] **Step 1-4: Write, fail, implement, pass**
- [x] **Step 5: Run `make lint && make test`, then commit**

---

## After this plan

`model.py` and `slurm.py` land once TIGRIS is confirmed live, because both are only exercisable there. Everything above is complete and tested before then, and the February confirmatory run is not the first time the orchestration executes.
