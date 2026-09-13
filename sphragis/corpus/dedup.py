"""Three-stage deduplication: exact, near-duplicate, repeated boilerplate."""

from __future__ import annotations

import hashlib
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


def _pair_text(example: Mapping[str, Any]) -> str:
    return f"{normalize(str(example['before']))}\n{normalize(str(example['after']))}"


def dedup(
    examples: Sequence[Mapping[str, Any]],
    *,
    threshold: float = 0.8,
    k: int = 5,
    boilerplate_max_docs: int = 20,
    boilerplate_fraction: float = 0.5,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Remove duplicates, near-duplicates and boilerplate; keep the earliest occurrence."""
    removed: Counter[str] = Counter({"exact": 0, "near_duplicate": 0, "boilerplate": 0})
    ordered = sorted(examples, key=lambda e: (str(e.get("created") or ""), str(e["id"])))

    by_hash: dict[str, dict[str, Any]] = {}
    for example in ordered:
        key = hashlib.sha256(_pair_text(example).encode()).hexdigest()
        if key in by_hash:
            removed["exact"] += 1
            continue
        by_hash[key] = dict(example)

    kept: list[dict[str, Any]] = []
    signatures: list[frozenset[str]] = []
    for example in by_hash.values():
        signature = shingles(_pair_text(example), k)
        if any(jaccard(signature, seen) >= threshold for seen in signatures):
            removed["near_duplicate"] += 1
            continue
        kept.append(example)
        signatures.append(signature)

    document_frequency: Counter[str] = Counter()
    for signature in signatures:
        document_frequency.update(signature)
    repeated = {s for s, n in document_frequency.items() if n > boilerplate_max_docs}

    survivors: list[dict[str, Any]] = []
    for example, signature in zip(kept, signatures, strict=True):
        share = len(signature & repeated) / len(signature) if signature else 0.0
        if share >= boilerplate_fraction:
            removed["boilerplate"] += 1
            continue
        survivors.append(example)
    return survivors, dict(removed)
