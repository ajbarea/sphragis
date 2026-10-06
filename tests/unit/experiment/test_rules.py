"""The rules-file comparator's distillation: chunks, rule lines, and the map and reduce passes."""

from __future__ import annotations

import pytest

from sphragis.experiment.rules import (
    CHUNK_TOKENS,
    DEFAULT_SYSTEM,
    MAP_ANSWER_TOKENS,
    MAP_GUIDE,
    MAP_REVIEWS,
    REDUCE_GUIDE,
    REDUCE_REVIEWS,
    RULES_BUDGET,
    chunks,
    distil,
    pipeline,
    review_text,
    rule_lines,
    rules_system,
)


def _words(text: str) -> int:
    return len(text.split())


def test_texts_are_packed_in_order_under_the_budget() -> None:
    texts = ["a b", "c d e", "f", "g h i j"]
    assert chunks(texts, length=_words, budget=5) == ["a b\n\nc d e", "f\n\ng h i j"]


def test_a_text_over_the_budget_is_a_chunk_of_its_own_not_cut() -> None:
    texts = ["a", "b c d e f g", "h"]
    assert chunks(texts, length=_words, budget=3) == ["a", "b c d e f g", "h"]


def test_only_listed_lines_are_rules() -> None:
    answer = "Here are the rules:\n- Use the logger.\n  - Name tests test_*.\nSome prose.\n-dash"
    assert rule_lines(answer) == ["- Use the logger.", "- Name tests test_*."]


def test_a_reviewed_change_shows_its_comments_code_and_revision() -> None:
    text = review_text({"comments": ["use log", "and a test"], "before": "x", "after": "y"})
    assert text.index("- use log") < text.index("Code:\nx") < text.index("Revised code:\ny")


class _Model:
    """Answers each map with one rule naming its chunk, the reduce with every rule it was shown."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def __call__(self, prompt: str, max_new_tokens: int) -> tuple[str, bool]:
        self.calls.append((prompt, max_new_tokens))
        if "Below are lists" in prompt:
            return "Merged:\n" + "\n".join(rule_lines(prompt)), False
        return f"- rule {len(self.calls)}", False


def _chunk_each(text: str) -> int:
    """A length under which every source fills a chunk of its own, and an answer is short."""
    return CHUNK_TOKENS if text.startswith("s") else len(text.split())


@pytest.mark.parametrize(
    "kind,map_prompt,reduce_prompt",
    [("reviews", MAP_REVIEWS, REDUCE_REVIEWS), ("guide", MAP_GUIDE, REDUCE_GUIDE)],
)
def test_each_chunk_is_mapped_then_one_reduce_merges_their_lists(
    kind: str, map_prompt: str, reduce_prompt: str
) -> None:
    model = _Model()
    result = distil(["s1", "s2", "s3"], kind=kind, generate=model, length=_chunk_each)
    # Three sources a chunk each: three maps, then one reduce.
    assert result["chunks"] == 3 and len(model.calls) == 4
    *maps, (reduce_call, reduce_budget) = model.calls
    for (map_call, map_budget), source in zip(maps, ("s1", "s2", "s3"), strict=True):
        assert map_call.startswith(map_prompt.split("{source}")[0])
        assert map_call.endswith(source) and map_budget == MAP_ANSWER_TOKENS
    assert reduce_budget == RULES_BUDGET
    assert reduce_call.startswith(reduce_prompt.split("{lists}")[0])
    assert "List 1:\n- rule 1" in reduce_call and "List 3:\n- rule 3" in reduce_call
    assert result["rules"] == ["- rule 1", "- rule 2", "- rule 3"]
    assert result["kind"] == kind and result["map_lists"] == ["- rule 1", "- rule 2", "- rule 3"]
    assert result["pipeline"] == pipeline()


def test_mined_rules_need_three_lists_for_recurrence_to_mean_anything() -> None:
    with pytest.raises(ValueError, match="recurrence across lists needs 3"):
        distil(["s1", "s2"], kind="reviews", generate=_Model(), length=_chunk_each)
    # A guide's merge keeps every rule, so one chunk is enough.
    assert distil(["s1"], kind="guide", generate=_Model(), length=_chunk_each)["chunks"] == 1


def test_a_reduce_that_lists_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="listed no rules"):
        distil(["a"], kind="guide", generate=lambda p, n: ("no list here", False), length=_words)


def test_bad_inputs_are_refused() -> None:
    with pytest.raises(ValueError, match="kind must be"):
        distil(["a"], kind="other", generate=lambda p, n: ("- r", False), length=_words)
    with pytest.raises(ValueError, match="no sources"):
        distil([], kind="guide", generate=lambda p, n: ("- r", False), length=_words)


def test_the_reviews_reduce_keeps_only_recurring_rules_and_the_guide_reduce_keeps_all() -> None:
    assert "at least two of the lists" in REDUCE_REVIEWS
    assert "at least two" not in REDUCE_GUIDE


def test_a_rules_arm_keeps_the_default_system_prompt_and_adds_the_file() -> None:
    system = rules_system("- Use the logger.")
    assert system.startswith(DEFAULT_SYSTEM + "\n\n") and system.endswith("- Use the logger.")


def test_a_capped_answer_is_flagged_and_its_cut_line_dropped() -> None:
    reduce_prompts: list[str] = []

    def model(prompt: str, budget: int) -> tuple[str, bool]:
        if "Below are lists" in prompt:
            reduce_prompts.append(prompt)
            return "- one\n- two\n- three", False
        # The map answer stops at its budget: three whole rules and a half one.
        return "- one\n- two\n- three\n- fou", True

    result = distil(["a"], kind="guide", generate=model, length=_words)
    assert result["map_capped"] == [True] and result["reduce_capped"] is False
    # The reduce saw the capped list without its last, cut line.
    assert "- three" in reduce_prompts[0] and "- fou" not in reduce_prompts[0]


def test_every_prompt_and_budget_is_in_the_pipeline_fingerprint(monkeypatch) -> None:
    from sphragis.experiment import rules

    before = rules.pipeline()
    monkeypatch.setattr(rules, "MAP_ANSWER_TOKENS", rules.MAP_ANSWER_TOKENS + 1)
    assert rules.pipeline() != before
    monkeypatch.undo()
    monkeypatch.setitem(rules.PROMPTS, "guide", ("x {source}", "y {lists}"))
    assert rules.pipeline() != before


def test_mined_rules_ask_for_the_particular_and_a_guide_keeps_what_it_states() -> None:
    assert "Leave out general good practice" in MAP_REVIEWS
    assert "Leave out general good practice" in REDUCE_REVIEWS
    assert "Leave out general good practice" not in MAP_GUIDE
    assert "Leave out general good practice" not in REDUCE_GUIDE
