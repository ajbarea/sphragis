"""What an aggregate of client updates reveals, when secure aggregation hides the individuals.

RQ2's second threat model. Under secure aggregation the server never sees one client's update,
only the mean of a round's participants, so the per-client attack does not apply. The question
becomes whether a round's aggregate betrays who was in it.

Everything here is geometry over the Gram matrix of the updates, exact and without the weights:
an aggregate is the mean of its members, so

    <mean(A), u> = (1/|A|) sum_{a in A} <a, u>,

and the same for the inner product of two aggregates. `adapter_geometry` records the pairwise
cosines and the norms, which give the inner products.

Two detectors, both what an honest-but-curious server could run with reference updates of its own:

- `membership_score`: how much an aggregate aligns with a candidate source's direction, which
  separates rounds that contained that source from rounds that did not.
- `paired_difference`: FedAttr's mechanism (arXiv:2605.06596) with the watermark removed. The
  server averages the aggregates of rounds containing a target and subtracts the average of
  rounds without it, and reads the difference's alignment with the target's direction.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from itertools import combinations
from statistics import fmean


def gram(cosine: Sequence[Sequence[float]], norms: Sequence[float]) -> list[list[float]]:
    """Inner products from the cosines and norms `adapter_geometry` records."""
    return [
        [cosine[i][j] * norms[i] * norms[j] for j in range(len(norms))] for i in range(len(norms))
    ]


def _mean_inner(products: Sequence[Sequence[float]], left: Sequence[int], right: Sequence[int]):
    """<mean of the left updates, mean of the right updates>."""
    total = sum(products[i][j] for i in left for j in right)
    return total / (len(left) * len(right))


def direction_score(
    products: Sequence[Sequence[float]], subset: Sequence[int], reference: Sequence[int]
) -> float:
    """Cosine between an aggregate and a reference source's mean update."""
    left = _mean_inner(products, subset, subset) ** 0.5
    right = _mean_inner(products, reference, reference) ** 0.5
    if left <= 0 or right <= 0:
        return 0.0
    return _mean_inner(products, subset, reference) / (left * right)


def rounds(
    members: Sequence[int], *, size: int, draws: int, rng: random.Random
) -> list[tuple[int, ...]]:
    """`draws` distinct rounds of `size` participants, or every one when few enough."""
    if size > len(members):
        raise ValueError(f"a round of {size} needs {size} clients, got {len(members)}")
    every = combinations(sorted(members), size)
    if _choose(len(members), size) <= draws:
        return list(every)
    seen: set[tuple[int, ...]] = set()
    while len(seen) < draws:
        seen.add(tuple(sorted(rng.sample(list(members), size))))
    return sorted(seen)


def _choose(n: int, k: int) -> int:
    total = 1
    for i in range(k):
        total = total * (n - i) // (i + 1)
    return total


def membership_auc(
    products: Sequence[Sequence[float]],
    *,
    target: Sequence[int],
    others: Sequence[int],
    reference: Sequence[int],
    size: int,
    draws: int,
    seed: int,
) -> dict[str, float]:
    """How well a round's alignment with `reference` says whether `target` was in it.

    Rounds are drawn at one size, half containing exactly one target client and half none, so
    the two classes differ in the target's presence and in nothing else. The score is the area
    under the ROC curve of `direction_score`, with ties counted as half, and 0.5 is no signal.

    `reference` is the attacker's own reference updates from the target source; it must not
    contain the client being detected, or the score reads that client against itself.
    """
    rng = random.Random(seed)
    with_target, without = [], []
    for _ in range(draws):
        rest = rng.sample(list(others), size - 1)
        client = rng.choice(list(target))
        with_target.append(direction_score(products, [client, *rest], reference))
        without.append(direction_score(products, rng.sample(list(others), size), reference))
    wins = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in with_target for b in without)
    return {
        "auc": wins / (len(with_target) * len(without)),
        "rounds": float(draws),
        "round_size": float(size),
        "mean_with": fmean(with_target),
        "mean_without": fmean(without),
    }


def paired_subset_difference(
    products: Sequence[Sequence[float]],
    *,
    target: int,
    others: Sequence[int],
    reference: Sequence[int],
    size: int,
    draws: int,
    seed: int,
) -> float:
    """FedAttr's statistic without a watermark: alignment of the include-minus-exclude difference.

    The server averages aggregates of rounds holding the target and subtracts the average of
    rounds without it; the difference is the target's own update scaled by 1/size, plus the
    difference of two averages of other clients, which shrinks as the draws grow. Its cosine
    with the source's reference direction is what identifies the source.
    """
    rng = random.Random(seed)
    include = [[target, *rng.sample(list(others), size - 1)] for _ in range(draws)]
    exclude = [rng.sample(list(others), size) for _ in range(draws)]
    # <mean(include) - mean(exclude), reference> over the norms, all from the Gram matrix.
    flat_in = [i for r in include for i in r]
    flat_out = [i for r in exclude for i in r]
    inner = _mean_inner(products, flat_in, reference) - _mean_inner(products, flat_out, reference)
    left = (
        _mean_inner(products, flat_in, flat_in)
        - 2 * _mean_inner(products, flat_in, flat_out)
        + _mean_inner(products, flat_out, flat_out)
    )
    right = _mean_inner(products, reference, reference)
    if left <= 0 or right <= 0:
        return 0.0
    return inner / (left**0.5 * right**0.5)
