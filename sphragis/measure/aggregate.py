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
    pool = list(others)
    if len(pool) < size:
        raise ValueError(
            f"a round without the target needs {size} others, got {len(pool)}: the caller must "
            "leave enough outside the target, including when it holds one out as a stranger"
        )
    include = [[target, *rng.sample(pool, size - 1)] for _ in range(draws)]
    exclude = [rng.sample(pool, size) for _ in range(draws)]
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


def organization_membership_auc(
    products: Sequence[Sequence[float]],
    *,
    members: Sequence[int],
    everyone: Sequence[int],
    size: int,
    draws: int,
    seed: int,
    at_least: int = 1,
    splits: int = 8,
) -> dict[str, float]:
    """Did any client of this organization take part, from a mixed round's aggregate alone?

    The deployment question, and a harder one than detecting a named client: rounds are drawn
    from every client, so a round without the target still holds clients of other sources, and a
    round with it holds `at_least` of its clients among participants drawn from everyone.

    The organization's clients are split into the attacker's reference and the participants it may
    contribute, so no round is scored against itself AND both classes are scored against the same
    reference. Scoring the two classes against references of different sizes makes the comparison
    read reference size rather than membership: it put this at an AUC of 1.000.

    The split is drawn at random, not in index order, because clients arrive grouped by project
    and an ordered split hands a multi-project organization a reference from other projects than
    its participants, which ran the detector backwards. One split is a draw like any other and
    moved the figure by 0.06 to 0.08, so `splits` of them are drawn and the spread is reported.
    """
    mine = sorted(members)
    if len(mine) < 2:
        raise ValueError(f"a reference and a participant need two clients, got {len(mine)}")
    half = max(1, len(mine) // 2)
    outside = [i for i in everyone if i not in set(mine)]
    if size > len(outside):
        raise ValueError(f"a round without the target needs {size} others, got {len(outside)}")
    scores = []
    for split in range(splits):
        rng = random.Random(f"{seed}/{split}")
        shuffled = list(mine)
        rng.shuffle(shuffled)
        reference, participants = shuffled[:half], shuffled[half:]
        if at_least > len(participants):
            raise ValueError(f"{at_least} participants needed, {len(participants)} available")
        present, absent = [], []
        for _ in range(draws):
            contributed = rng.sample(participants, at_least)
            rest = rng.sample(outside, size - at_least)
            present.append(direction_score(products, [*contributed, *rest], reference))
            absent.append(direction_score(products, rng.sample(outside, size), reference))
        wins = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in present for b in absent)
        scores.append(wins / (len(present) * len(absent)))
    return {
        "auc": fmean(scores),
        "auc_lowest_split": min(scores),
        "auc_highest_split": max(scores),
        "splits": float(splits),
        "rounds": float(draws),
        "round_size": float(size),
        "target_clients_per_round": float(at_least),
        "reference_clients": float(half),
    }
