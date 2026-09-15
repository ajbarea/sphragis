"""Walk the condition grid and read the RQ1 gate off the outcomes.

The orchestration the confirmatory run needs, with no model in it. `runner` scores one
condition; this decides which conditions exist, which pairs are contrasted, and how the
per-seed intervals collapse into one verdict.

Keeping it here rather than in a job script is what lets February's run be exercised in
November against fakes, and what stops the pass rule from being re-derived by hand at the
point where the answer is already visible.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from statistics import median
from typing import Any

from sphragis.experiment.grid import EvalRun, eval_runs, run_id, training_runs
from sphragis.experiment.runner import Generator, Trainer, evaluate, to_clusters
from sphragis.measure.stats import cluster_bootstrap, gate_verdict


def walk(
    *,
    orgs: Sequence[str],
    seeds: Sequence[int],
    windows: Mapping[str, Sequence[Mapping[str, Any]]],
    trainer: Trainer,
    generator_for: Callable[[str | None], Generator],
) -> dict[str, list[dict[str, Any]]]:
    """Every evaluation in the grid, keyed by `run_id`.

    Each adapter is trained once and evaluated on every organization's window, because
    training it per evaluation would spend the budget twice and, at a different seed,
    silently compare two different adapters.
    """
    missing = [org for org in orgs if org not in windows]
    if missing:
        raise ValueError(f"no evaluation window for {', '.join(missing)}")
    _require_registered_seeds(seeds)

    adapters = {
        (run.org, run.seed): trainer.train(run.org, run.seed) for run in training_runs(orgs, seeds)
    }
    # Distinct handles, checked rather than assumed. A trainer that names an adapter by
    # its output directory without the seed returns one handle for three runs, and every
    # "per-seed" interval is then the same adapter evaluated three times: a replication
    # that did not happen, reported as one that did.
    if len(set(adapters.values())) != len(adapters):
        raise ValueError(
            "trainer returned the same adapter handle for different (org, seed) runs; "
            "the seed replication would silently evaluate one adapter repeatedly"
        )
    # One generator alive at a time. Each holds a full copy of the 7B, about 15 GB in bf16,
    # and the grid needs seven (base plus six adapters): cached for the whole walk they need
    # roughly 105 GB against a GH200's 102, so the job would die partway through the grid
    # after the queue wait. Runs are grouped by the generator they need, each generator
    # serves all of its runs, and it is released before the next is built.
    #
    # Grouped on the run, not on the adapter handle, so equal handles can never alias two
    # conditions onto one generator. `None` is the base model.
    by_generator: dict[tuple[str, int] | None, list[EvalRun]] = {}
    for run in eval_runs(orgs, seeds):
        key = None if run.seed is None else (_trained_on(run), run.seed)
        by_generator.setdefault(key, []).append(run)
    results: dict[str, list[dict[str, Any]]] = {}
    for key, runs in by_generator.items():
        generator = generator_for(None if key is None else adapters[key])
        for run in runs:
            results[run_id(run)] = evaluate(generator, windows[run.eval_org])
        del generator
    return results


def _require_registered_seeds(seeds: Sequence[int]) -> None:
    """Distinct seeds, and an odd count so the median is a seed rather than a midpoint.

    With an even count two seeds are equidistant from the median, and which one binds was
    decided by floating-point rounding: the higher estimate bound in 49.4% of 5,000 random
    draws. Duplicate seeds collapse in `run_id`, silently turning three seeds into two.
    """
    if len(set(seeds)) != len(seeds):
        raise ValueError(f"seeds must be distinct, got {list(seeds)}")
    if len(seeds) % 2 == 0:
        raise ValueError(
            f"an odd number of seeds is required so one seed is the median, got {len(seeds)}"
        )


def _trained_on(run: EvalRun) -> str:
    """The organization an adapter condition was trained on.

    `grid.eval_runs` gives the base condition a `None` seed and every adapter condition a
    real one, so a seed is what distinguishes them; reading the condition string would
    duplicate that rule in a second place.
    """
    condition, _, org = run.condition.partition(":")
    if condition != "adapter" or not org:
        raise ValueError(f"not an adapter condition: {run.condition!r}")
    return org


def matched_vs_mismatched(
    results: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    eval_org: str,
    other_org: str,
    seed: int,
    metric: str = "exact_match",
    bootstrap_seed: int,
) -> dict[str, float]:
    """The RQ1 contrast on one window at one seed.

    Treatment is the adapter trained on the evaluated organization, control the adapter
    trained on the other. The base model is not the control: it would measure that
    adaptation helps, which is the positive control, not the fingerprint claim.
    """
    matched = results[run_id(EvalRun(f"adapter:{eval_org}", eval_org, seed))]
    mismatched = results[run_id(EvalRun(f"adapter:{other_org}", eval_org, seed))]
    clusters = to_clusters(matched, mismatched, metric=metric)
    return cluster_bootstrap(clusters, seed=bootstrap_seed)


def gate(
    results: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    orgs: Sequence[str],
    seeds: Sequence[int],
    metric: str = "exact_match",
    bootstrap_seed: int,
) -> dict[str, Any]:
    """The pre-registered RQ1 verdict, with the per-seed intervals it was read from.

    PRE-REGISTRATION NOTE. The design fixes three seeds and says "median reported with
    range". That says what to report, not which interval binds the gate, so the rule is
    made explicit here: the verdict reads the interval of the seed whose point estimate is
    the median, and every seed's interval is returned beside it. The alternative -- require
    all seeds to exclude zero -- is strictly harder and is a different study; whichever
    stands has to be in the Stage 1 report, not decided at analysis time.
    """
    if len(orgs) != 2:
        raise ValueError("the RQ1 gate is defined over exactly two organizations")
    _require_registered_seeds(seeds)
    first, second = orgs
    per_org: dict[str, dict[str, float]] = {}
    per_seed: dict[str, dict[int, dict[str, float]]] = {}
    for eval_org, other_org in ((first, second), (second, first)):
        intervals = {
            seed: matched_vs_mismatched(
                results,
                eval_org=eval_org,
                other_org=other_org,
                seed=seed,
                metric=metric,
                bootstrap_seed=bootstrap_seed,
            )
            for seed in seeds
        }
        per_seed[eval_org] = intervals
        middle = median(interval["estimate"] for interval in intervals.values())
        # `median` averages the two middle values on an even count, which may match no
        # seed, so the binding seed is the one closest to it. With three seeds, as
        # registered, this is exactly the middle seed.
        binding = min(intervals, key=lambda s: abs(intervals[s]["estimate"] - middle))
        per_org[eval_org] = {**intervals[binding], "binding_seed": float(binding)}
    return {
        "verdict": gate_verdict(per_org),
        "binding": per_org,
        "per_seed": per_seed,
    }
