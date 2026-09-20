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
    clients = partition(_rows([1, 2, 3, 1, 4, 2, 2, 1, 3, 1] * 5), size=8, seed=0, max_per_change=8)
    assert clients
    assert {len(c) for c in clients} == {8}


def test_clients_are_disjoint_and_never_split_a_change() -> None:
    clients = partition(_rows([3, 1, 2, 2, 1, 4, 1, 1, 2, 3] * 6), size=6, seed=1, max_per_change=6)
    ids = [r["id"] for c in clients for r in c]
    assert len(ids) == len(set(ids))
    owner = {}
    for index, client in enumerate(clients):
        for row in client:
            assert owner.setdefault(row["change_id"], index) == index


def test_the_partition_is_a_function_of_the_seed() -> None:
    rows = _rows([1, 2, 3] * 10)
    assert partition(rows, size=6, seed=4) == partition(rows, size=6, seed=4)
    assert partition(rows, size=6, seed=4, limit=2) != partition(rows, size=6, seed=5, limit=2)


def test_limit_caps_the_number_of_clients() -> None:
    assert len(partition(_rows([2] * 40), size=4, seed=0, limit=3, max_per_change=4)) == 3


def test_a_change_contributes_at_most_its_cap() -> None:
    """A large change gives its first examples up to the cap instead of being dropped whole."""
    clients = partition(_rows([9, 2, 2]), size=4, seed=0, max_per_change=2)
    assert Counter(r["change_id"] for c in clients for r in c) == {"c0": 2, "c1": 2}
    assert all(r["id"] in {"c0:0", "c0:1"} for c in clients for r in c if r["change_id"] == "c0")


def test_too_few_examples_for_one_client_gives_none() -> None:
    assert partition(_rows([1, 1]), size=4, seed=0, max_per_change=4) == []


def test_a_client_size_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="positive"):
        partition(_rows([1]), size=0, seed=0)


@pytest.mark.parametrize("seed", range(8))
def test_a_client_that_cannot_complete_never_blocks_the_rest(seed: int) -> None:
    """One 3-example change and nineteen 2-example changes make nine clients of four, whatever
    order the shuffle gives; the first version got stuck behind the odd one at 2 to 9."""
    clients = partition(_rows([3] + [2] * 19), size=4, seed=seed, max_per_change=4)
    assert len(clients) == 9
    assert {len(c) for c in clients} == {4}


def test_every_client_spans_several_changes_by_default() -> None:
    """A quarter-client cap per change: no client is one big change from one reviewer."""
    clients = partition(_rows([64] * 3 + [2] * 200), size=64, seed=0)
    assert clients
    for client in clients:
        assert Counter(r["change_id"] for r in client).most_common(1)[0][1] <= 16


def test_the_clients_kept_are_a_draw_not_the_largest_changes() -> None:
    """Under first-fit decreasing the first clients opened hold the largest changes; a limit
    must not select exactly those."""
    rows = _rows([16] * 12 + [1] * 400)
    everything = partition(rows, size=16, seed=3, max_per_change=16)
    kept = partition(rows, size=16, seed=3, limit=6, max_per_change=16)
    single_change = [c for c in everything if len({r["change_id"] for r in c}) == 1]
    assert len(single_change) == 12 and len(everything) > 30
    assert sum(1 for c in kept if len({r["change_id"] for r in c}) == 1) < 6
