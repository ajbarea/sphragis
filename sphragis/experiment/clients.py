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

    Changes are shuffled by seed and packed in order; a change that would overfill the client
    being packed is set aside for the next, and the remainder that cannot make a full client is
    dropped rather than padded or shared, as is any single change larger than a client. Examples
    keep their order within a change.
    """
    if size < 1:
        raise ValueError(f"client size must be positive, got {size}")
    by_change: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_change[str(row["change_id"])].append(dict(row))
    changes = sorted(by_change)
    random.Random(f"clients/{seed}").shuffle(changes)
    clients: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    deferred: list[str] = []
    queue = list(changes)
    while queue:
        change = queue.pop(0)
        examples = by_change[change]
        if len(examples) > size:
            continue
        if len(current) + len(examples) <= size:
            current.extend(examples)
        else:
            deferred.append(change)
        if len(current) == size:
            clients.append(current)
            current = []
            queue = deferred + queue
            deferred = []
            if limit is not None and len(clients) == limit:
                break
    return clients
