"""Three-stage dedup: exact, near-duplicate, repeated boilerplate."""

from __future__ import annotations

from typing import Any

from sphragis.corpus.dedup import dedup, jaccard, normalize, shingles


def _ex(example_id: str, before: str, after: str, created: str = "2024-10-01") -> dict[str, Any]:
    return {"id": example_id, "before": before, "after": after, "created": created, "org": "o"}


def test_normalize_collapses_whitespace_but_keeps_identifiers() -> None:
    assert normalize("  def  foo_bar( x ) :  ") == "def foo_bar( x ) :"
    assert "foo_bar" in normalize("def foo_bar(x):")


def test_jaccard_is_one_for_identical_and_zero_for_disjoint() -> None:
    assert jaccard(shingles("abcdefgh"), shingles("abcdefgh")) == 1.0
    assert jaccard(frozenset({"a"}), frozenset({"b"})) == 0.0


def test_dedup_removes_exact_duplicates_and_keeps_the_earliest() -> None:
    kept, removed = dedup(
        [
            _ex("late", "x = 1", "x = 2", created="2024-12-01"),
            _ex("early", "x = 1", "x = 2", created="2024-10-01"),
        ]
    )
    assert [e["id"] for e in kept] == ["early"]
    assert removed["exact"] == 1


def test_dedup_removes_near_duplicates_above_the_threshold() -> None:
    base = "the quick brown fox jumps over the lazy dog and then keeps running"
    kept, removed = dedup(
        [_ex("a", base, base + "!"), _ex("b", base + " x", base + "! x")], threshold=0.8
    )
    assert len(kept) == 1 and removed["near_duplicate"] == 1


def test_dedup_drops_examples_that_are_mostly_repeated_boilerplate() -> None:
    # threshold=0.99 keeps stage 2 out of the way so this isolates stage 3.
    header = "copyright 2024 the authors licensed under the apache license version two "
    examples = [_ex(f"h{i}", header + f"body number {i} " * 2, "after") for i in range(25)]
    kept, removed = dedup(
        examples, threshold=0.99, boilerplate_document_fraction=0.5, boilerplate_fraction=0.5
    )
    assert removed["boilerplate"] == 25 and kept == []


def test_dedup_keeps_distinct_examples_untouched() -> None:
    kept, removed = dedup([_ex("a", "alpha beta gamma", "x"), _ex("b", "zeta eta theta", "y")])
    assert len(kept) == 2
    assert removed == {"exact": 0, "near_duplicate": 0, "boilerplate": 0, "shared_change_id": 0}


def test_one_example_per_id_keeping_the_earliest() -> None:
    """A Change-Id shared across cherry-picks yields one id for two different changes."""
    from sphragis.corpus.dedup import dedup

    early = {"id": "qt:I1:a.py:1:10", "created": "2024-10-03", "before": "x = 1", "after": "x = 2"}
    late = {
        "id": "qt:I1:a.py:1:10",
        "created": "2024-11-24",
        "before": "completely other code here",
        "after": "entirely different refinement text",
    }
    kept, removed = dedup([late, early])
    assert [e["created"] for e in kept] == ["2024-10-03"]
    assert removed["shared_change_id"] == 1


def test_identical_copies_are_still_counted_as_exact_not_as_shared_ids() -> None:
    """Placing the id pass last keeps the recorded exact counts where they were."""
    from sphragis.corpus.dedup import dedup

    row = {"id": "os:I2:b.py:1:5", "created": "2024-10-01", "before": "a", "after": "b"}
    kept, removed = dedup([row, dict(row, created="2024-12-01")])
    assert len(kept) == 1
    assert removed["exact"] == 1
    assert removed["shared_change_id"] == 0
