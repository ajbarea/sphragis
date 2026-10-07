"""Blind baselines for the contamination battery: how far the windows separate without a model.

The battery compares a pre-cutoff window with a post-cutoff one, and the two differ in time as
well as in exposure. A membership score that separates them may be reading the drift. Das,
Zhang and Tramèr (arXiv:2406.16201) show that classifiers which never look at the model, a bag
of words and the latest date in the text, beat published membership attacks on such splits.
This module computes those blind separations on the same examples the membership scores were
computed on, so a gap can be read against them.

Separation is an AUC with a fixed direction: the probability that a post-cutoff example scores
as more post-like than a pre-cutoff one. A membership score is member-like when high, so its
post-likeness is its negation.
"""

from __future__ import annotations

import math
import random
import re
from collections import Counter
from collections.abc import Sequence

from sphragis.measure.stats import percentile_interval

WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
YEAR = re.compile(r"(?<!\d)(?:19[89]\d|20[0-4]\d)(?!\d)")


def features(text: str) -> set[str]:
    """Lower-cased word and number tokens and their adjacent pairs, present or absent."""
    words = [w.lower() for w in WORD.findall(text)]
    return set(words) | {f"{a} {b}" for a, b in zip(words, words[1:], strict=False)}


def latest_year(text: str) -> int:
    """The latest year from 1980 to 2049 written in the text, or 0 when none is."""
    return max((int(y) for y in YEAR.findall(text)), default=0)


def grouped_folds(groups: Sequence[str], folds: int, seed: int) -> list[int]:
    """A fold per example, every example of one group in the same fold."""
    if folds < 2:
        raise ValueError("grouped_folds needs at least two folds")
    unique = sorted(set(groups))
    if len(unique) < folds:
        raise ValueError(f"{len(unique)} groups cannot fill {folds} folds")
    random.Random(seed).shuffle(unique)
    fold_of = {group: i % folds for i, group in enumerate(unique)}
    return [fold_of[group] for group in groups]


def naive_bayes(
    train: Sequence[set[str]], labels: Sequence[int], test: Sequence[set[str]]
) -> list[float]:
    """Log-odds of label 1 for each test document, multinomial naive Bayes on presence counts.

    Laplace smoothing over the training vocabulary; features unseen in training are ignored.
    """
    if set(labels) != {0, 1}:
        raise ValueError("naive_bayes needs both labels in the training documents")
    counts = (Counter(), Counter())
    for doc, label in zip(train, labels, strict=True):
        counts[label].update(doc)
    vocabulary = set(counts[0]) | set(counts[1])
    totals = [sum(c.values()) + len(vocabulary) for c in counts]
    prior = math.log(sum(labels) / (len(labels) - sum(labels)))
    scores = []
    for doc in test:
        score = prior
        for feature in doc & vocabulary:
            score += math.log((counts[1][feature] + 1) / totals[1])
            score -= math.log((counts[0][feature] + 1) / totals[0])
        scores.append(score)
    return scores


def out_of_fold(
    texts: Sequence[str], labels: Sequence[int], groups: Sequence[str], *, folds: int, seed: int
) -> list[float]:
    """Each example's bag-of-words log-odds from a classifier that never saw its group."""
    docs = [features(text) for text in texts]
    fold = grouped_folds(groups, folds, seed)
    scores = [0.0] * len(docs)
    for held in range(folds):
        train = [i for i, f in enumerate(fold) if f != held]
        test = [i for i, f in enumerate(fold) if f == held]
        predicted = naive_bayes(
            [docs[i] for i in train], [labels[i] for i in train], [docs[i] for i in test]
        )
        for i, score in zip(test, predicted, strict=True):
            scores[i] = score
    return scores


def auc(scores: Sequence[float], labels: Sequence[int]) -> float:
    """Probability that a label-1 example outscores a label-0 one, ties counted half."""
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    positives = sum(labels)
    negatives = len(labels) - positives
    if not positives or not negatives:
        raise ValueError("auc needs both labels")
    rank_sum = sum(r for r, label in zip(ranks, labels, strict=True) if label)
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def clustered_separation(
    arms: dict[str, Sequence[float]],
    labels: Sequence[int],
    groups: Sequence[str],
    *,
    seed: int,
    resamples: int = 2_000,
    confidence: float = 0.95,
) -> dict[str, dict[str, float]]:
    """Each arm's AUC, and every pair's difference, with intervals resampling whole groups.

    One resample of groups serves every arm, so a difference's interval is paired.
    """
    names = list(arms)
    by_group: dict[str, list[int]] = {}
    for i, group in enumerate(groups):
        by_group.setdefault(group, []).append(i)
    keys = sorted(by_group)
    rng = random.Random(seed)
    draws: dict[str, list[float]] = {name: [] for name in names}
    for _ in range(resamples):
        rows = [i for _ in keys for i in by_group[keys[rng.randrange(len(keys))]]]
        sample = [labels[i] for i in rows]
        for name in names:
            draws[name].append(auc([arms[name][i] for i in rows], sample))
    out: dict[str, dict[str, float]] = {}
    for name in names:
        low, high = percentile_interval(draws[name], confidence)
        out[name] = {"estimate": auc(arms[name], labels), "low": low, "high": high}
    for a in names:
        for b in names:
            if a < b:
                diffs = [x - y for x, y in zip(draws[a], draws[b], strict=True)]
                low, high = percentile_interval(diffs, confidence)
                estimate = out[a]["estimate"] - out[b]["estimate"]
                out[f"{a} - {b}"] = {"estimate": estimate, "low": low, "high": high}
    return out
