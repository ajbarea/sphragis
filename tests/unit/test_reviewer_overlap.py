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


def _row(project: str, change: str) -> dict:
    return {"project": project, "change_id": change}


def test_reviewers_are_pseudonyms_never_raw_keys_or_reason_text() -> None:
    change = {
        "owner": {"_account_id": "own"},
        "attention_set": {
            "12345": {"account": {"_account_id": "r1"}, "reason": "<GERRIT_ACCOUNT_9>"}
        },
        "removed_from_attention_set": {
            "2": {"account": {"_account_id": "own"}},
            "3": {"account": {"_account_id": "r2"}},
        },
    }
    assert script.change_reviewers(change) == {"r1", "r2"}


def test_exposure_shared_reviewers_and_skips_in_both_directions() -> None:
    side = {"a": 0, "b": 1}
    reviewers = {
        ("b", "T1"): {"r1", "r2"},
        ("b", "T2"): {"r9"},
        ("a", "T3"): {"r5"},
        ("a", "D1"): {"r1", "r2"},
        ("a", "D2"): set(),
        ("b", "D4"): {"r5", "r7"},
    }
    train = [_row("b", "T1"), _row("b", "T2"), _row("b", "T2"), _row("a", "T3")]
    dev = [_row("a", "D1"), _row("a", "D2"), _row("z", "D3"), _row("b", "D4")]
    out = script.overlap(train, dev, side, reviewers)
    assert out["dev_examples_in_halves"] == 3 and out["dev_examples_without_a_half"] == 1
    assert out["dev_examples_with_reviewers"] == 2
    assert out["dev_examples_sharing_a_sibling_reviewer"] == 2
    assert out["dev_examples_sharing_two_sibling_reviewers"] == 1, "D4 shares only r5"
    assert out["exposure_quantiles"]["0.1"] == 1 / 3 and out["exposure_quantiles"]["0.9"] == 1.0
    assert out["reviewers_per_half"] == [1, 3] and out["reviewers_in_both_halves"] == 0


def test_an_empty_sibling_half_has_no_exposure() -> None:
    out = script.overlap([], [_row("a", "D1")], {"a": 0}, {("a", "D1"): {"r1"}})
    assert out["dev_examples_sharing_a_sibling_reviewer"] == 0
    assert out["exposure_quantiles"] == {str(q): 0.0 for q in script.QUANTILES}


def test_quantiles_are_nearest_rank() -> None:
    assert script.quantiles([4.0, 1.0, 3.0, 2.0])["0.5"] == 3.0
    assert script.quantiles([]) == {}
