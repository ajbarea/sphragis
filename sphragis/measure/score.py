"""The metric ladder: exact match binds the pass rule, two others are reported."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_WHITESPACE = re.compile(r"\s+")
_FENCE = re.compile(r"```(?:[a-zA-Z0-9_+.-]*)\n(.*?)```", re.DOTALL)


def normalize_formatting(text: str) -> str:
    """Collapse every whitespace run to one space and trim the edges."""
    return _WHITESPACE.sub(" ", text).strip()


def extract_code(text: str) -> str:
    """The code a chat model actually emitted, without its prose or fences.

    Measured 2026-09-14 on Qwen2.5-Coder-1.5B: the model answered three refinement
    prompts correctly and raw exact match scored one of three, because it prefixed
    "Here is the revised code:" and wrapped the answer in a markdown fence. Without this
    step exact match measures output formatting rather than whether the edit was right,
    which is the metric-artifact criticism the ladder exists to defend against.

    Extraction is part of the pre-registered metric definition, and `score` reports the
    unextracted value alongside so its effect stays auditable.
    """
    match = _FENCE.search(text)
    return (match.group(1) if match else text).rstrip("\n")


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
    """The whole ladder for one prediction, every value a float.

    Every metric scores the *extracted* code. ``exact_match_raw`` reports what the
    unextracted output would have scored, so the extraction step never hides behind the
    number it improves.
    """
    code = extract_code(prediction)
    return {
        "exact_match": float(exact_match(code, reference)),
        "exact_match_raw": float(exact_match(prediction, reference)),
        "normalized_exact_match": float(normalized_exact_match(code, reference)),
        "edit_similarity": edit_similarity(code, reference),
    }
