"""The rules-file comparator's distillation: chunks, rule lines, and the map and reduce passes."""

from __future__ import annotations

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
    """A length under which every source fills nearly half a chunk, so a chunk holds two changes
    and the separator between them."""
    return CHUNK_TOKENS // 2 - 8 if text.split()[-1].startswith("s") else len(text.split())


class _Cites(_Model):
    """Answers each map with one rule citing both of its chunk's changes, and one citing one;
    merges with rules on the lists' wording citing three lists, two lists and one, one citing
    none, and one citing a list it shares no wording with."""

    def __call__(self, prompt: str, max_new_tokens: int) -> tuple[str, bool]:
        self.calls.append((prompt, max_new_tokens))
        if "numbered lists" in prompt:
            return (
                "- `shared` alpha. [1, 2]\n- `shared` beta. [3]\n"
                "- `shared` gamma. [1, 2, 3]\n- bare.\n- Use black for formatting. [2]"
            ), False
        n = len(self.calls)
        return (
            f"- rule {n} on `shared` alpha beta gamma. [1, 2]\n- lone {n}. [1]\n- none {n}.",
            False,
        )


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
    assert "- rule 1 on `shared` alpha beta gamma." in reduce_call and "[1, 2]" not in reduce_call
    assert "lone" not in reduce_call and "none" not in reduce_call
    assert result["evidence"] == [{"listed": 3, "kept": 1}] * 3
    # Kept when it cites a list it shares wording with, the most-cited first; an uncited rule,
    # or one labelled with a list it shares nothing with, is the merge's own.
    assert result["rules"] == ["- `shared` gamma.", "- `shared` alpha.", "- `shared` beta."]
    assert result["recurrence"] == [[1, 2, 3], [1, 2], [3]]


def test_a_map_keeps_its_first_rules_up_to_the_limit() -> None:
    from sphragis.experiment.rules import MAX_MAP_RULES

    class Long(_Cites):
        def __call__(self, prompt: str, max_new_tokens: int) -> tuple[str, bool]:
            if "numbered lists" in prompt:
                return super().__call__(prompt, max_new_tokens)
            self.calls.append((prompt, max_new_tokens))
            rules = (
                f"- r{i} on `shared` alpha beta gamma. [1, 2]" for i in range(MAX_MAP_RULES + 5)
            )
            return "\n".join(rules), False

    result = distil(
        [f"s{n}" for n in range(6)], kind="reviews", generate=Long(), length=_half_chunk
    )
    assert result["evidence"] == [{"listed": MAX_MAP_RULES + 5, "kept": MAX_MAP_RULES}] * 3


def test_merged_rules_are_cut_to_the_file_budget_from_the_least_cited() -> None:
    from sphragis.experiment.rules import recurring

    lines = ["- `aaa` here. [1, 2, 3]", "- `bbb` here. [1, 2]", "- `ccc` here. [2, 3]"]
    lists = [["- `aaa` here, `bbb` here and `ccc` here."]] * 3
    rules, cites = recurring(lines, lists=lists, length=lambda t: 10**6 if "ccc" in t else 1)
    assert rules == ["- `aaa` here.", "- `bbb` here."] and cites == [[1, 2, 3], [1, 2]]


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


# Every grounding case a review raised, with what `grounded` decides: the specification, so a
# change to the predicate that flips one fails here.
GROUNDING_CASES = [
    # Restatements of a source rule.
    ("- Prefer `joinedload` for eager loads.", "- Use `joinedload` for eager loads.", True),
    # One word, however distinctive, is not support.
    ("- Prefer `joinedload`.", "- Replace `joinedload_all` with `joinedload`.", False),
    ("- Use oslo_log for logging.", "- Log with oslo_log for logging, not print.", True),
    ("- Use oslo.log for logging.", "- Log via oslo.log for logging, not print.", True),
    ("- Log through oslo logging.", "- Route messages via oslo logging helpers.", True),
    ("- Import `os` lazily.", "- Import `os` lazily, never at module top.", True),
    ("- Use `assertEqual` rather than `assertTrue`.", "- Use assertEqual over assertTrue.", True),
    ("- Use the `Session` class.", "- Use the Session class.", True),
    ("- Don't use print for logging.", "- Never use print for logging.", True),
    # A rule whose only subject is a name every Python file holds cannot be grounded (one word).
    ("- Compare with `is None`, not `== None`.", "- Compare to None with is.", False),
    # Invented, or sharing only a name, a keyword, an abbreviation or one word.
    ("- Use black for formatting.", "- Keep formatting changes apart.", False),
    ("- Follow OpenStack hacking rules.", "- Name OpenStack services in lowercase.", False),
    ("- Use `in` to test keys.", "- Check membership with `in` on sets.", False),
    ("- Prefer short names, e.g. ids.", "- Wrap long calls, e.g. in parens.", False),
    ("- Return `None` from handlers.", "- Compare to `None` with is.", False),
    ("- Use `self`.", "- Pass `self` first.", False),
    ("- Use `true`/`false` instead of `'1'`.", "- Number lists from 1.", False),
    ("- Compare with `is None`, not `== None`.", "- Compare floats with assertAlmostEqual.", False),
    ("- Use 4 spaces.", "- Indent with 2 spaces.", False),
    ("- Set `self.x` to `None`.", "- Return `None` from `self.close`.", False),
    ("- Use `self` and `cls`.", "- Name `self` and `cls` in methods.", False),
    ("- Return `None`, not `False`.", "- Never compare `True`/`False`/`None` with ==.", False),
    ("- Couldn't log here, mustn't print.", "- Mustn't print, couldn't skip.", False),
    ("- Use the.", "- Use the logger.", False),
    # The stated limit: support is lexical, so an inverted or altered rule passes.
    ("- Use print for logging.", "- Log with oslo_log for logging, not print.", True),
    ("- Indent with 4 spaces.", "- Indent with 2 spaces.", True),
]


@pytest.mark.parametrize(("rule", "source", "expected"), GROUNDING_CASES)
def test_grounding_decides_every_case_a_review_raised(
    rule: str, source: str, expected: bool
) -> None:
    from sphragis.experiment.rules import content_words, grounded, supports

    assert grounded(rule, source) is expected
    # The word sets `recurring` compares decide the same.
    assert supports(content_words(rule), content_words(source)) is expected


def test_a_rule_the_merge_wrote_twice_is_kept_once_at_its_best_cited_copy() -> None:
    from sphragis.experiment.rules import recurring

    lines = ["- Use `oslo_log` here. [1]", "- use  `oslo_log` here [2, 3]"]
    lines += ["- Use `Session` too. [2]", "- Use `session` too. [3]"]
    lists = [["- Use `oslo_log` here."], ["- Use `oslo_log` here, `Session` too."]]
    lists += [["- Use `oslo_log` here, `session` too."]]
    rules, cites = recurring(lines, lists=lists, length=len)
    # Copies merge their citations, identifier case aside, as grounding reads them.
    assert rules == ["- Use `oslo_log` here.", "- Use `Session` too."]
    assert cites == [[1, 2, 3], [2, 3]]


def test_a_half_whose_lists_hold_no_rule_with_its_evidence_is_refused() -> None:
    no_evidence = lambda p, n: ("- a. [1]", False)  # noqa: E731
    with pytest.raises(ValueError, match="no list holds a rule with its evidence"):
        distil(
            [f"s{n}" for n in range(6)], kind="reviews", generate=no_evidence, length=_half_chunk
        )


def test_packing_counts_the_separator_between_texts() -> None:
    from sphragis.experiment.rules import packed

    # Two texts of 5 and a separator of 1 fill 11, one over a budget of 10.
    assert packed(["a", "b"], length=lambda t: 1 if t == "\n\n" else 5, budget=10) == [["a"], ["b"]]
    assert packed(["a", "b"], length=lambda t: 1 if t == "\n\n" else 5, budget=11) == [["a", "b"]]


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


def test_the_pipeline_fingerprint_covers_the_code_distillation_runs_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sphragis.experiment import rules

    before = pipeline()
    # The budget's value, set outside this module, moves it.
    monkeypatch.setattr(rules, "MAX_SEQ_LENGTH", rules.MAX_SEQ_LENGTH + 1)
    assert pipeline() != before


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


def test_copies_of_a_rule_meet_the_threshold_together(monkeypatch: pytest.MonkeyPatch) -> None:
    from sphragis.experiment import rules

    monkeypatch.setattr(rules, "MIN_CITED_LISTS", 2)
    lines = ["- Use `ddt` here. [1]", "- use `ddt` here [2]", "- Use `mock` here. [1]"]
    lists = [["- Use `ddt` here, `mock` here."]] * 2
    kept, cites = rules.recurring(lines, lists=lists, length=len)
    assert kept == ["- Use `ddt` here."] and cites == [[1, 2]]
