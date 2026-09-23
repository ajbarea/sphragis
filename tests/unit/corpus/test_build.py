"""Turning a raw snapshot into refinement examples."""

from __future__ import annotations

from typing import Any

import pytest

from sphragis.corpus.build import build_from_change

CHANGE: dict[str, Any] = {
    "change_id": "I1",
    "_number": 42,
    "project": "openstack/nova",
    "created": "2024-10-02 11:00:00.000000000",
    "revisions": {"aaa": {"_number": 1}, "bbb": {"_number": 2}},
}
COMMENTS = {
    "/COMMIT_MSG": [{"patch_set": 1, "line": 3, "message": "typo"}],
    "nova/f.py": [{"patch_set": 1, "line": 2, "message": "spaces around the operator"}],
}
DIFF = {
    "content": [
        {"ab": ["def f(x):"]},
        {"a": ["    return x+1"], "b": ["    return x + 1"]},
        {"ab": ["", "def g():"]},
    ]
}


def _fetchers(diff: dict[str, Any] | None = None):
    calls: list[tuple] = []

    def comments(number: int) -> dict[str, list[dict[str, Any]]]:
        return COMMENTS

    def diff_for(number: int, rev: int, path: str, base: int) -> dict[str, Any]:
        calls.append((number, rev, path, base))
        return diff if diff is not None else DIFF

    return comments, diff_for, calls


def test_a_commented_changed_hunk_becomes_one_example() -> None:
    comments, diff_for, _ = _fetchers()
    examples, drops = build_from_change("openstack", CHANGE, comments, diff_for)
    assert len(examples) == 1
    e = examples[0]
    assert e["before"] == "    return x+1"
    assert e["after"] == "    return x + 1"
    assert e["comments"] == ["spaces around the operator"]
    assert e["org"] == "openstack" and e["change_id"] == "I1"
    assert e["path"] == "nova/f.py"
    assert drops["metadata_file"] == 1


def test_the_commit_message_never_produces_an_example() -> None:
    comments, diff_for, calls = _fetchers()
    build_from_change("openstack", CHANGE, comments, diff_for)
    assert all(path != "/COMMIT_MSG" for _, _, path, _ in calls)


def test_the_diff_is_requested_between_consecutive_patch_sets() -> None:
    comments, diff_for, calls = _fetchers()
    build_from_change("openstack", CHANGE, comments, diff_for)
    assert calls == [(42, 2, "nova/f.py", 1)]


def test_a_comment_outside_every_changed_hunk_is_dropped() -> None:
    comments, diff_for, _ = _fetchers(diff={"content": [{"ab": ["a", "b", "c"]}]})
    examples, drops = build_from_change("openstack", CHANGE, comments, diff_for)
    assert examples == [] and drops["no_anchored_hunk"] == 1


def test_a_comment_on_the_last_patch_set_is_dropped_before_any_fetch() -> None:
    def comments(number: int) -> dict[str, list[dict[str, Any]]]:
        return {"nova/f.py": [{"patch_set": 2, "line": 2, "message": "x"}]}

    _, diff_for, calls = _fetchers()
    examples, drops = build_from_change("openstack", CHANGE, comments, diff_for)
    assert examples == [] and drops["no_successor"] == 1
    assert calls == [], "no diff should be fetched for a dropped comment"


def test_a_comment_with_no_line_anchor_is_dropped() -> None:
    def comments(number: int) -> dict[str, list[dict[str, Any]]]:
        return {"nova/f.py": [{"patch_set": 1, "message": "file-level note"}]}

    _, diff_for, calls = _fetchers()
    examples, drops = build_from_change("openstack", CHANGE, comments, diff_for)
    assert examples == [] and drops["no_line_anchor"] == 1
    assert calls == []


def test_a_diff_failure_is_counted_not_raised() -> None:
    def failing(number: int, rev: int, path: str, base: int) -> dict[str, Any]:
        raise RuntimeError("gerrit returned 404")

    comments, _, _ = _fetchers()
    examples, drops = build_from_change("openstack", CHANGE, comments, failing)
    assert examples == [] and drops["diff_error"] == 1


def test_a_comments_failure_is_counted_not_raised() -> None:
    """Mirrors the diff-failure test: one unreachable change must not abort a month's build."""
    _, diff_for, calls = _fetchers()

    def failing_comments(number: int) -> dict[str, list[dict[str, Any]]]:
        raise RuntimeError("gerrit returned 503 after 5 attempts")

    examples, drops = build_from_change("openstack", CHANGE, failing_comments, diff_for)
    assert examples == []
    assert drops["comment_error"] == 1
    assert calls == [], "no diff is requested for a change whose comments never arrived"


def test_several_comments_on_one_hunk_make_one_example_carrying_all_of_them() -> None:
    # The spec says "the inline reviewer comments anchored inside that hunk", plural.
    # One example per comment would emit identical before/after rows that dedup then
    # discards as duplicates, silently losing every comment but the first.
    def comments(number: int) -> dict[str, list[dict[str, Any]]]:
        return {
            "nova/f.py": [
                {"patch_set": 1, "line": 2, "message": "spaces around the operator"},
                {"patch_set": 1, "line": 2, "message": "and drop the redundant parens"},
            ]
        }

    _, diff_for, calls = _fetchers()
    examples, _ = build_from_change("openstack", CHANGE, comments, diff_for)
    assert len(examples) == 1
    assert examples[0]["comments"] == [
        "spaces around the operator",
        "and drop the redundant parens",
    ]


def test_comments_on_different_hunks_stay_separate() -> None:
    diff = {
        "content": [
            {"a": ["one"], "b": ["ONE"]},
            {"ab": ["pad"]},
            {"a": ["two"], "b": ["TWO"]},
        ]
    }

    def comments(number: int) -> dict[str, list[dict[str, Any]]]:
        return {
            "nova/f.py": [
                {"patch_set": 1, "line": 1, "message": "first"},
                {"patch_set": 1, "line": 3, "message": "second"},
            ]
        }

    _, diff_for, _ = _fetchers(diff=diff)
    examples, _ = build_from_change("openstack", CHANGE, comments, diff_for)
    assert len(examples) == 2
    assert [e["comments"] for e in examples] == [["first"], ["second"]]


def test_comments_by_the_change_owner_are_not_review_comments() -> None:
    # Measured on live OpenStack data 2026-09-14: 52% of code-file comments are authored
    # by the change owner and 24% are literally "Done". They are the author acknowledging
    # a fix, not an instruction to make one, and feeding them to the model both pollutes
    # the input and leaks that the edit was applied.
    from sphragis.corpus.build import is_reviewer_comment

    owner = "abc123"
    assert is_reviewer_comment({"author": {"_account_id": "reviewer9"}}, owner) is True
    assert is_reviewer_comment({"author": {"_account_id": owner}}, owner) is False


def test_a_comment_with_no_author_is_kept_rather_than_guessed_at() -> None:
    from sphragis.corpus.build import is_reviewer_comment

    assert is_reviewer_comment({"message": "x"}, "abc123") is True


def test_owner_replies_are_dropped_and_counted() -> None:
    owner_change = {**CHANGE, "owner": {"_account_id": "owner1"}}

    def comments(number: int) -> dict[str, list[dict[str, Any]]]:
        return {
            "nova/f.py": [
                {
                    "patch_set": 1,
                    "line": 2,
                    "message": "fix spacing",
                    "author": {"_account_id": "rev1"},
                },
                {"patch_set": 1, "line": 2, "message": "Done", "author": {"_account_id": "owner1"}},
            ]
        }

    _, diff_for, _ = _fetchers()
    examples, drops = build_from_change("openstack", owner_change, comments, diff_for)
    assert len(examples) == 1
    assert examples[0]["comments"] == ["fix spacing"], "the author's ack must not survive"
    assert drops["author_comment"] == 1


def test_mixing_scrubbed_and_unscrubbed_ids_raises_instead_of_silently_matching_nothing() -> None:
    # This actually happened: the snapshot's owner was scrubbed to a 12-hex string while
    # the comments endpoint returned raw integer account ids, so the author filter
    # compared str to int, matched nothing, and looked like it was working.
    from sphragis.corpus.build import is_reviewer_comment

    with pytest.raises(TypeError, match="scrubbed"):
        is_reviewer_comment({"author": {"_account_id": 1000096}}, "0b58c157f99a")
    with pytest.raises(TypeError, match="scrubbed"):
        is_reviewer_comment({"author": {"_account_id": "0b58c157f99a"}}, 1000096)


def test_consistently_scrubbed_ids_compare_fine() -> None:
    from sphragis.corpus.build import is_reviewer_comment

    assert is_reviewer_comment({"author": {"_account_id": "aaa"}}, "bbb") is True
    assert is_reviewer_comment({"author": {"_account_id": "aaa"}}, "aaa") is False


def test_ill_posed_examples_are_dropped_and_counted_by_reason() -> None:
    # The drop profile is the Stage 1 sampling section; a silent drop would make the
    # corpus look cleaner than it is.
    def comments(number: int) -> dict[str, list[dict[str, Any]]]:
        return {"nova/f.py": [{"patch_set": 1, "line": 2, "message": "unused import"}]}

    def deletion(number: int, rev: int, path: str, base: int) -> dict[str, Any]:
        return {"content": [{"ab": ["def f(x):"]}, {"a": ["    import os"], "b": []}]}

    examples, drops = build_from_change("openstack", CHANGE, comments, deletion)
    assert examples == []
    assert drops["ill_posed_empty_after"] == 1


def test_a_well_posed_example_is_unaffected_by_the_filter() -> None:
    comments, diff_for, _ = _fetchers()
    examples, drops = build_from_change("openstack", CHANGE, comments, diff_for)
    assert len(examples) == 1
    assert all(v == 0 for k, v in drops.items() if k.startswith("ill_posed_"))


@pytest.mark.parametrize(
    "message", ["Done", "done.", " Ditto ", "+1", "LGTM!", "Thanks :)", "Fixed"]
)
def test_an_acknowledgement_is_not_an_instruction(message: str) -> None:
    from sphragis.corpus.build import is_acknowledgement

    assert is_acknowledgement(message)


@pytest.mark.parametrize(
    "message",
    ["done, but rename the variable", "Done? This still leaks the handle", "spaces around +1", ""],
)
def test_a_comment_with_anything_to_act_on_survives(message: str) -> None:
    from sphragis.corpus.build import is_acknowledgement

    assert not is_acknowledgement(message)


def test_a_hunk_whose_only_comment_is_an_acknowledgement_yields_no_example() -> None:
    """The target is unreachable from a prompt that says only "Done"."""
    only_ack = {"nova/f.py": [{"patch_set": 1, "line": 2, "message": "Done"}]}
    _, diff_for, calls = _fetchers()
    examples, drops = build_from_change("openstack", CHANGE, lambda n: only_ack, diff_for)
    assert examples == []
    assert drops["acknowledgement"] == 1
    assert calls == [], "an acknowledgement is dropped before its diff is fetched"


def test_an_acknowledgement_beside_a_real_comment_is_dropped_and_the_example_kept() -> None:
    mixed = {
        "nova/f.py": [
            {"patch_set": 1, "line": 2, "message": "spaces around the operator"},
            {"patch_set": 1, "line": 2, "message": "+1"},
        ]
    }
    comments, diff_for, _ = _fetchers()
    examples, drops = build_from_change("openstack", CHANGE, lambda n: mixed, diff_for)
    assert [e["comments"] for e in examples] == [["spaces around the operator"]]
    assert drops["acknowledgement"] == 1


def test_context_is_carried_and_changes_nothing_else() -> None:
    """Adding context must be purely additive: same examples, same targets, same drops."""
    comments, diff_for, _ = _fetchers()
    without, drops_without = build_from_change(
        "openstack", CHANGE, comments, diff_for, context_lines=0
    )
    with_ctx, drops_with = build_from_change(
        "openstack", CHANGE, comments, diff_for, context_lines=3
    )

    def strip(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{k: v for k, v in r.items() if not k.startswith("context_")} for r in rows]

    assert strip(with_ctx) == strip(without)
    assert drops_with == drops_without
    assert with_ctx[0]["context_before"] == "def f(x):"
    assert with_ctx[0]["context_after"] == "\ndef g():"
    assert without[0]["context_before"] == "" and without[0]["context_after"] == ""


def _only(comment: dict[str, Any]):
    def comments(number: int) -> dict[str, list[dict[str, Any]]]:
        return {"nova/f.py": [{"patch_set": 1, "line": 2, **comment}]}

    return comments


def test_a_bot_template_is_not_a_review_comment() -> None:
    _, diff_for, calls = _fetchers()
    examples, drops = build_from_change(
        "openstack", CHANGE, _only({"message": "Hint: Trailing whitespace"}), diff_for
    )
    assert examples == []
    assert drops["automated_comment"] == 1
    assert calls == [], "a bot's comment should cost no diff request"


def test_a_service_account_is_not_a_reviewer_whatever_it_writes() -> None:
    _, diff_for, _ = _fetchers()
    author = {"_account_id": "abc", "tags": ["SERVICE_USER"]}
    examples, drops = build_from_change(
        "openstack", CHANGE, _only({"message": "rename this", "author": author}), diff_for
    )
    assert examples == []
    assert drops["automated_comment"] == 1


def test_acknowledged_is_an_acknowledgement() -> None:
    _, diff_for, _ = _fetchers()
    examples, drops = build_from_change(
        "openstack", CHANGE, _only({"message": "Acknowledged"}), diff_for
    )
    assert examples == []
    assert drops["acknowledgement"] == 1


@pytest.mark.parametrize("kind", ["TRIVIAL_REBASE", "NO_CODE_CHANGE", "NO_CHANGE"])
def test_a_successor_that_changed_no_code_yields_no_example(kind: str) -> None:
    comments, diff_for, calls = _fetchers()
    change = {**CHANGE, "revisions": {"aaa": {"_number": 1}, "bbb": {"_number": 2, "kind": kind}}}
    examples, drops = build_from_change("openstack", change, comments, diff_for)
    assert examples == []
    assert drops["not_rework_successor"] == 1
    assert calls == []


def test_a_rework_successor_is_unaffected() -> None:
    comments, diff_for, _ = _fetchers()
    change = {
        **CHANGE,
        "revisions": {"aaa": {"_number": 1}, "bbb": {"_number": 2, "kind": "REWORK"}},
    }
    examples, drops = build_from_change("openstack", change, comments, diff_for)
    assert len(examples) == 1
    assert drops["not_rework_successor"] == 0
