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
from collections import defaultdict
from collections.abc import Sequence
from itertools import combinations
from math import comb
from statistics import fmean, median, stdev
from typing import Any


def tpr_at_fpr(present: Sequence[float], absent: Sequence[float], fpr: float) -> dict[str, float]:
    """The share of members caught at a false-positive rate the defender would tolerate.

    An AUC averages over the whole ROC curve, including the high-false-positive region no
    attacker would operate in, and an attack can score well there while catching no member at a
    usable threshold (Carlini et al., "Membership Inference Attacks From First Principles",
    IEEE S&P 2022, arXiv:2112.03570). A privacy claim is about the confident identifications,
    so every detector here reports this beside its AUC.

    The threshold is the largest one whose empirical false-positive rate is at most `fpr`: with
    `m = floor(fpr * n)` non-members allowed above it, that is the (m+1)-th largest non-member
    score, and at m = 0 it is the largest, which asks how many members outrank every non-member
    drawn. The achieved rate is returned rather than the requested one, since `n` draws cannot
    resolve a rate below 1/n.
    """
    if not present or not absent:
        raise ValueError("both classes need scores")
    ranked = sorted(absent, reverse=True)
    allowed = min(int(fpr * len(ranked)), len(ranked) - 1)
    threshold = ranked[allowed]
    return {
        "fpr_requested": fpr,
        "fpr_achieved": sum(1 for x in absent if x > threshold) / len(absent),
        "tpr": sum(1 for x in present if x > threshold) / len(present),
        "threshold": threshold,
        "resolution": 1.0 / len(absent),
    }


def _operating_points(present: Sequence[float], absent: Sequence[float]) -> dict[str, float]:
    """AUC, and the low-false-positive points the AUC can hide."""
    wins = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in present for b in absent)
    out = {"auc": wins / (len(present) * len(absent))}
    for fpr in (0.01, 0.05):
        point = tpr_at_fpr(present, absent, fpr)
        out[f"tpr_at_{int(fpr * 100)}pct_fpr"] = point["tpr"]
        out[f"fpr_achieved_at_{int(fpr * 100)}pct"] = point["fpr_achieved"]
    return out


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
    return {
        **_operating_points(with_target, without),
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
    groups: Sequence[str] | None = None,
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
    # With `groups`, the split is over groups rather than clients: a target's own project must not
    # sit in the reference, or the detector reads the project and reports it as the organization.
    by_group: dict[str, list[int]] = defaultdict(list)
    if groups is not None:
        for client in mine:
            by_group[groups[client]].append(client)
        if len(by_group) < 2:
            raise ValueError(
                f"an organization needs two groups to be told from one of them, got "
                f"{sorted(by_group)}"
            )
    outside = [i for i in everyone if i not in set(mine)]
    if size > len(outside):
        raise ValueError(f"a round without the target needs {size} others, got {len(outside)}")
    scores, separations, reference_sizes = [], [], []
    for split in range(splits):
        # Two streams: which clients are the reference must not decide which rounds are drawn,
        # or the spread across splits would carry the round draws with it.
        splitter = random.Random(f"{seed}/split/{split}")
        rng = random.Random(f"{seed}/rounds/{split}")
        if groups is None:
            shuffled = list(mine)
            splitter.shuffle(shuffled)
            reference, participants = shuffled[:half], shuffled[half:]
        else:
            names = sorted(by_group)
            splitter.shuffle(names)
            cut = max(1, len(names) // 2)
            reference = [c for name in names[:cut] for c in by_group[name]]
            participants = [c for name in names[cut:] for c in by_group[name]]
            if not reference or not participants:
                raise ValueError("a group split left one side empty")
        if at_least > len(participants):
            raise ValueError(f"{at_least} participants needed, {len(participants)} available")
        reference_sizes.append(len(reference))
        present, absent = [], []
        for _ in range(draws):
            contributed = rng.sample(participants, at_least)
            rest = rng.sample(outside, size - at_least)
            present.append(direction_score(products, [*contributed, *rest], reference))
            absent.append(direction_score(products, rng.sample(outside, size), reference))
        scores.append(_operating_points(present, absent))
        separations.append((fmean(present), fmean(absent)))
    aucs = [s["auc"] for s in scores]
    return {
        **{key: fmean(s[key] for s in scores) for key in scores[0]},
        # A standard deviation rather than the range, which only widens with more splits.
        "auc_sd_over_splits": stdev(aucs) if len(aucs) > 1 else 0.0,
        "splits": float(splits),
        "rounds": float(draws),
        "round_size": float(size),
        "target_clients_per_round": float(at_least),
        # The reference as it was, averaged over splits: over projects it is whole projects, not
        # half the clients, and reporting `half` there gave AOSP 6 where its splits held 3 to 6.
        "reference_clients": fmean(reference_sizes),
        "split_over_groups": groups is not None,
        # The size of the separation, not only its ordering: an AUC near 1 over scores that
        # differ in the fourth decimal is a consistent ordering of almost nothing.
        "mean_present": fmean(p for p, _ in separations),
        "mean_absent": fmean(a for _, a in separations),
    }


def project_permutation(
    products: Sequence[Sequence[float]],
    *,
    projects_of: Sequence[str],
    owner: dict[str, str],
    label: str,
    size: int,
    draws: int,
    splits: int,
    seeds: Sequence[int],
    cap: int = 5000,
) -> dict[str, Any]:
    """Is `label`'s detector reading the organization, or the projects that happen to compose it?

    The exchangeable unit is the project, since a project's clients share it. Under the null the
    organization's k projects are any k of the P projects present, so the null is the C(P, k)
    distinct groupings -- not every arrangement of every organization's labels, which with three
    or more organizations scores each grouping many times over and misreports the count. Each
    grouping is scored by the same detector call, the true grouping included, so it counts itself
    and p is at least 1/C(P, k). Beyond `cap` groupings a random sample is drawn, the truth always
    among them.

    The detector's AUC moves with its round draws, so the test is repeated over `seeds` and every
    seed's p is reported: at 150 draws one organization's p ran from 0.167 to 0.333 across four
    seeds, and a single seed had been reported as though it were the value.
    """
    projects = sorted(set(projects_of))
    mine = tuple(sorted(p for p in projects if owner[p] == label))
    if len(mine) < 2:
        raise ValueError(f"{label} has {len(mine)} project; a grouping needs two to split over")
    total = comb(len(projects), len(mine))
    if total <= cap:
        groupings = list(combinations(projects, len(mine)))
    else:
        sampler = random.Random(f"groupings/{label}/{min(seeds)}")
        chosen = {mine}
        while len(chosen) < cap:
            chosen.add(tuple(sorted(sampler.sample(projects, len(mine)))))
        groupings = sorted(chosen)
    for grouping in groupings:
        outside = sum(1 for p in projects_of if p not in grouping)
        if outside < size:
            raise ValueError(
                f"a grouping of {label} leaves {outside} clients outside, fewer than a round of "
                f"{size}: choose a round size every grouping fits"
            )

    def score(grouping: tuple[str, ...], seed: int) -> float:
        members = [i for i, p in enumerate(projects_of) if p in grouping]
        return organization_membership_auc(
            products,
            members=members,
            everyone=range(len(projects_of)),
            size=size,
            draws=draws,
            seed=seed,
            at_least=1,
            splits=splits,
            groups=list(projects_of),
        )["auc"]

    per_seed = []
    for seed in seeds:
        observed = score(mine, seed)
        scores = {grouping: score(grouping, seed) for grouping in groupings}
        # The truth is one of the groupings, scored by the same call, so it must reproduce the
        # observed statistic exactly. When it did not -- the observed value came from a run at
        # other settings -- the truth scored below itself and the test reported an impossible
        # p = 0.000; this makes that mismatch stop the test instead of shifting its answer.
        if scores[mine] != observed:
            raise RuntimeError(
                f"{label}: the true grouping scores {scores[mine]} in the null and {observed} as "
                "observed, so the two are not the same statistic"
            )
        # `>=`, so the truth counts itself; a strict comparison lets p reach zero.
        hits = sum(value >= observed for value in scores.values())
        per_seed.append(
            {"seed": seed, "observed_auc": observed, "hits": hits, "p": hits / len(groupings)}
        )
    values = sorted(entry["p"] for entry in per_seed)
    return {
        "projects": len(projects),
        "label_projects": len(mine),
        "groupings": len(groupings),
        "exhaustive": total <= cap,
        "floor": 1 / len(groupings),
        "round_size": size,
        "draws": draws,
        "splits": splits,
        "per_seed": per_seed,
        "p_median": median(values),
        "p_min": values[0],
        "p_max": values[-1],
    }
