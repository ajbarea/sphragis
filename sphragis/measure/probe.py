"""Is a label decodable from review comments, once content is controlled for?

The generative contrast asks whether an adapter trained on one organization does better on
that organization's refinements. That is a question about behaviour, and it answers weakly:
a small advantage inside a large task-adaptation effect. This asks the prior question
directly. If a classifier cannot tell the organizations apart from their review text, there
is no organizational signal for an adapter to have failed to use.

Two things make the answer mean something.

**Content control.** OpenStack's examples are 47% Python and Qt's are 49% C++, so a
classifier reading file extensions scores in the nineties and has learned nothing about
organizations. Conditions are restricted to one file suffix, present in both sides.

**A within-label baseline.** Accuracy alone is uninterpretable: two projects inside one
organization are also separable, because they are different codebases. The baseline is what
"different codebase, same organization" looks like, and a cross-organization probe has to
beat it before it says anything about organizations. Liu et al. (arXiv:2606.07103, 2026)
show style classifiers routinely ride content cues and recommend exactly this kind of
controlled comparison.

Deliberately a bag-of-words naive Bayes rather than a learned encoder: it is deterministic,
has no hyperparameters to tune into a result, runs on a CPU in seconds, and is the weakest
instrument that could show the effect. A null from a weak instrument is weaker evidence than
a null from a strong one, which is stated with the result rather than hidden.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

TOKEN = re.compile(r"[a-z']+")


@dataclass(frozen=True)
class Document:
    """One example's reviewer text, carrying the change it belongs to and its label."""

    change_id: str
    label: int
    words: tuple[str, ...]


def comment_words(row: Mapping[str, object]) -> tuple[str, ...]:
    """The reviewer's words. Never the code, which is the content shortcut itself."""
    raw = row.get("comments")
    if isinstance(raw, str):
        parts: list[str] = [raw]
    elif isinstance(raw, Sequence):
        parts = [str(c) for c in raw]
    else:
        parts = []
    return tuple(TOKEN.findall(" ".join(parts).lower()))


# Shapes rather than identifiers: an identifier names the project's own domain, so a probe over
# identifiers separates projects by their subject matter whatever their conventions. These are the
# conventions themselves, the kind Xu et al. (arXiv:2506.12014) measure drifting.
_SHAPES = (
    ("snake", re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")),
    ("camel", re.compile(r"\b[a-z][a-z0-9]*[A-Z][A-Za-z0-9]*\b")),
    ("pascal", re.compile(r"\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*\b")),
    ("upper", re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")),
    ("dunder", re.compile(r"__[a-z]+__")),
    ("arrow", re.compile(r"->")),
    ("scope", re.compile(r"::")),
    ("fstring", re.compile(r"f\"")),
    ("brace", re.compile(r"\{")),
    ("semicolon", re.compile(r";\s*$", re.MULTILINE)),
)


def code_shapes(row: Mapping[str, object]) -> tuple[str, ...]:
    """The refinement's code, read as convention shapes rather than words.

    Each occurrence of a shape contributes one token, so a naive Bayes over these reads how the
    code is written rather than what it is about: this is the medium Xu et al. measure drifting,
    and the one `comment_words` deliberately never touches.
    """
    text = str(row.get("after", ""))
    found: list[str] = []
    for name, pattern in _SHAPES:
        found.extend([name] * len(pattern.findall(text)))
    return tuple(found)


def documents(
    rows: Sequence[Mapping[str, object]],
    label: int,
    *,
    suffix: str | None,
    words_of: Any = comment_words,
) -> list[Document]:
    """Label one side, optionally restricted to a single file suffix."""
    out = []
    for row in rows:
        path = str(row.get("path", ""))
        if suffix is not None and not path.endswith(suffix):
            continue
        words = words_of(row)
        if words:
            out.append(Document(str(row["change_id"]), label, words))
    return out


def _fit(train: Sequence[Document]) -> tuple[dict[int, dict[str, int]], dict[int, int], int]:
    counts: dict[int, dict[str, int]] = {0: {}, 1: {}}
    totals = {0: 0, 1: 0}
    vocab: set[str] = set()
    for doc in train:
        bucket = counts[doc.label]
        for word in doc.words:
            bucket[word] = bucket.get(word, 0) + 1
        totals[doc.label] += len(doc.words)
        vocab.update(doc.words)
    return counts, totals, len(vocab)


def _predict(doc: Document, counts, totals, vocab: int) -> int:
    best, best_score = 0, -math.inf
    for label in (0, 1):
        bucket, total = counts[label], totals[label]
        score = sum(math.log((bucket.get(w, 0) + 1) / (total + vocab)) for w in doc.words)
        if score > best_score:
            best, best_score = label, score
    return best


def balance(docs: Sequence[Document], *, seed: int) -> list[Document]:
    """Equal documents per label, so accuracy is comparable to a 0.5 chance line."""
    rng = random.Random(seed)
    by_label = {0: [d for d in docs if d.label == 0], 1: [d for d in docs if d.label == 1]}
    n = min(len(by_label[0]), len(by_label[1]))
    return rng.sample(by_label[0], n) + rng.sample(by_label[1], n)


def separability(
    docs: Sequence[Document], *, seed: int, folds: int = 5, min_per_label: int = 40
) -> dict[str, float]:
    """Cross-validated accuracy, with whole changes held out together.

    Splitting by example would leak: one change contributes several examples that share a
    review conversation, so the same words appear on both sides of the split and accuracy
    rises for a reason that has nothing to do with the label. This is the same grouping the
    generative contrast uses, for the same reason.
    """
    docs = balance(docs, seed=seed)
    if min(sum(1 for d in docs if d.label == label) for label in (0, 1)) < min_per_label:
        return {"accuracy": float("nan"), "documents": float(len(docs)), "changes": 0.0}

    # Folds are stratified by label. Assigning shuffled changes round-robin leaves each
    # fold's label mix to chance -- measured at 0.25 to 0.79 on a balanced sample of 120
    # changes -- and since ties are broken toward the first label, an uneven fold moves the
    # accuracy for a reason that has nothing to do with the text.
    label_of = {d.change_id: d.label for d in docs}
    rng = random.Random(seed)
    assigned: dict[str, int] = {}
    for label in (0, 1):
        changes_for_label = sorted(c for c, lab in label_of.items() if lab == label)
        rng.shuffle(changes_for_label)
        for i, change in enumerate(changes_for_label):
            assigned[change] = i % folds
    changes = sorted(assigned)

    # Balanced accuracy: the mean of the two per-label recalls, not the raw hit rate.
    # Stratifying by change equalises how many CHANGES of each label land in a fold, not
    # how many documents, and changes carry between one and forty-five. A fold whose
    # documents run 80% one label rewards a classifier that simply leans that way, which is
    # how a chance-level probe came back at 0.55 on a bootstrap resample. Balanced accuracy
    # puts the chance line at 0.5 whatever the fold's composition.
    # Balanced accuracy, averaged ACROSS folds rather than pooled over them. Pooling the
    # hits first lets folds that lean toward opposite labels cancel into apparent skill: a
    # fold predicting everything label 0 and another predicting everything label 1 each
    # score 0.5 alone, but their pooled recalls average above it. That is how a probe with
    # no learnable signal returned 0.55 on bootstrap resamples.
    scores = []
    for fold in range(folds):
        train = [d for d in docs if assigned[d.change_id] != fold]
        test = [d for d in docs if assigned[d.change_id] == fold]
        if not train or not test:
            continue
        counts, totals, vocab = _fit(train)
        hits = {0: 0, 1: 0}
        seen = {0: 0, 1: 0}
        for doc in test:
            hits[doc.label] += _predict(doc, counts, totals, vocab) == doc.label
            seen[doc.label] += 1
        if seen[0] and seen[1]:
            scores.append((hits[0] / seen[0] + hits[1] / seen[1]) / 2)
    if not scores:
        return {"accuracy": float("nan"), "documents": float(len(docs)), "changes": 0.0}
    return {
        "accuracy": sum(scores) / len(scores),
        "folds_scored": float(len(scores)),
        "documents": float(len(docs)),
        "changes": float(len(changes)),
    }


def accuracy_interval(
    docs: Sequence[Document], *, seed: int, resamples: int = 400, folds: int = 5
) -> dict[str, float]:
    """Percentile interval for the accuracy, resampling whole changes.

    The same clustered resampling the gate uses. An interval built over examples would be
    too narrow by the amount the within-change correlation contributes.
    """
    point = separability(docs, seed=seed, folds=folds)
    by_change: dict[str, list[Document]] = {}
    for doc in docs:
        by_change.setdefault(doc.change_id, []).append(doc)
    ids = sorted(by_change)
    rng = random.Random(seed)

    draws = []
    for trial in range(resamples):
        picked: list[Document] = []
        for _ in range(len(ids)):
            source = ids[rng.randrange(len(ids))]
            # Keep the original change id. Renaming a twice-drawn change into two clusters
            # is right for a difference of means and wrong here: the copies carry identical
            # text, so they land in different folds and the classifier trains on the very
            # documents it is scored on. That inflated the interval until it no longer
            # contained the estimate it was built around (0.837 against [0.856, 0.874]).
            picked.extend(by_change[source])
        drawn = separability(picked, seed=seed + trial, folds=folds)
        if not math.isnan(drawn["accuracy"]):
            draws.append(drawn["accuracy"])
    draws.sort()
    if not draws:
        return {**point, "low": float("nan"), "high": float("nan")}
    lo = draws[max(0, round(0.025 * len(draws)) - 1)]
    hi = draws[min(len(draws) - 1, round(0.975 * len(draws)))]
    return {**point, "low": lo, "high": hi}
