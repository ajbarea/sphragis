"""The rules-file comparator's distillation: chunks, rule lines, and the map and reduce passes."""

from __future__ import annotations

from pathlib import Path

import pytest

from sphragis.experiment.rules import (
    CHUNK_TOKENS,
    MAP_ANSWER_TOKENS,
    MAP_GUIDE,
    MAP_REVIEWS,
    REDUCE_GUIDE,
    REDUCE_REVIEWS,
    RULES_BUDGET,
    chunks,
    cited,
    distil,
    pipeline,
    review_text,
    rule_lines,
)
from sphragis.experiment.rules_arms import DEFAULT_SYSTEM, rules_system


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


def _half_chunk(text: str) -> int:
    """A length under which every source fills half a chunk, so a chunk holds two changes."""
    return CHUNK_TOKENS // 2 if text.split()[-1].startswith("s") else len(text.split())


class _Cites(_Model):
    """Answers each map with one rule citing both of its chunk's changes, and one citing one;
    merges with rules on the lists' wording citing three lists, two lists and one, one citing
    none, and one citing a list it shares no wording with."""

    def __call__(self, prompt: str, max_new_tokens: int) -> tuple[str, bool]:
        self.calls.append((prompt, max_new_tokens))
        if "numbered lists" in prompt:
            return (
                "- two on `shared`. [1, 2]\n- one on `shared`. [3]\n"
                "- three on `shared`. [1, 2, 3]\n- bare.\n- Use black for formatting. [2]"
            ), False
        n = len(self.calls)
        return f"- rule {n} on `shared`. [1, 2]\n- lone {n}. [1]\n- none {n}.", False


def test_each_chunk_of_a_guide_is_mapped_then_one_reduce_merges_their_lists() -> None:
    model = _Model()
    length = lambda t: CHUNK_TOKENS if t.startswith("s") else len(t.split())  # noqa: E731
    result = distil(["s1", "s2", "s3"], kind="guide", generate=model, length=length)
    assert result["chunks"] == 3 and len(model.calls) == 4
    *maps, (reduce_call, reduce_budget) = model.calls
    for (map_call, map_budget), source in zip(maps, ("s1", "s2", "s3"), strict=True):
        assert map_call.startswith(MAP_GUIDE.split("{source}")[0])
        assert map_call.endswith(source) and map_budget == MAP_ANSWER_TOKENS
    assert reduce_budget == RULES_BUDGET
    assert reduce_call.startswith(REDUCE_GUIDE.split("{lists}")[0])
    assert result["rules"] == ["- rule 1", "- rule 2", "- rule 3"]
    assert result["pipeline"] == pipeline() and result["evidence"] == []


def test_a_mined_rule_stands_on_two_of_its_chunks_numbered_changes() -> None:
    model = _Cites()
    sources = [f"s{n}" for n in range(1, 7)]
    result = distil(sources, kind="reviews", generate=model, length=_half_chunk)
    assert result["chunks"] == 3
    first_map = model.calls[0][0]
    assert first_map.startswith(MAP_REVIEWS.split("{source}")[0])
    assert "### Change 1\ns1" in first_map and "### Change 2\ns2" in first_map
    # Only the rule citing both changes reaches the merge, without its brackets.
    reduce_call = model.calls[-1][0]
    assert reduce_call.startswith(REDUCE_REVIEWS.split("{lists}")[0])
    assert "- rule 1 on `shared`." in reduce_call and "[1, 2]" not in reduce_call
    assert "lone" not in reduce_call and "none" not in reduce_call
    assert result["evidence"] == [{"listed": 3, "kept": 1}] * 3
    # Kept when it cites a list it shares wording with, the most-cited first; an uncited rule,
    # or one labelled with a list it shares nothing with, is the merge's own.
    assert result["rules"] == ["- three on `shared`.", "- two on `shared`.", "- one on `shared`."]
    assert result["recurrence"] == [[1, 2, 3], [1, 2], [3]]


def test_a_map_keeps_its_first_rules_up_to_the_limit() -> None:
    from sphragis.experiment.rules import MAX_MAP_RULES

    class Long(_Cites):
        def __call__(self, prompt: str, max_new_tokens: int) -> tuple[str, bool]:
            if "numbered lists" in prompt:
                return super().__call__(prompt, max_new_tokens)
            self.calls.append((prompt, max_new_tokens))
            rules = (f"- r{i} on `shared`. [1, 2]" for i in range(MAX_MAP_RULES + 5))
            return "\n".join(rules), False

    result = distil(
        [f"s{n}" for n in range(6)], kind="reviews", generate=Long(), length=_half_chunk
    )
    assert result["evidence"] == [{"listed": MAX_MAP_RULES + 5, "kept": MAX_MAP_RULES}] * 3


def test_merged_rules_are_cut_to_the_file_budget_from_the_least_cited() -> None:
    from sphragis.experiment.rules import recurring

    lines = ["- `aaa`. [1, 2, 3]", "- `bbb`. [1, 2]", "- `ccc`. [2, 3]"]
    lists = [["- `aaa`, `bbb` and `ccc`."]] * 3
    rules, cites = recurring(lines, lists=lists, length=lambda t: 10**6 if "ccc" in t else 1)
    assert rules == ["- `aaa`.", "- `bbb`."] and cites == [[1, 2, 3], [1, 2]]


def test_a_list_left_empty_is_not_numbered_for_the_merge() -> None:
    class Gappy(_Cites):
        def __call__(self, prompt: str, max_new_tokens: int) -> tuple[str, bool]:
            if "numbered lists" in prompt or len(self.calls) != 1:
                return super().__call__(prompt, max_new_tokens)
            self.calls.append((prompt, max_new_tokens))
            return "- lone. [1]", False

    model = Gappy()
    result = distil([f"s{n}" for n in range(8)], kind="reviews", generate=model, length=_half_chunk)
    reduce_call = model.calls[-1][0]
    assert result["evidence"][1] == {"listed": 1, "kept": 0}
    assert "List 3:" in reduce_call and "List 4:" not in reduce_call
    # The merge's list 2 is chunk 3, recorded so a citation reads back to its chunk.
    assert result["merge_chunks"] == [1, 3, 4]


def test_a_rule_is_grounded_by_a_shared_identifier_or_two_uncommon_words() -> None:
    from sphragis.experiment.rules import grounded

    assert grounded("- Prefer `joinedload` here.", "- Replace `joinedload_all` with `joinedload`.")
    assert grounded("- Log through oslo logging.", "- Route messages via oslo logging helpers.")
    # One identifier is enough, ticked or not; a trivial ticked word is not one.
    assert grounded("- Use oslo_log for logging.", "- Log with oslo_log, not print.")
    assert not grounded("- Return `None` from handlers.", "- Compare to `None` with is.")
    assert not grounded("- Use black for formatting.", "- Use `joinedload` instead of others.")
    assert not grounded("- Use the logger.", "- Use the logger instead.")


def test_a_rule_the_merge_wrote_twice_is_kept_once_at_its_best_cited_copy() -> None:
    from sphragis.experiment.rules import recurring

    lines = ["- Use `oslo_log`. [1]", "- Use `oslo_log`. [1, 2]", "- Use `ddt`. [2]"]
    lists = [["- `oslo_log` and `ddt`."]] * 2
    rules, cites = recurring(lines, lists=lists, length=len)
    assert rules == ["- Use `oslo_log`.", "- Use `ddt`."] and cites == [[1, 2], [2]]


def test_mined_rules_need_three_lists_with_evidence() -> None:
    with pytest.raises(ValueError, match="recurrence across lists needs 3"):
        distil(["s1", "s2", "s3", "s4"], kind="reviews", generate=_Cites(), length=_half_chunk)
    no_evidence = lambda p, n: ("- a. [1]", False)  # noqa: E731
    with pytest.raises(ValueError, match="0 lists hold a rule with its evidence"):
        distil(
            [f"s{n}" for n in range(6)], kind="reviews", generate=no_evidence, length=_half_chunk
        )


@pytest.mark.parametrize(
    "line,changes,expected",
    [
        ("- Use oslo_log. [3, 17]", 20, ("- Use oslo_log.", {3, 17})),
        ("- Use oslo_log [3,17].", 20, ("- Use oslo_log", {3, 17})),
        ("- Out of range. [4, 99]", 10, ("- Out of range.", {4})),
        ("- No evidence.", 10, ("- No evidence.", set())),
        ("- Mid [2] line.", 10, ("- Mid [2] line.", set())),
    ],
)
def test_cited_change_numbers_are_read_from_the_lines_end(
    line: str, changes: int, expected: tuple[str, set[int]]
) -> None:
    assert cited(line, changes) == expected


def test_a_reduce_that_lists_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="listed no rules"):
        distil(["a"], kind="guide", generate=lambda p, n: ("no list here", False), length=_words)


def test_bad_inputs_are_refused() -> None:
    with pytest.raises(ValueError, match="kind must be"):
        distil(["a"], kind="other", generate=lambda p, n: ("- r", False), length=_words)
    with pytest.raises(ValueError, match="no sources"):
        distil([], kind="guide", generate=lambda p, n: ("- r", False), length=_words)


def test_the_reviews_merge_cites_its_lists_and_the_guide_merge_keeps_all() -> None:
    assert "numbers of the lists it appears" in REDUCE_REVIEWS
    assert "numbers of the lists" not in REDUCE_GUIDE


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


def test_the_pipeline_fingerprint_is_the_modules_source() -> None:
    import hashlib

    from sphragis.experiment import rules

    assert pipeline() == hashlib.sha256(Path(rules.__file__).read_bytes()).hexdigest()


def test_mined_rules_ask_for_the_particular_and_a_guide_keeps_what_it_states() -> None:
    assert "Leave out general good practice" in MAP_REVIEWS
    assert "Leave out general good practice" in REDUCE_REVIEWS
    assert "Leave out general good practice" not in MAP_GUIDE
    assert "Leave out general good practice" not in REDUCE_GUIDE


class _Template:
    """A chat template that writes `default` as the system turn when none is given."""

    def __init__(self, default: str) -> None:
        self.default = default

    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        if messages[0]["role"] != "system":
            messages = [{"role": "system", "content": self.default}, *messages]
        return "|".join(f"{m['role']}:{m['content']}" for m in messages) + "|assistant:"


def test_the_default_system_turn_is_checked_against_the_template() -> None:
    from sphragis.experiment.rules_arms import default_system_holds

    assert default_system_holds(_Template(DEFAULT_SYSTEM))
    assert not default_system_holds(_Template("You are a different assistant."))


def test_a_guide_file_is_held_to_the_caps_of_a_distilled_one() -> None:
    from sphragis.experiment.rules import MAX_FILE_RULES

    many = lambda p, n: ("\n".join(f"- g{i}." for i in range(MAX_FILE_RULES + 9)), False)  # noqa: E731
    result = distil(["s1"], kind="guide", generate=many, length=lambda t: len(t.split()))
    assert len(result["rules"]) == MAX_FILE_RULES


def test_the_file_budget_is_counted_by_the_model_that_reads_it() -> None:
    many = lambda p, n: ("\n".join(f"- g{i}." for i in range(6)), False)  # noqa: E731
    result = distil(
        ["s1"],
        kind="guide",
        generate=many,
        length=lambda t: 1,
        file_length=lambda t: RULES_BUDGET + 1 if t.count("\n") >= 2 else len(t),
    )
    assert result["rules"] == ["- g0.", "- g1."] and result["file_tokens"] == len("- g0.\n- g1.")
