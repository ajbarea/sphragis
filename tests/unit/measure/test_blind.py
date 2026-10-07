"""Blind baselines: separation of the battery's windows without the model."""

from __future__ import annotations

import pytest

from sphragis.measure.blind import (
    auc,
    clustered_separation,
    features,
    grouped_folds,
    latest_year,
    naive_bayes,
    out_of_fold,
)


def test_auc_counts_ties_as_half_and_reads_direction() -> None:
    assert auc([1.0, 2.0, 3.0, 4.0], [0, 0, 1, 1]) == 1.0
    assert auc([4.0, 3.0, 2.0, 1.0], [0, 0, 1, 1]) == 0.0
    assert auc([1.0, 1.0, 1.0, 1.0], [0, 1, 0, 1]) == 0.5
    assert auc([1.0, 2.0, 2.0, 3.0], [0, 0, 1, 1]) == pytest.approx(0.875)


def test_auc_refuses_one_label() -> None:
    with pytest.raises(ValueError):
        auc([1.0, 2.0], [1, 1])


def test_latest_year_reads_the_latest_plausible_year() -> None:
    assert latest_year("Copyright 2019-2025 Foo; port 8080, id 12345") == 2025
    assert latest_year("no dates, version 3.11") == 0
    assert latest_year("20241") == 0


def test_features_are_lowercased_words_and_adjacent_pairs() -> None:
    assert features("Foo bar_1 = 2") == {"foo", "bar_1", "2", "foo bar_1", "bar_1 2"}


def test_grouped_folds_keep_a_group_together_and_use_every_fold() -> None:
    groups = ["a", "a", "b", "c", "c", "d", "e"]
    fold = grouped_folds(groups, 3, seed=1)
    assert fold[0] == fold[1] and fold[3] == fold[4]
    assert set(fold) == {0, 1, 2}
    assert fold == grouped_folds(groups, 3, seed=1)
    with pytest.raises(ValueError):
        grouped_folds(["a", "b"], 3, seed=1)


def test_naive_bayes_leans_toward_the_label_a_feature_marks() -> None:
    train = [{"old"}, {"old", "x"}, {"new"}, {"new", "x"}]
    scores = naive_bayes(train, [0, 0, 1, 1], [{"new"}, {"old"}, {"x"}, {"unseen"}])
    assert scores[0] > 0 > scores[1]
    assert scores[2] == pytest.approx(0.0) and scores[3] == pytest.approx(0.0)


def test_out_of_fold_separates_a_marked_shift_and_not_noise() -> None:
    texts = [f"common token {'release2025' if i % 2 else 'release2023'} {i}" for i in range(80)]
    labels = [i % 2 for i in range(80)]
    groups = [f"g{i // 2}{i % 2}" for i in range(80)]
    assert auc(out_of_fold(texts, labels, groups, folds=5, seed=3), labels) == 1.0
    noise = [f"common token {i}" for i in range(80)]
    assert auc(out_of_fold(noise, labels, groups, folds=5, seed=3), labels) < 0.75


def test_out_of_fold_never_scores_an_example_with_its_own_group() -> None:
    """A group's unique word must not help it: unseen in training, it adds nothing."""
    texts = [f"shared w{i // 2}" for i in range(40)]
    labels = [(i // 2) % 2 for i in range(40)]
    groups = [f"g{i // 2}" for i in range(40)]
    scores = out_of_fold(texts, labels, groups, folds=4, seed=0)
    fold = grouped_folds(groups, 4, seed=0)
    for held in range(4):
        assert len({round(s, 12) for s, f in zip(scores, fold, strict=True) if f == held}) == 1


def test_clustered_separation_pairs_the_arms_on_one_resample() -> None:
    labels = [i % 2 for i in range(60)]
    groups = [f"g{i // 2}" for i in range(60)]
    perfect = [float(label) for label in labels]
    result = clustered_separation(
        {"a": perfect, "b": perfect}, labels, groups, seed=1, resamples=200
    )
    assert result["a"]["estimate"] == 1.0 == result["a"]["low"]
    assert result["a - b"] == {"estimate": 0.0, "low": 0.0, "high": 0.0}
