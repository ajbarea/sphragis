"""Reviewer overlap between an evaluated half's dev window and its sibling's training data."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("overlap", ROOT / "scripts" / "reviewer_overlap.py")
assert _spec is not None and _spec.loader is not None
script = importlib.util.module_from_spec(_spec)
sys.argv = ["reviewer_overlap"]
_spec.loader.exec_module(script)


def test_a_change_s_reviewers_are_its_attention_set_minus_its_owner() -> None:
    change = {
        "owner": {"_account_id": "own"},
        "attention_set": {"1": {"account": {"_account_id": "r1"}}},
        "removed_from_attention_set": {
            "2": {"account": {"_account_id": "own"}},
            "3": {"account": {"_account_id": "r2"}},
        },
    }
    assert script.change_reviewers(change) == {"r1", "r2"}


def test_exposure_is_the_share_of_sibling_training_examples_a_reviewer_reviewed() -> None:
    def row(project: str, change: str) -> dict:
        return {"project": project, "change_id": change}

    side = {"a": 0, "b": 1}
    reviewers = {("b", "T1"): {"r1"}, ("b", "T2"): {"r9"}, ("a", "D1"): {"r1"}, ("a", "D2"): set()}
    train = [row("b", "T1"), row("b", "T2"), row("b", "T2"), row("a", "T3")]
    dev = [row("a", "D1"), row("a", "D2"), row("z", "D3")]
    out = script.overlap(train, dev, side, reviewers)
    assert out["dev_examples_in_halves"] == 2 and out["dev_examples_with_reviewers"] == 1
    assert out["dev_examples_sharing_a_sibling_reviewer"] == 1
    assert out["exposure_quantiles"]["0.5"] == 1 / 3
