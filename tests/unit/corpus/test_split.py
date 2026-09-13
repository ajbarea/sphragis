"""Time windows grouped by change, and the seal on the confirmatory window."""

from __future__ import annotations

from typing import Any

from sphragis.corpus.split import (
    assign_windows,
    is_test_window_unlocked,
    seal,
    straddling_changes,
)

BOUNDS = {
    "pilot": ("2024-10-01", "2024-11-01"),
    "train": ("2024-11-01", "2025-09-01"),
    "dev": ("2025-09-01", "2025-11-01"),
    "test": ("2025-11-01", "2026-09-01"),
}


def _ex(example_id: str, change_id: str, created: str) -> dict[str, Any]:
    return {"id": example_id, "change_id": change_id, "created": created}


def test_assign_windows_places_each_example_by_date() -> None:
    windows = assign_windows(
        [_ex("a", "I1", "2024-10-05"), _ex("b", "I2", "2025-01-05"), _ex("c", "I3", "2025-12-05")],
        BOUNDS,
    )
    assert [e["id"] for e in windows["pilot"]] == ["a"]
    assert [e["id"] for e in windows["train"]] == ["b"]
    assert [e["id"] for e in windows["test"]] == ["c"]
    assert windows["dev"] == []


def test_a_change_spanning_a_boundary_goes_whole_into_its_earliest_window() -> None:
    windows = assign_windows([_ex("a", "I1", "2024-10-28"), _ex("b", "I1", "2024-11-03")], BOUNDS)
    assert sorted(e["id"] for e in windows["pilot"]) == ["a", "b"]
    assert windows["train"] == []
    assert straddling_changes(windows) == []


def test_examples_outside_every_window_are_discarded() -> None:
    windows = assign_windows([_ex("old", "I0", "2024-01-01")], BOUNDS)
    assert all(w == [] for w in windows.values())


def test_seal_records_a_hash_and_a_timestamp_and_starts_locked() -> None:
    record = seal({"query": "status:merged", "after": "2025-11-01"})
    assert len(record["hash"]) == 64 and record["sealed_at"]
    assert record["accepted_at"] is None
    assert is_test_window_unlocked(record) is False


def test_seal_is_stable_for_the_same_definition_and_changes_with_it() -> None:
    a = seal({"query": "q", "after": "2025-11-01"})
    b = seal({"after": "2025-11-01", "query": "q"})
    c = seal({"query": "q", "after": "2025-12-01"})
    assert a["hash"] == b["hash"] != c["hash"]


def test_the_test_window_unlocks_only_once_an_acceptance_date_is_recorded() -> None:
    record = {**seal({"query": "q"}), "accepted_at": "2027-02-04"}
    assert is_test_window_unlocked(record) is True
