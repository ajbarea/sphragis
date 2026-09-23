"""The data audit's label rules, applied to built examples."""

from __future__ import annotations

from sphragis.corpus.refine import REFINE_REASONS, refine, revision_kinds


def _row(comments: list[str], patch_set: int = 1, change: str = "I1") -> dict:
    return {
        "id": f"qt:{change}:a.cpp:{patch_set}:10",
        "change_id": change,
        "project": "qt/qtbase",
        "patch_set": patch_set,
        "comments": comments,
        "before": "x",
        "after": "y",
    }


def _kinds(kind: str = "REWORK", change: str = "I1", patch_set: int = 2) -> dict:
    return {(change, "qt/qtbase", patch_set): kind}


def test_a_reviewer_comment_survives_untouched() -> None:
    kept, drops = refine([_row(["rename this"])], _kinds())
    assert [r["comments"] for r in kept] == [["rename this"]]
    assert sum(drops.values()) == 0


def test_a_bot_comment_is_removed_and_the_reviewer_beside_it_kept() -> None:
    kept, drops = refine([_row(["Hint: Trailing whitespace", "rename this"])], _kinds())
    assert kept[0]["comments"] == ["rename this"]
    assert drops["automated_comment"] == 1
    assert drops["automated_only"] == 0


def test_an_example_resting_only_on_a_bot_is_dropped() -> None:
    kept, drops = refine([_row(["Hint: Leading tabs", "Unresolved merge conflict"])], _kinds())
    assert kept == []
    assert drops["automated_comment"] == 2
    assert drops["automated_only"] == 1


def test_an_example_resting_only_on_acknowledged_is_dropped() -> None:
    kept, drops = refine([_row(["Acknowledged"])], _kinds())
    assert kept == []
    assert drops["acknowledgement_comment"] == 1
    assert drops["acknowledgement_only"] == 1


def test_a_rebase_successor_drops_the_example() -> None:
    for kind in ("TRIVIAL_REBASE", "TRIVIAL_REBASE_WITH_MESSAGE_UPDATE", "NO_CODE_CHANGE"):
        kept, drops = refine([_row(["rename this"])], _kinds(kind))
        assert kept == []
        assert drops["not_rework_successor"] == 1


def test_an_unrecorded_successor_is_dropped_and_counted_apart() -> None:
    kept, drops = refine([_row(["rename this"])], {})
    assert kept == []
    assert drops["unknown_successor"] == 1
    assert drops["not_rework_successor"] == 0


def test_the_successor_looked_up_is_the_next_patch_set() -> None:
    kinds = {("I1", "qt/qtbase", 3): "TRIVIAL_REBASE", ("I1", "qt/qtbase", 4): "REWORK"}
    assert refine([_row(["x"], patch_set=3)], kinds)[0] != []
    assert refine([_row(["x"], patch_set=2)], kinds)[0] == []


def test_every_reason_is_reported_even_at_zero() -> None:
    _, drops = refine([], {})
    assert set(drops) == set(REFINE_REASONS)


def test_revision_kinds_reads_raw_changes() -> None:
    raw = [
        {
            "change_id": "I1",
            "project": "qt/qtbase",
            "revisions": {"a": {"_number": 1, "kind": "REWORK"}, "b": {"_number": 2}},
        }
    ]
    assert revision_kinds(raw) == {("I1", "qt/qtbase", 1): "REWORK"}
