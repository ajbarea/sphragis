"""BM25 few-shot retrieval, the comparator the adapters are read against."""

from __future__ import annotations

import pytest

from sphragis.experiment.retrieval import BM25, few_shot_prompt, tokens
from sphragis.experiment.runner import build_prompt


def _row(n: int, comment: str, before: str, after: str = "x") -> dict:
    return {"id": str(n), "comments": [comment], "before": before, "after": after}


def test_identifiers_split_into_their_parts() -> None:
    assert tokens("getServiceContainer() max_retries=3") == [
        "get",
        "service",
        "container",
        "max",
        "retries",
        "3",
    ]


def test_the_closest_example_ranks_first_and_ties_keep_pool_order() -> None:
    pool = [
        _row(1, "use the logger here", "print(x)"),
        _row(2, "rename this variable", "foo = 1"),
        _row(3, "rename this variable", "foo = 1"),
    ]
    index = BM25(pool)
    target = _row(9, "please rename the variable", "foo = 2")
    assert [r["id"] for r in index.top(target, 2)] == ["2", "3"]
    assert index.top(_row(8, "use the logger", "print(y)"), 1)[0]["id"] == "1"


def test_a_prompt_without_shots_is_the_registered_prompt() -> None:
    target = _row(1, "fix it", "a = 1")
    assert few_shot_prompt(target, []) == build_prompt(target)


def test_shots_come_solved_before_the_target_in_the_registered_template() -> None:
    target = _row(1, "fix it", "a = 1")
    shot = _row(2, "rename", "b = 1", after="bb = 1")
    prompt = few_shot_prompt(target, [shot])
    assert build_prompt(shot) in prompt and "Revised code:\nbb = 1" in prompt
    assert prompt.endswith(build_prompt(target))
    assert prompt.index(build_prompt(shot)) < prompt.index("### Now this one")


def test_the_closest_shot_is_written_next_to_the_target() -> None:
    target = _row(1, "fix it", "a = 1")
    closest, farther = _row(2, "near", "b = 1"), _row(3, "far", "c = 1")
    prompt = few_shot_prompt(target, [closest, farther])
    assert prompt.index(build_prompt(farther)) < prompt.index(build_prompt(closest))


def test_an_empty_pool_is_refused() -> None:
    with pytest.raises(ValueError, match="empty pool"):
        BM25([])
