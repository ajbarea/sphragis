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
    rows: Sequence[Mapping[str, Any]], *, size: int, seed: int, limit: int | None = None
) -> list[list[dict[str, Any]]]:
    """As many disjoint clients of exactly `size` examples as whole changes allow.

    Exact-size packing is bin packing; first-fit decreasing over several open clients is the
    standard near-optimal heuristic. A client left short is dropped whole rather than padded or
    shared, as is any single change larger than a client. The seed orders changes of equal size.
    Examples keep their order within a change.
    """
    if size < 1:
        raise ValueError(f"client size must be positive, got {size}")
    by_change: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_change[str(row["change_id"])].append(dict(row))
    order = [c for c in sorted(by_change) if len(by_change[c]) <= size]
    random.Random(f"clients/{seed}").shuffle(order)
    # First-fit decreasing: largest changes first, each into the first open client it fits.
    # The sort is stable, so the seeded shuffle still orders changes of equal size.
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
    clients = [[row for c in b for row in by_change[c]] for b in full]
    return clients if limit is None else clients[:limit]
