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
