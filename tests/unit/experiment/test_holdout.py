"""The pilot's held-out split: changes whole on one side, and content leakage measured."""

from __future__ import annotations

import random
from collections.abc import Mapping
from typing import Any

import pytest

from sphragis.experiment.holdout import (
    equalize_training,
    holdout_by_change,
    matched_foreign_draw,
    split_by_window,
    verbatim_overlap,
)


def _rows(n_changes: int, per_change: int = 3, prefix: str = "I") -> list[dict[str, Any]]:
    return [
        {
            "id": f"{prefix}{c}:{e}",
            "change_id": f"{prefix}{c}",
            "before": f"b{prefix}{c}.{e}",
            "after": f"a{prefix}{c}.{e}",
        }
        for c in range(n_changes)
        for e in range(per_change)
    ]


def test_no_change_appears_on_both_sides() -> None:
    train, held_out = holdout_by_change(_rows(50), seed=0)
    assert {r["change_id"] for r in train}.isdisjoint({r["change_id"] for r in held_out})
    assert len(train) + len(held_out) == 150


def test_the_split_is_reproducible_and_seed_dependent() -> None:
    rows = _rows(50)
    assert holdout_by_change(rows, seed=3) == holdout_by_change(rows, seed=3)
    assert holdout_by_change(rows, seed=3)[1] != holdout_by_change(rows, seed=4)[1]


def test_the_held_out_share_is_a_share_of_changes_not_examples() -> None:
    uneven = _rows(10, per_change=1) + _rows(1, per_change=40, prefix="BIG")
    _, held_out = holdout_by_change(uneven, seed=0, eval_fraction=0.2)
    assert len({r["change_id"] for r in held_out}) == 11 - int(0.8 * 11)


def test_the_first_pilot_split_is_reproduced_exactly() -> None:
    """scripts/pilot.py used int(0.8 * n) on a seeded shuffle; moving it must not move it."""
    rows = _rows(88)
    changes = sorted({r["change_id"] for r in rows})
    random.Random(0).shuffle(changes)
    expected = set(changes[int(0.8 * len(changes)) :])
    _, held_out = holdout_by_change(rows, seed=0)
    assert {r["change_id"] for r in held_out} == expected


@pytest.mark.parametrize("fraction", [0.0, 1.0, -0.1])
def test_a_degenerate_fraction_is_refused(fraction: float) -> None:
    with pytest.raises(ValueError, match="eval_fraction"):
        holdout_by_change(_rows(10), seed=0, eval_fraction=fraction)


def test_verbatim_overlap_finds_the_same_edit_carried_by_two_changes() -> None:
    train = [{"id": "t", "change_id": "I1", "before": "x=1", "after": "x = 1"}]
    held_out = [
        {"id": "h1", "change_id": "I2", "before": "x=1", "after": "x = 1"},
        {"id": "h2", "change_id": "I3", "before": "y=1", "after": "y = 1"},
    ]
    assert verbatim_overlap(train, held_out) == ["h1"]


def test_equalize_cuts_every_organization_to_the_smallest() -> None:
    train = {"openstack": _rows(10, per_change=1), "qt": _rows(30, per_change=1, prefix="Q")}
    out = equalize_training(train, seed=1)
    assert {org: len(rows) for org, rows in out.items()} == {"openstack": 10, "qt": 10}


def test_equalize_draws_a_subset_not_a_prefix_and_is_reproducible() -> None:
    qt = _rows(30, per_change=1, prefix="Q")
    train = {"openstack": _rows(10, per_change=1), "qt": qt}
    first = equalize_training(train, seed=1)["qt"]
    assert first == equalize_training(train, seed=1)["qt"]
    assert first != qt[:10], "a prefix would bias toward the earliest examples"
    assert {r["id"] for r in first} <= {r["id"] for r in qt}
    assert first != equalize_training(train, seed=2)["qt"]


def test_equalize_refuses_an_organization_with_nothing_to_train_on() -> None:
    with pytest.raises(ValueError, match="no training examples"):
        equalize_training({"openstack": [], "qt": _rows(3)}, seed=0)


WINDOWED = {
    "pilot": _rows(3, prefix="P"),
    "train": _rows(5, prefix="T"),
    "dev": _rows(2, prefix="D"),
    "test": _rows(9, prefix="X"),
}


def test_split_by_window_takes_the_two_named_windows() -> None:
    train, held_out = split_by_window(WINDOWED, train_window="train", eval_window="dev")
    assert {r["change_id"] for r in train} == {f"T{i}" for i in range(5)}
    assert {r["change_id"] for r in held_out} == {f"D{i}" for i in range(2)}


@pytest.mark.parametrize(
    ("train_window", "eval_window"), [("train", "test"), ("test", "dev"), ("test", "test")]
)
def test_split_by_window_refuses_the_sealed_window(train_window: str, eval_window: str) -> None:
    """One typo away from spending the confirmatory set before acceptance."""
    with pytest.raises(ValueError, match="sealed"):
        split_by_window(WINDOWED, train_window=train_window, eval_window=eval_window)


def test_split_by_window_refuses_an_unknown_window() -> None:
    with pytest.raises(ValueError, match="no window named"):
        split_by_window(WINDOWED, train_window="train", eval_window="validation")


def test_split_by_window_refuses_to_evaluate_on_the_training_window() -> None:
    with pytest.raises(ValueError, match="both"):
        split_by_window(WINDOWED, train_window="train", eval_window="train")


def test_split_by_window_copies_rather_than_aliasing_the_windows() -> None:
    train, _ = split_by_window(WINDOWED, train_window="train", eval_window="dev")
    train[0]["change_id"] = "mutated"
    assert WINDOWED["train"][0]["change_id"] == "T0"


def test_equalize_training_cuts_to_a_fixed_size_as_a_nested_prefix() -> None:
    train = {"openstack": _rows(10), "qt": _rows(20)}  # 30 and 60 examples
    full = equalize_training(train, seed=1)
    capped = equalize_training(train, seed=1, size=4)
    assert {org: len(rows) for org, rows in capped.items()} == {"openstack": 4, "qt": 4}
    for org in train:
        assert capped[org] == full[org][:4]


@pytest.mark.parametrize("size", [0, 31])
def test_equalize_training_refuses_a_size_it_cannot_honour(size: int) -> None:
    with pytest.raises(ValueError, match="size must be between"):
        equalize_training({"openstack": _rows(10), "qt": _rows(20)}, seed=1, size=size)


def test_a_reader_that_must_not_hold_sealed_rows_refuses_a_corpus_that_does(tmp_path) -> None:
    import json

    import pytest

    from sphragis.corpus.load import refined_dir, write_build_record, write_source_record
    from sphragis.experiment.holdout import window_split

    train_day, test_day = "2025-01-10 00:00:00.000000000", "2025-12-10 00:00:00.000000000"
    rows = [
        {
            "id": f"o:c{n}:f:1",
            "change_id": f"c{n}",
            "project": "p",
            "org": "o",
            "created": created,
            "comments": [f"c{n} " * n],
            "before": f"b{n} " * n,
            "after": f"a{n} " * n,
        }
        for n, created in ((1, train_day), (2, test_day))
    ]
    text = "".join(json.dumps(r) + "\n" for r in rows)
    built = tmp_path / "o" / "examples" / "2025-01.jsonl"
    built.parent.mkdir(parents=True)
    built.write_text(text)
    write_build_record(built, None, complete=True)
    refined = refined_dir(tmp_path, "o") / "2025-01.jsonl"
    refined.parent.mkdir(parents=True)
    refined.write_text(text)
    write_source_record(tmp_path, "o", built, refined)
    window_split(tmp_path, "o")
    with pytest.raises(ValueError, match="holds test-window examples"):
        window_split(tmp_path, "o", refuse_sealed_rows=True)


# The matched foreign draw (H2's foreign arm)


def _lang_rows(prefix: str, cpp: int, other: int) -> list[dict[str, Any]]:
    return [{"id": f"{prefix}:c{i}", "path": f"f{i}.cc"} for i in range(cpp)] + [
        {"id": f"{prefix}:o{i}", "path": f"f{i}.py"} for i in range(other)
    ]


def _cc(row: Mapping[str, Any]) -> bool:
    return str(row["path"]).endswith(".cc")


def test_a_matched_draw_matches_the_siblings_size_and_in_language_count() -> None:
    sibling = _lang_rows("qt-b", cpp=6, other=4)
    pool = _lang_rows("aosp", cpp=20, other=20)
    draw = matched_foreign_draw(sibling, pool, in_language=_cc, seed=3, sibling="qt-b")
    assert len(draw) == len(sibling)
    assert sum(map(_cc, draw)) == 6
    ids = [r["id"] for r in draw]
    assert len(set(ids)) == len(ids) and set(ids) <= {r["id"] for r in pool}


def test_a_matched_draw_is_reproducible_and_independent_per_sibling() -> None:
    sibling = _lang_rows("s", cpp=5, other=5)
    pool = _lang_rows("aosp", cpp=40, other=40)

    def ids(seed: int, name: str) -> list[str]:
        draw = matched_foreign_draw(sibling, pool, in_language=_cc, seed=seed, sibling=name)
        return [r["id"] for r in draw]

    assert ids(1, "qt-a") == ids(1, "qt-a")
    assert ids(1, "qt-a") != ids(1, "qt-b")
    assert ids(1, "qt-a") != ids(2, "qt-a")


@pytest.mark.parametrize(
    ("pool", "match"),
    [
        (_lang_rows("p", cpp=5, other=10), "5 in-language"),
        (_lang_rows("p", cpp=10, other=3), "3 other"),
    ],
)
def test_a_pool_that_cannot_cover_the_draw_is_refused(
    pool: list[dict[str, Any]], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        matched_foreign_draw(
            _lang_rows("s", cpp=6, other=4), pool, in_language=_cc, seed=1, sibling="qt-a"
        )


def test_an_empty_sibling_draw_is_refused() -> None:
    with pytest.raises(ValueError, match="empty"):
        matched_foreign_draw([], _lang_rows("p", 3, 3), in_language=_cc, seed=1, sibling="qt-a")


@pytest.mark.parametrize(("cpp", "other"), [(0, 6), (6, 0)])
def test_a_matched_draw_with_one_empty_stratum_takes_only_the_other(cpp: int, other: int) -> None:
    sibling = _lang_rows("s", cpp=cpp, other=other)
    draw = matched_foreign_draw(
        sibling, _lang_rows("p", 10, 10), in_language=_cc, seed=1, sibling="qt-a"
    )
    assert len(draw) == 6 and sum(map(_cc, draw)) == cpp


def test_a_pool_of_exactly_the_needed_size_is_used_whole_and_left_unchanged() -> None:
    pool = _lang_rows("p", cpp=4, other=2)
    before = [dict(r) for r in pool]
    draw = matched_foreign_draw(
        _lang_rows("s", 4, 2), pool, in_language=_cc, seed=1, sibling="qt-a"
    )
    assert sorted(r["id"] for r in draw) == sorted(r["id"] for r in pool)
    assert pool == before


def test_a_pool_with_repeated_ids_is_refused() -> None:
    pool = _lang_rows("p", cpp=3, other=3)
    pool.append(dict(pool[0]))
    with pytest.raises(ValueError, match="repeats example ids"):
        matched_foreign_draw(_lang_rows("s", 2, 2), pool, in_language=_cc, seed=1, sibling="qt-a")
