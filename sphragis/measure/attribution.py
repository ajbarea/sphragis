"""Which source did a client update come from, read from its alignment with other updates?

The attack is the simplest learned one: an attacker holding reference updates from each
candidate source assigns a new update to the source whose references it aligns with most (mean
cosine, leave one out). It is read at three altitudes from labels of the form
`<organization>:<content>/<project>`: which project, which organization, which kind of content.

The relation table is what separates organization from language. If two updates from different
organizations on the same kind of content align more than two from one organization on different
content, what an update reveals is its content, not its organization.
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean


@dataclass(frozen=True)
class Source:
    organization: str
    content: str
    project: str

    @classmethod
    def parse(cls, label: str) -> Source:
        organization, _, rest = label.partition(":")
        content, _, project = rest.partition("/")
        if not (organization and content and project):
            raise ValueError(f"expected <organization>:<content>/<project>, got {label!r}")
        return cls(organization, content, project)

    def at(self, altitude: str) -> str:
        if altitude == "project":
            return f"{self.organization}/{self.project}"
        if altitude == "organization":
            return self.organization
        if altitude == "content":
            return self.content
        raise ValueError(f"no altitude {altitude!r}")


ALTITUDES = ("project", "organization", "content")


def relation(a: Source, b: Source) -> str:
    """How two sources relate, from most to least shared."""
    if a == b:
        return "same project"
    same_org, same_content = a.organization == b.organization, a.content == b.content
    if same_org and same_content:
        return "same organization and content, other project"
    if same_content:
        return "other organization, same content"
    if same_org:
        return "same organization, other content"
    return "other organization and content"


def relation_means(cosine: Sequence[Sequence[float]], sources: Sequence[Source]) -> dict:
    """Mean cosine and pair count for each relation, over distinct pairs."""
    groups: dict[str, list[float]] = defaultdict(list)
    for i in range(len(sources)):
        for j in range(i + 1, len(sources)):
            groups[relation(sources[i], sources[j])].append(cosine[i][j])
    return {name: {"mean": fmean(v), "pairs": len(v)} for name, v in groups.items()}


def leave_one_out(
    cosine: Sequence[Sequence[float]],
    labels: Sequence[str],
    groups: Sequence[str] | None = None,
) -> list[str | None]:
    """Each update assigned to the class whose other members it aligns with most.

    With `groups`, every member of the update's own group is left out too, not just the update:
    leave one project out, so an organization must be recognised from its other projects rather
    than from the update's siblings. None where no candidate remains.
    """
    predictions: list[str | None] = []
    for i in range(len(labels)):
        scores: dict[str, list[float]] = defaultdict(list)
        for j, label in enumerate(labels):
            if j != i and (groups is None or groups[j] != groups[i]):
                scores[label].append(cosine[i][j])
        means = {label: fmean(values) for label, values in scores.items()}
        predictions.append(max(sorted(means), key=means.__getitem__) if means else None)
    return predictions


def accuracy(
    cosine: Sequence[Sequence[float]],
    labels: Sequence[str],
    groups: Sequence[str] | None = None,
) -> float:
    predictions = leave_one_out(cosine, labels, groups)
    return fmean(1.0 if p == t else 0.0 for p, t in zip(predictions, labels, strict=True))


def permutation_p(
    cosine: Sequence[Sequence[float]], labels: Sequence[str], *, draws: int, seed: int
) -> float:
    """How often shuffled labels reach the observed accuracy: (1 + hits) / (1 + draws)."""
    observed = accuracy(cosine, labels)
    rng = random.Random(seed)
    shuffled = list(labels)
    hits = 0
    for _ in range(draws):
        rng.shuffle(shuffled)
        if accuracy(cosine, shuffled) >= observed:
            hits += 1
    return (1 + hits) / (1 + draws)


def group_permutation_p(
    cosine: Sequence[Sequence[float]],
    labels: Sequence[str],
    groups: Sequence[str],
    *,
    draws: int,
    seed: int,
) -> tuple[float, int]:
    """Leave-one-group-out accuracy against labels permuted across groups, not members.

    The group, a project, is the exchangeable unit: its members share it, so shuffling members
    would treat siblings as independent evidence. Every group must carry one label. When the
    distinct relabelings number no more than `draws` they are enumerated and the p-value is
    exact; otherwise `draws` are sampled. Returns the p-value and how many relabelings it used.
    """
    label_of: dict[str, str] = {}
    for label, group in zip(labels, groups, strict=True):
        if label_of.setdefault(group, label) != label:
            raise ValueError(f"group {group!r} carries more than one label")
    names = sorted(label_of)
    base = [label_of[g] for g in names]
    observed = accuracy(cosine, labels, groups)

    def score(assignment: Sequence[str]) -> float:
        relabel = dict(zip(names, assignment, strict=True))
        return accuracy(cosine, [relabel[g] for g in groups], groups)

    count = _arrangements(base)
    if count <= draws:
        hits = sum(
            1 for assignment in _distinct_arrangements(base) if score(assignment) >= observed
        )
        return hits / count, count
    rng = random.Random(seed)
    hits = 0
    for _ in range(draws):
        shuffled = list(base)
        rng.shuffle(shuffled)
        hits += score(shuffled) >= observed
    return (1 + hits) / (1 + draws), draws


def _arrangements(items: Sequence[str]) -> int:
    """How many distinct orderings a multiset has: n! over the product of each count's factorial."""
    total = math.factorial(len(items))
    for n in Counter(items).values():
        total //= math.factorial(n)
    return total


def _distinct_arrangements(items: Sequence[str]):
    """Every distinct ordering of a multiset, each once, without generating the n! duplicates."""
    counts = Counter(items)
    order = sorted(counts)
    current: list[str] = []

    def extend():
        if len(current) == len(items):
            yield tuple(current)
            return
        for item in order:
            if counts[item]:
                counts[item] -= 1
                current.append(item)
                yield from extend()
                current.pop()
                counts[item] += 1

    yield from extend()
