"""Orchestration: run a condition over a window and shape the outcomes for statistics."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
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
                "path": example.get("path"),
                "prediction": prediction,
                **score(prediction, str(example["after"])),
            }
        )
    return results


def require_unique_ids(rows: Sequence[Mapping[str, Any]], *, label: str) -> None:
    """Refuse rows in which any example id appears twice.

    One definition, called both before evaluation and inside `to_clusters`. Only the second
    existed at first, so a repeated id in an evaluation set surfaced after every generation had
    run: job 148093 spent four GPU-hours and wrote nothing. Checked up front it costs seconds on
    a login node, in the dry run the job script offers for exactly that purpose.
    """
    counts = Counter(r["id"] for r in rows)
    repeated = sorted((i for i, n in counts.items() if n > 1), key=str)
    if repeated:
        raise ValueError(f"{label} repeats example ids {repeated[:5]}")


def to_clusters(
    treatment: Sequence[Mapping[str, Any]],
    control: Sequence[Mapping[str, Any]],
    *,
    metric: str = "exact_match",
) -> list[Cluster]:
    """Group paired outcomes by change, pairing on example id rather than position.

    Every guard here closes an input that previously produced a confident wrong interval
    with no error:

    - a duplicated id inside an arm passed a set comparison, and the id-keyed lookup kept
      only the last row, so a 0.333 difference was reported as 0.667;
    - a `None` change id became the string "None" for every example, collapsing the window
      into one cluster whose interval had zero width and always excluded zero;
    - a NaN outcome has no ordering, so the sorted bootstrap draws are meaningless and
      read as a pass.
    """
    for label, arm in (("treatment", treatment), ("control", control)):
        require_unique_ids(arm, label=f"{label} arm")
    by_id_control = {r["id"]: r for r in control}
    if {r["id"] for r in treatment} != set(by_id_control):
        raise ValueError("both arms must be evaluated on the same examples")
    grouped: dict[str, tuple[list[float], list[float]]] = defaultdict(lambda: ([], []))
    for result in treatment:
        change_id = result["change_id"]
        if not isinstance(change_id, str) or not change_id:
            raise ValueError(
                f"example {result['id']!r} has change id {change_id!r}; clustering needs a "
                "non-empty string, or every such example lands in one cluster"
            )
        pair = (float(result[metric]), float(by_id_control[result["id"]][metric]))
        if not all(math.isfinite(value) for value in pair):
            raise ValueError(f"example {result['id']!r} has a non-finite {metric}: {pair}")
        arms = grouped[change_id]
        arms[0].append(pair[0])
        arms[1].append(pair[1])
    return [Cluster(change_id, tuple(t), tuple(c)) for change_id, (t, c) in sorted(grouped.items())]
