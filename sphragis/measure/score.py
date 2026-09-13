"""The metric ladder: exact match binds the pass rule, two others are reported."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_WHITESPACE = re.compile(r"\s+")


def normalize_formatting(text: str) -> str:
    """Collapse every whitespace run to one space and trim the edges."""
    return _WHITESPACE.sub(" ", text).strip()


def exact_match(prediction: str, reference: str) -> bool:
    """Byte identity. The only metric the RQ1 pass rule reads."""
    return prediction == reference


def normalized_exact_match(prediction: str, reference: str) -> bool:
    """Exact match once formatting differences are removed."""
    return normalize_formatting(prediction) == normalize_formatting(reference)


def edit_similarity(prediction: str, reference: str) -> float:
    """Character-level similarity in [0, 1]; 1.0 when both sides are empty."""
    if not prediction and not reference:
        return 1.0
    return SequenceMatcher(a=prediction, b=reference, autojunk=False).ratio()


def score(prediction: str, reference: str) -> dict[str, float]:
    """The whole ladder for one prediction, every value a float."""
    return {
        "exact_match": float(exact_match(prediction, reference)),
        "normalized_exact_match": float(normalized_exact_match(prediction, reference)),
        "edit_similarity": edit_similarity(prediction, reference),
    }
