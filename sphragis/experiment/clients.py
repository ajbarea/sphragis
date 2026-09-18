"""Federated clients cut from a source's examples: disjoint, equal-sized, grouped by change.

RQ2's per-client question is whether an update reveals its source. Two updates can differ for
reasons that have nothing to do with source, and the adapter geometry measured how large those
are: initialization moves the cosine between updates from 0.96 to 0.13, and training length from
0.21 to 0.055. So clients are cut to one size, every client of every source trains the same number
of steps from the same initialization, and what is left to differ is the data.

A change never spans two clients. Its examples share a reviewer, a file and often a phrasing, so
splitting one across clients would let two clients share data the attack could match on.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


def partition(
    rows: Sequence[Mapping[str, Any]],
    *,
    size: int,
    seed: int,
    limit: int | None = None,
    max_per_change: int | None = None,
) -> list[list[dict[str, Any]]]:
    """Disjoint clients of exactly `size` examples, near-maximal in number, drawn at random.

    Exact-size packing is bin packing; first-fit decreasing over several open clients is the
    standard near-optimal heuristic, not an exact one. A client left short is dropped whole rather
    than padded or shared.

    Two choices keep packing from becoming a property of the source the attack could read.
    First-fit decreasing opens clients in order of change size, so keeping its first `limit`
    would keep exactly the clients built around a project's largest changes, one change and one
    reviewer each, while a project of small changes gave clients spanning twenty. So a change
    contributes at most `max_per_change` examples (default a quarter of a client, its first ones),
    which makes every client span several changes, and the `limit` kept are a seeded random draw
    from all the full clients. Examples keep their order within a change.
    """
    if size < 1:
        raise ValueError(f"client size must be positive, got {size}")
    cap = max(1, size // 4) if max_per_change is None else max_per_change
    if cap < 1:
        raise ValueError(f"max_per_change must be positive, got {cap}")
    by_change: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_change[str(row["change_id"])].append(dict(row))
    for change in by_change:
        del by_change[change][cap:]
    order = [c for c in sorted(by_change) if len(by_change[c]) <= size]
    rng = random.Random(f"clients/{seed}")
    rng.shuffle(order)
    # Largest first, each into the first open client it fits; the sort is stable, so the seeded
    # shuffle orders changes of equal size.
    order.sort(key=lambda c: len(by_change[c]), reverse=True)
    bins: list[list[str]] = []
    fill: list[int] = []
    for change in order:
        n = len(by_change[change])
        for index, used in enumerate(fill):
            if used + n <= size:
                bins[index].append(change)
                fill[index] += n
                break
        else:
            bins.append([change])
            fill.append(n)
    full = [b for b, used in zip(bins, fill, strict=True) if used == size]
    if limit is not None and len(full) > limit:
        full = [full[i] for i in sorted(rng.sample(range(len(full)), limit))]
    return [[row for c in b for row in by_change[c]] for b in full]
