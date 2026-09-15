"""Orchestration against the model seam, and the pairing that feeds the statistics."""

from __future__ import annotations

from typing import Any

import pytest

from sphragis.experiment.runner import build_prompt, evaluate, to_clusters


class FakeGenerator:
    """Returns a canned completion per prompt, so the runner is testable with no model."""

    def __init__(self, answers: dict[str, str], default: str = "") -> None:
        self.answers = answers
        self.default = default
        self.seen: list[str] = []

    def generate(self, prompt: str) -> str:
        self.seen.append(prompt)
        for key, value in self.answers.items():
            if key in prompt:
                return value
        return self.default


def _ex(example_id: str, change_id: str, before: str, after: str) -> dict[str, Any]:
    return {
        "id": example_id,
        "change_id": change_id,
        "before": before,
        "after": after,
        "comments": ["fix it"],
    }


EXAMPLES = [_ex("a", "I1", "x=1", "x = 1"), _ex("b", "I1", "y=2", "y = 2")]


def test_build_prompt_carries_the_code_and_every_comment() -> None:
    prompt = build_prompt(_ex("a", "I1", "x=1", "x = 1"))
    assert "x=1" in prompt and "fix it" in prompt


def test_evaluate_scores_every_example_with_the_whole_ladder() -> None:
    gen = FakeGenerator({"x=1": "x = 1"}, default="nope")
    results = evaluate(gen, EXAMPLES)
    assert [r["id"] for r in results] == ["a", "b"]
    assert results[0]["exact_match"] == 1.0
    assert results[1]["exact_match"] == 0.0
    assert "normalized_exact_match" in results[0] and "edit_similarity" in results[0]
    assert len(gen.seen) == 2


def test_to_clusters_groups_by_change_and_pairs_by_example_id() -> None:
    treatment = [
        {"id": "a", "change_id": "I1", "exact_match": 1.0},
        {"id": "b", "change_id": "I1", "exact_match": 0.0},
    ]
    control = [
        {"id": "b", "change_id": "I1", "exact_match": 1.0},
        {"id": "a", "change_id": "I1", "exact_match": 0.0},
    ]
    clusters = to_clusters(treatment, control)
    assert len(clusters) == 1
    assert clusters[0].treatment == (1.0, 0.0)
    assert clusters[0].control == (0.0, 1.0)


def test_to_clusters_rejects_arms_evaluated_on_different_examples() -> None:
    treatment = [{"id": "a", "change_id": "I1", "exact_match": 1.0}]
    control = [{"id": "z", "change_id": "I1", "exact_match": 1.0}]
    with pytest.raises(ValueError, match="same examples"):
        to_clusters(treatment, control)


def test_to_clusters_separates_changes() -> None:
    treatment = [
        {"id": "a", "change_id": "I1", "exact_match": 1.0},
        {"id": "c", "change_id": "I2", "exact_match": 0.0},
    ]
    clusters = to_clusters(treatment, treatment)
    assert {c.change_id for c in clusters} == {"I1", "I2"}


def _outcome(example_id: str, change_id: object, value: float) -> dict[str, Any]:
    return {"id": example_id, "change_id": change_id, "exact_match": value}


def test_to_clusters_rejects_a_duplicated_id_that_a_set_comparison_would_accept() -> None:
    # Set-equal arms: {a, b} on both sides. Pairing on the last row per id reported a
    # difference of 0.667 where the rows supported 0.333.
    treatment = [_outcome("a", "I1", 1.0), _outcome("a", "I1", 1.0), _outcome("b", "I1", 0.0)]
    control = [_outcome("a", "I1", 0.0), _outcome("b", "I1", 0.0), _outcome("b", "I1", 1.0)]
    with pytest.raises(ValueError, match="repeats example ids"):
        to_clusters(treatment, control)


@pytest.mark.parametrize("change_id", [None, "", 5])
def test_to_clusters_rejects_a_change_id_that_would_merge_clusters(change_id: object) -> None:
    rows = [_outcome("a", change_id, 1.0), _outcome("b", change_id, 0.0)]
    with pytest.raises(ValueError, match="change id"):
        to_clusters(rows, rows)


def test_to_clusters_rejects_a_non_finite_outcome() -> None:
    treatment = [_outcome("a", "I1", float("nan"))]
    with pytest.raises(ValueError, match="non-finite"):
        to_clusters(treatment, [_outcome("a", "I1", 0.0)])
