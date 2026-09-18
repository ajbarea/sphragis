"""Clients for RQ2: disjoint, equal-sized, and never splitting a change."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from sphragis.experiment.clients import partition


def _rows(sizes: list[int]) -> list[dict[str, Any]]:
    return [
        {"id": f"c{c}:{e}", "change_id": f"c{c}"} for c, n in enumerate(sizes) for e in range(n)
    ]


def test_every_client_has_exactly_the_size_asked_for() -> None:
    clients = partition(_rows([1, 2, 3, 1, 4, 2, 2, 1, 3, 1] * 5), size=8, seed=0)
    assert clients
    assert {len(c) for c in clients} == {8}


def test_clients_are_disjoint_and_never_split_a_change() -> None:
    clients = partition(_rows([3, 1, 2, 2, 1, 4, 1, 1, 2, 3] * 6), size=6, seed=1)
    ids = [r["id"] for c in clients for r in c]
    assert len(ids) == len(set(ids))
    owner = {}
    for index, client in enumerate(clients):
        for row in client:
            assert owner.setdefault(row["change_id"], index) == index


def test_the_partition_is_a_function_of_the_seed() -> None:
    rows = _rows([1, 2, 3] * 10)
    assert partition(rows, size=6, seed=4) == partition(rows, size=6, seed=4)
    assert partition(rows, size=6, seed=4) != partition(rows, size=6, seed=5)


def test_limit_caps_the_number_of_clients() -> None:
    assert len(partition(_rows([2] * 40), size=4, seed=0, limit=3)) == 3


def test_a_change_larger_than_a_client_is_left_out() -> None:
    clients = partition(_rows([9, 2, 2]), size=4, seed=0)
    assert Counter(r["change_id"] for c in clients for r in c) == {"c1": 2, "c2": 2}


def test_too_few_examples_for_one_client_gives_none() -> None:
    assert partition(_rows([1, 1]), size=4, seed=0) == []


def test_a_client_size_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="positive"):
        partition(_rows([1]), size=0, seed=0)


@pytest.mark.parametrize("seed", range(8))
def test_a_client_that_cannot_complete_never_blocks_the_rest(seed: int) -> None:
    """One 3-example change and nineteen 2-example changes make nine clients of four, whatever
    order the shuffle gives; the first version got stuck behind the odd one at 2 to 9."""
    clients = partition(_rows([3] + [2] * 19), size=4, seed=seed)
    assert len(clients) == 9
    assert {len(c) for c in clients} == {4}
