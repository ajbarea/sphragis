"""Three-stage deduplication: exact, near-duplicate, repeated boilerplate."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Collapse whitespace; identifier style is left alone because it is the signal."""
    return _WHITESPACE.sub(" ", text).strip()


def shingles(text: str, k: int = 5) -> frozenset[str]:
    """Character k-grams of the normalized text."""
    normalized = normalize(text)
    if len(normalized) <= k:
        return frozenset({normalized}) if normalized else frozenset()
    return frozenset(normalized[i : i + k] for i in range(len(normalized) - k + 1))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity, defined as 0.0 when both sides are empty."""
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def boilerplate_threshold(n_documents: int, *, fraction: float, floor: int) -> int:
    """Document-frequency cutoff above which a shingle counts as boilerplate.

    Separate and public because the realized value depends on corpus size, and the Stage 1
    report has to state the number that actually ran rather than the fraction it was
    derived from.
    """
    return max(floor, math.ceil(fraction * n_documents))


def pair_text(example: Mapping[str, Any]) -> str:
    """The normalized before/after pair every duplicate comparison is made over."""
    return f"{normalize(str(example['before']))}\n{normalize(str(example['after']))}"


def dedup(
    examples: Sequence[Mapping[str, Any]],
    *,
    threshold: float = 0.8,
    k: int = 5,
    boilerplate_document_fraction: float = 0.10,
    boilerplate_min_docs: int = 5,
    boilerplate_fraction: float = 0.5,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Remove duplicates, near-duplicates and boilerplate; keep the earliest occurrence.

    The boilerplate threshold is a FRACTION of the surviving corpus, not a document count.
    An absolute count silently tightens as the corpus grows: 20 documents is 11.5% document
    frequency at the pilot month's 174 examples and 0.4% at 5,000, where almost every
    common 5-gram clears it and the stage stops removing boilerplate and starts removing
    the corpus. A swept count on the pilot data confirms the cliff is close -- 20, 5 and 3
    documents remove 0, 0 and 6, and 1 removes 54 of 174.

    The floor keeps a small corpus from filtering itself: at 20 examples a 10% threshold
    would be 2 documents, which is inside the destructive band.
    """
    removed: Counter[str] = Counter({"exact": 0, "near_duplicate": 0, "boilerplate": 0})
    ordered = sorted(examples, key=lambda e: (str(e.get("created") or ""), str(e["id"])))

    by_hash: dict[str, dict[str, Any]] = {}
    for example in ordered:
        key = hashlib.sha256(pair_text(example).encode()).hexdigest()
        if key in by_hash:
            removed["exact"] += 1
            continue
        by_hash[key] = dict(example)

    kept: list[dict[str, Any]] = []
    signatures: list[frozenset[str]] = []
    for example in by_hash.values():
        signature = shingles(pair_text(example), k)
        if any(jaccard(signature, seen) >= threshold for seen in signatures):
            removed["near_duplicate"] += 1
            continue
        kept.append(example)
        signatures.append(signature)

    document_frequency: Counter[str] = Counter()
    for signature in signatures:
        document_frequency.update(signature)
    max_docs = boilerplate_threshold(
        len(kept), fraction=boilerplate_document_fraction, floor=boilerplate_min_docs
    )
    repeated = {s for s, n in document_frequency.items() if n > max_docs}

    survivors: list[dict[str, Any]] = []
    for example, signature in zip(kept, signatures, strict=True):
        share = len(signature & repeated) / len(signature) if signature else 0.0
        if share >= boilerplate_fraction:
            removed["boilerplate"] += 1
            continue
        survivors.append(example)
    return survivors, dict(removed)
