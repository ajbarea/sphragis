"""The log-probability an adapter assigns to a held-out reference refinement.

Greedy exact match credits a convention only where an adapter adopts it as its most likely
output, so a convention it learned at half the rate is invisible to it (research log,
2026-10-09, check 5). The log-probability of the reference is a strictly proper score: it
rewards an adapter for every bit of probability it puts on what the reviewer's revision did, so
a convention appears in proportion to how often it was learned, and no decoder sits between the
adapter and the measurement.

The reference is tokenized exactly as training tokenized it (`build_supervised`: the chat-
rendered prompt, then the target and its end token), so the score is the training objective
evaluated on held-out data; `model.reference_logprob` runs the forward pass.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from sphragis.experiment.decomposition import SESOI
from sphragis.experiment.training import IGNORE_INDEX

#: Where the per-token log-probability's SESOI is registered (`likelihood_sesoi.py`).
LIKELIHOOD_SESOI = Path("datasets/results/likelihood-sesoi.json")


def metric_sesoi(metric: str) -> float | None:
    """A metric's registered SESOI, or None where none is registered.

    Exact match's is the cost-benefit constant; the per-token log-probability's is carried from it
    by the positive control. Total log-probability, a sensitivity, has none.
    """
    if metric == "exact_match":
        return SESOI
    if metric == "logprob_per_token" and LIKELIHOOD_SESOI.is_file():
        return float(json.loads(LIKELIHOOD_SESOI.read_text())["sesoi"])
    return None


#: Wide enough that no held-out example is refused for length: scoring reads one forward pass,
#: so the training budget, which exists to keep a batch in memory, does not apply.
SCORING_MAX_LENGTH = 32_768


def target_span(labels: Sequence[int]) -> tuple[int, list[int]]:
    """Where the supervised target starts, and its tokens.

    Every label before the target is masked and every one from it on is supervised; anything
    else is not an item `build_supervised` makes, and is refused rather than scored.
    """
    start = next((i for i, label in enumerate(labels) if label != IGNORE_INDEX), None)
    if start is None or start == 0:
        raise ValueError("an item needs a masked prompt followed by a supervised target")
    target = list(labels[start:])
    if any(label == IGNORE_INDEX for label in target):
        raise ValueError("the supervised target is interrupted by masked labels")
    return start, target
