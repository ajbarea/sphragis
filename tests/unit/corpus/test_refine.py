"""The data audit's label rules, applied to built examples."""

from __future__ import annotations

from typing import Any

from sphragis.corpus.refine import (
    ADDRESS_RESIDUE,
    KEPT_UNCHECKED,
    REFINE_REASONS,
    index_changes,
    refine,
    rules_version,
    successor_kind,
)

CREATED = "2024-10-05 10:00:00.000000000"


def _change(number: int, kind: str | None = "REWORK", *, branch: str = "master") -> dict:
    second: dict[str, Any] = {"_number": 2}
    if kind is not None:
        second["kind"] = kind
    return {
        "_number": number,
        "change_id": "I1",
        "project": "qt/qtbase",
        "branch": branch,
        "created": CREATED,
        "revisions": {"p1": {"_number": 1, "kind": "REWORK"}, "p2": second},
    }


def _row(comments: list[str], **extra: Any) -> dict:
    return {
        "id": "qt:I1:a.cpp:1:10",
        "change_id": "I1",
        "project": "qt/qtbase",
        "created": CREATED,
        "patch_set": 1,
        "comments": comments,
        **extra,
    }


def test_a_reviewer_comment_survives_untouched() -> None:
    kept, counts = refine([_row(["rename this"])], index_changes([_change(10)]))
    assert [r["comments"] for r in kept] == [["rename this"]]
    assert sum(counts.values()) == 0


def test_a_bot_comment_is_removed_and_the_reviewer_beside_it_kept() -> None:
    kept, counts = refine(
        [_row(["Hint: Trailing whitespace", "rename this"])], index_changes([_change(10)])
    )
    assert kept[0]["comments"] == ["rename this"]
    assert counts["automated_comment"] == 1
    assert counts["automated_only"] == 0


def test_an_example_resting_only_on_a_bot_is_dropped() -> None:
    kept, counts = refine(
        [_row(["Hint: Leading tabs", "Unresolved merge conflict"])], index_changes([_change(10)])
    )
    assert kept == []
    assert counts["automated_comment"] == 2
    assert counts["automated_only"] == 1


def test_an_example_resting_only_on_acknowledged_is_dropped() -> None:
    kept, counts = refine([_row(["Acknowledged"])], index_changes([_change(10)]))
    assert kept == []
    assert counts["acknowledgement_only"] == 1


def test_a_rebase_successor_of_the_examples_own_change_drops_it() -> None:
    for kind in ("TRIVIAL_REBASE", "TRIVIAL_REBASE_WITH_MESSAGE_UPDATE", "NO_CODE_CHANGE"):
        kept, counts = refine([_row(["rename this"])], index_changes([_change(10, kind)]))
        assert kept == []
        assert counts["not_rework_successor"] == 1


def test_a_cherry_pick_on_another_branch_does_not_lend_its_kind() -> None:
    # The same Change-Id on two branches: the example's own change reworked, the stable copy
    # only rebased. Keyed on the Change-Id, the copy's kind used to overwrite the example's.
    original = _change(10, "REWORK")
    stable_copy = {**_change(11, "NO_CODE_CHANGE", branch="stable"), "created": "2024-11-01"}
    index = index_changes([original, stable_copy])
    kept, counts = refine([_row(["rename this"])], index)
    assert len(kept) == 1
    assert counts["not_rework_successor"] == 0
    assert successor_kind(_row([]), index) == "REWORK"


def test_a_change_number_resolves_the_change_directly() -> None:
    index = index_changes([_change(10, "REWORK"), _change(11, "TRIVIAL_REBASE")])
    assert successor_kind(_row([], change_number=11), index) == "TRIVIAL_REBASE"
    assert successor_kind(_row([], change_number=10), index) == "REWORK"


def test_changes_that_cannot_be_told_apart_are_kept_and_counted() -> None:
    # Same Change-Id, project and creation time, different numbers, no number on the row.
    index = index_changes([_change(10, "REWORK"), _change(11, "TRIVIAL_REBASE")])
    kept, counts = refine([_row(["rename this"])], index)
    assert len(kept) == 1
    assert counts[KEPT_UNCHECKED] == 1


def test_an_unrecorded_kind_is_kept_and_counted_as_the_build_keeps_it() -> None:
    kept, counts = refine([_row(["rename this"])], index_changes([_change(10, None)]))
    assert len(kept) == 1
    assert counts[KEPT_UNCHECKED] == 1
    assert counts["not_rework_successor"] == 0


def test_every_reason_is_reported_even_at_zero() -> None:
    _, counts = refine([], index_changes([]))
    assert set(counts) == {*REFINE_REASONS, KEPT_UNCHECKED, ADDRESS_RESIDUE}


def test_the_rules_version_moves_with_every_input() -> None:
    sources = {"refine.py": "a", "build.py": "b", "examples.py": "c", "automated/__init__.py": "d"}
    base = rules_version(sources, "reg")
    for name in sources:
        assert rules_version({**sources, name: sources[name] + " "}, "reg") != base
    assert rules_version(sources, "reg2") != base
    assert rules_version(dict(reversed(list(sources.items()))), "reg") == base


def test_a_change_number_seen_twice_with_different_content_is_ambiguous() -> None:
    fetched_early = _change(10, "REWORK")
    fetched_late = {**_change(10, "TRIVIAL_REBASE"), "updated": "later"}
    index = index_changes([fetched_early, fetched_late])
    assert successor_kind(_row([], change_number=10), index) is None


def test_the_same_change_read_twice_still_resolves() -> None:
    index = index_changes([_change(10, "REWORK"), _change(10, "REWORK")])
    assert successor_kind(_row([], change_number=10), index) == "REWORK"


def test_missing_fields_are_unresolved_rather_than_fatal() -> None:
    no_number = {k: v for k, v in _change(10).items() if k != "_number"}
    index = index_changes([no_number])
    row = {k: v for k, v in _row(["rename this"]).items() if k != "project"}
    kept, counts = refine([row], index)
    assert len(kept) == 1 and counts[KEPT_UNCHECKED] == 1
    assert successor_kind({**_row([]), "patch_set": None}, index) is None


def test_an_empty_recorded_kind_is_unrecorded() -> None:
    kept, counts = refine([_row(["rename this"])], index_changes([_change(10, "")]))
    assert len(kept) == 1 and counts[KEPT_UNCHECKED] == 1


def test_an_address_domain_left_behind_a_pseudonym_is_removed() -> None:
    kept, counts = refine(
        [_row(["ask 0123456789ab@example.com, and see @mock.patch"])], index_changes([_change(10)])
    )
    assert kept[0]["comments"] == ["ask 0123456789ab, and see @mock.patch"]
    assert counts[ADDRESS_RESIDUE] == 1
