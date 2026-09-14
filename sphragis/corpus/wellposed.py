"""Structural filter for examples no model could answer.

Measured on the real October 2024 OpenStack corpus (275 examples): 27% are structurally
ill-posed, the largest category being pure deletions at 21.5%, whose target is the empty
string. Exact match scores those a miss for a structural reason rather than a modelling
one, so leaving them in depresses the primary metric without saying anything about the
model.

These are the misaligned pairs arXiv:2607.25851 describes. The thresholds are parameters
rather than constants because the filter is a pre-registration decision: the Stage 1
report states them and the drop counts.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

ILL_POSED_REASONS = ("empty_after", "empty_before", "expansion", "contraction")

# Beyond these the hunk pairing has captured something other than the edit under review.
# The worst real case anchored a single line against a thirty-line block, a ratio of 1148.
RATIO_MAX = 5.0
RATIO_MIN = 0.2


def classify(
    example: Mapping[str, Any], *, ratio_max: float = RATIO_MAX, ratio_min: float = RATIO_MIN
) -> str | None:
    """Why this example is ill-posed, or None when it is fine."""
    before, after = str(example["before"]), str(example["after"])
    if not after.strip():
        return "empty_after"
    if not before.strip():
        return "empty_before"
    ratio = len(after) / max(len(before), 1)
    if ratio > ratio_max:
        return "expansion"
    if ratio < ratio_min:
        return "contraction"
    return None


def is_well_posed(
    example: Mapping[str, Any], *, ratio_max: float = RATIO_MAX, ratio_min: float = RATIO_MIN
) -> bool:
    """True when a model could plausibly be expected to produce the target."""
    return classify(example, ratio_max=ratio_max, ratio_min=ratio_min) is None
