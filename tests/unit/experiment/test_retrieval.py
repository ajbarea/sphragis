"""BM25 few-shot retrieval, the comparator the adapters are read against."""

from __future__ import annotations

import pytest

from sphragis.experiment.holdout import equalize_training
from sphragis.experiment.retrieval import (
    BM25,
    EQUALIZE_SEED,
    arm_key,
    arm_prompts,
    condition,
    few_shot_prompt,
    pools,
    resumable,
    shot_similarity,
    tokens,
)
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


def test_shots_are_prior_chat_turns_in_the_format_the_adapters_trained_on() -> None:
    target = _row(1, "fix it", "a = 1")
    shot = _row(2, "rename", "b = 1", after="bb = 1")
    assert few_shot_prompt(target, [shot]) == [
        {"role": "user", "content": build_prompt(shot)},
        {"role": "assistant", "content": "bb = 1"},
        {"role": "user", "content": build_prompt(target)},
    ]


def test_the_closest_shot_is_the_turn_before_the_targets() -> None:
    target = _row(1, "fix it", "a = 1")
    closest, farther = _row(2, "near", "b = 1"), _row(3, "far", "c = 1")
    turns = few_shot_prompt(target, [closest, farther])
    assert isinstance(turns, list)
    assert [t["content"] for t in turns if t["role"] == "user"] == [
        build_prompt(farther),
        build_prompt(closest),
        build_prompt(target),
    ]


def test_words_of_any_script_are_tokens() -> None:
    assert tokens("Bitte für alle Sprachen prüfen; проверьте 修正") == [
        "bitte",
        "für",
        "alle",
        "sprachen",
        "prüfen",
        "проверьте",
        "修正",
    ]


def test_a_term_repeated_in_the_query_weighs_more() -> None:
    pool = [_row(1, "timeout", "x = 1"), _row(2, "retry", "y = 2")]
    index = BM25(pool)
    # Once each, the tie goes to pool order; repeated, the repeated term decides.
    assert index.top(_row(9, "timeout retry", "z"), 1)[0]["id"] == "1"
    assert index.top(_row(9, "timeout retry retry retry", "z"), 1)[0]["id"] == "2"


def test_an_empty_pool_is_refused() -> None:
    with pytest.raises(ValueError, match="empty pool"):
        BM25([])


def test_an_arm_is_keyed_as_an_adapter_arm_is_without_a_seed() -> None:
    assert arm_key(3, "openstack-a", "openstack-b") == "retrieval-k3:openstack-a|openstack-b"
    assert condition(1) == "retrieval-k1"


def test_the_smaller_k_takes_the_nearest_of_the_larger_ks_shots() -> None:
    pool = [_row(n, f"comment {n} rename", f"v{n} = {n}") for n in range(6)]
    target = _row(9, "comment 2 rename", "v2 = 3")
    prompts, _ = arm_prompts([target], {"p": BM25(pool)}, evaluated="e", ks=(1, 3))
    nearest = BM25(pool).top(target, 3)
    assert prompts[arm_key(1, "p", "e")] == [few_shot_prompt(target, nearest[:1])]
    assert prompts[arm_key(3, "p", "e")] == [few_shot_prompt(target, nearest)]


def test_a_pool_is_the_equalized_cut_less_the_rows_training_refuses() -> None:
    train = {h: [_row(n, f"c{n}", f"b{n}") for n in range(10)] for h in ("x-a", "x-b")}
    cut = equalize_training(train, seed=EQUALIZE_SEED, size=6)
    built = pools(train, size=6, fits=lambda row: int(row["id"]) % 2 == 0)
    for half in train:
        assert [r["id"] for r in built[half].pool] == [
            r["id"] for r in cut[half] if int(r["id"]) % 2 == 0
        ]


def test_a_shot_carrying_the_targets_answer_is_a_near_duplicate() -> None:
    target = _row(
        1, "rename it", "value = compute_total(items)", after="total = compute_total(items)"
    )
    copy = dict(target, id="2")
    other = _row(3, "use a logger", "print(message)", after="log.info(message)")
    assert shot_similarity(target, [other, copy]) == 1.0
    assert shot_similarity(target, [other]) < 0.7
    assert shot_similarity(target, []) == 0.0


def test_a_list_or_number_line_is_unreadable_not_a_crash() -> None:
    kept, dropped = resumable(["[1]", "7", '"x"'], {})
    assert kept == {} and dropped == ["<unreadable line>"]


def test_an_admissible_list_must_name_its_organization() -> None:
    from sphragis.experiment.retrieval import first_partitions

    with pytest.raises(ValueError, match="None's partitions"):
        first_partitions({"admissible": list(range(10)), "size_floor": 5}, org="openstack")
