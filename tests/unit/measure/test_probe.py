"""The separability probe, and the two controls that make its number mean something."""

from __future__ import annotations

import math

import pytest

from sphragis.measure.probe import (
    Document,
    accuracy_interval,
    balance,
    comment_words,
    documents,
    separability,
)


def _docs(
    label: int, changes: int, words: str, *, per_change: int = 2, offset: int = 0
) -> list[Document]:
    return [
        Document(f"c{label}-{i + offset}", label, tuple(words.split()))
        for i in range(changes)
        for _ in range(per_change)
    ]


def test_comment_words_reads_comments_not_code() -> None:
    row = {"comments": ["Please Add a TEST"], "before": "def f(): pass", "after": "x"}
    assert comment_words(row) == ("please", "add", "a", "test")


def test_comment_words_accepts_a_bare_string() -> None:
    assert comment_words({"comments": "Looks good"}) == ("looks", "good")


def test_comment_words_is_empty_when_there_is_no_comment() -> None:
    assert comment_words({"comments": []}) == ()


def test_documents_restricts_to_a_suffix() -> None:
    rows = [
        {"change_id": "I1", "path": "a/b.py", "comments": ["one"]},
        {"change_id": "I2", "path": "a/b.cpp", "comments": ["two"]},
    ]
    assert [d.change_id for d in documents(rows, 0, suffix=".py")] == ["I1"]
    assert len(documents(rows, 0, suffix=None)) == 2


def test_documents_drops_examples_with_no_reviewer_words() -> None:
    rows = [{"change_id": "I1", "path": "a.py", "comments": []}]
    assert documents(rows, 0, suffix=None) == []


def test_balance_equalises_the_labels() -> None:
    docs = _docs(0, 10, "alpha") + _docs(1, 3, "beta")
    balanced = balance(docs, seed=0)
    assert sum(1 for d in balanced if d.label == 0) == 6
    assert sum(1 for d in balanced if d.label == 1) == 6


def test_perfectly_separable_labels_are_separated() -> None:
    docs = _docs(0, 40, "alpha alpha alpha") + _docs(1, 40, "beta beta beta")
    assert separability(docs, seed=0)["accuracy"] == pytest.approx(1.0)


def test_identical_text_under_both_labels_sits_at_chance() -> None:
    """The null the probe has to be able to report."""
    docs = _docs(0, 60, "same words here") + _docs(1, 60, "same words here", offset=1000)
    assert separability(docs, seed=0)["accuracy"] == pytest.approx(0.5, abs=0.05)


def test_a_change_never_spans_the_split() -> None:
    """Splitting by example would let a change's own words predict its held-out half.

    Every change here carries a token unique to it, so an example-level split scores far
    above chance while a change-level split cannot beat it.
    """
    docs = []
    for i in range(60):
        for label in (0, 1):
            token = f"unique{label}x{i}"
            docs.extend(
                Document(f"c{label}-{i}", label, ("shared", "words", token)) for _ in range(4)
            )
    assert separability(docs, seed=0)["accuracy"] == pytest.approx(0.5, abs=0.08)


def test_too_few_documents_reports_not_a_number_rather_than_a_number() -> None:
    result = separability(_docs(0, 5, "a") + _docs(1, 5, "b"), seed=0)
    assert math.isnan(result["accuracy"])


def test_the_interval_brackets_the_estimate_and_resamples_changes() -> None:
    docs = _docs(0, 50, "alpha alpha") + _docs(1, 50, "beta beta")
    result = accuracy_interval(docs, seed=0, resamples=60)
    assert result["low"] <= result["accuracy"] <= result["high"]
    assert result["changes"] == 100.0


def test_a_null_interval_covers_chance() -> None:
    docs = _docs(0, 60, "same words here") + _docs(1, 60, "same words here", offset=1000)
    result = accuracy_interval(docs, seed=1, resamples=60)
    assert result["low"] <= 0.5 <= result["high"]


def test_folds_are_stratified_by_label() -> None:
    """Unstratified folds let the label mix wander, and ties break toward the first label.

    Measured before this was fixed: fold label fractions of 0.79, 0.38, 0.54, 0.54 and 0.25
    on a balanced sample, which moved a chance-level probe to 0.65.
    """
    import random as _random

    docs = _docs(0, 50, "same words") + _docs(1, 50, "same words", offset=500)
    balanced = balance(docs, seed=0)
    label_of = {d.change_id: d.label for d in balanced}
    rng = _random.Random(0)
    assigned: dict[str, int] = {}
    for label in (0, 1):
        for_label = sorted(c for c, lab in label_of.items() if lab == label)
        rng.shuffle(for_label)
        for i, change in enumerate(for_label):
            assigned[change] = i % 5
    for fold in range(5):
        in_fold = [label_of[c] for c, f in assigned.items() if f == fold]
        assert abs(sum(in_fold) / len(in_fold) - 0.5) <= 0.05


def test_a_change_appears_in_exactly_one_fold() -> None:
    docs = _docs(0, 50, "alpha") + _docs(1, 50, "beta")
    seen: dict[str, set[int]] = {}
    balanced = balance(docs, seed=0)
    label_of = {d.change_id: d.label for d in balanced}
    import random as _random

    rng = _random.Random(0)
    assigned: dict[str, int] = {}
    for label in (0, 1):
        for_label = sorted(c for c, lab in label_of.items() if lab == label)
        rng.shuffle(for_label)
        for i, change in enumerate(for_label):
            assigned[change] = i % 5
    for doc in balanced:
        seen.setdefault(doc.change_id, set()).add(assigned[doc.change_id])
    assert all(len(folds) == 1 for folds in seen.values())


def test_the_interval_is_not_lifted_by_resampled_copies_leaking_across_folds() -> None:
    """Resampling changes under fresh ids puts identical copies on both sides of the split.

    Each change here carries a token unique to it, so a copy appearing in training
    identifies its twin in the test fold outright. Under change-level grouping the labels
    are unlearnable and the probe sits at chance; if the bootstrap renames its draws, the
    copies leak, every resample scores far above chance, and the whole interval lifts clear
    of the point estimate it is supposed to bracket. Measured on real data before the fix:
    0.837 against [0.856, 0.874].
    """
    docs = []
    for i in range(60):
        for label in (0, 1):
            docs.extend(
                Document(f"c{label}-{i}", label, ("shared", f"tok{label}x{i}")) for _ in range(3)
            )
    result = accuracy_interval(docs, seed=0, resamples=80)
    assert result["accuracy"] == pytest.approx(0.5, abs=0.05), result
    assert result["low"] <= result["accuracy"] <= result["high"], result
