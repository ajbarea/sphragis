"""The decision-model rater asks the rubric's two questions in the rubric's own words."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "label_audit_decision", ROOT / "scripts" / "label_audit_decision.py"
)
assert _spec is not None and _spec.loader is not None
decision = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(decision)

RUBRIC = (ROOT / "scripts" / "label_audit" / "rubric.md").read_text()


def test_the_label_is_a_choice_over_the_five_labels_in_the_rubrics_words() -> None:
    label = decision.questions(RUBRIC)["label"]
    assert label["type"] == "choice"
    assert list(label["criteria"]) == list(decision.LABELS)
    assert label["criteria"]["valid"].startswith("the comment asks for a change")
    assert "QUESTION 1:" in label["instructions"] and "QUESTION 2:" not in label["instructions"]
    assert "Worked example." in label["instructions"]


def test_outside_names_is_a_yes_no_question_on_its_own_section() -> None:
    outside = decision.questions(RUBRIC)["outside_names"]
    assert outside["type"] == "noul"
    assert "QUESTION 2:" in outside["instructions"]
    assert "QUESTION 1:" not in outside["instructions"]


def test_a_rubric_missing_a_label_is_refused() -> None:
    with pytest.raises(SystemExit, match="not the labels"):
        decision.questions(RUBRIC.replace("- partial:", "- halfway:"))
