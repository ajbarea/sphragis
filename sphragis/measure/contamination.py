"""Contamination battery: time partition, Min-K%, and guided completion.

Every method compares the post-cutoff corpus against a pre-cutoff control drawn from the
same projects, so each yields a gap rather than an absolute number. Min-K% family methods
detect verbatim overlap and degrade on paraphrase: a clean result bounds verbatim
memorization and does not rule out paraphrased exposure.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import fmean
from typing import Any

from sphragis.measure.score import normalize_formatting


def min_k_percent(logprobs: Sequence[float], *, k: float = 20.0) -> float:
    """Mean log-probability of the least likely k percent of tokens."""
    if not logprobs:
        raise ValueError("min_k_percent needs at least one token")
    count = max(1, math.floor(len(logprobs) * k / 100.0))
    return fmean(sorted(logprobs)[:count])


def guided_completion_rate(completions: Sequence[tuple[str, str]]) -> float:
    """Fraction of prefix-prompted continuations reproducing the reference verbatim."""
    if not completions:
        return 0.0
    hits = sum(
        normalize_formatting(generated) == normalize_formatting(reference)
        for generated, reference in completions
    )
    return hits / len(completions)


def compare_windows(
    *, post_cutoff: Sequence[float], pre_cutoff: Sequence[float]
) -> dict[str, float]:
    """Both window means and the gap between them; the sign is the whole point."""
    post = fmean(post_cutoff) if post_cutoff else 0.0
    pre = fmean(pre_cutoff) if pre_cutoff else 0.0
    return {"post_cutoff": post, "pre_cutoff": pre, "gap": post - pre}


def battery_report(
    *,
    post_logprobs: Sequence[Sequence[float]],
    pre_logprobs: Sequence[Sequence[float]],
    post_completions: Sequence[tuple[str, str]],
    pre_completions: Sequence[tuple[str, str]],
    corpus_starts: str,
    model_published: str,
    k: float = 20.0,
) -> dict[str, Any]:
    """Evidence from all three methods. Deliberately returns no verdict."""
    return {
        "time_partition": {
            "corpus_starts": corpus_starts,
            "model_published": model_published,
            "corpus_starts_after_model": corpus_starts > model_published,
        },
        "min_k_percent": {
            "k": k,
            **compare_windows(
                post_cutoff=[min_k_percent(seq, k=k) for seq in post_logprobs],
                pre_cutoff=[min_k_percent(seq, k=k) for seq in pre_logprobs],
            ),
        },
        "guided_completion": {
            **compare_windows(
                post_cutoff=[guided_completion_rate(post_completions)],
                pre_cutoff=[guided_completion_rate(pre_completions)],
            ),
        },
        "limitation": (
            "Min-K% detects verbatim overlap and degrades on paraphrase; a clean result "
            "bounds verbatim memorization and does not rule out paraphrased exposure."
        ),
    }
