"""Contamination battery: time partition, Min-K%++, and guided completion.

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

# One scored position: log p(x_t | x_<t), the mean and variance of log p(z | x_<t) for z drawn
# from the model's own next-token distribution over the full vocabulary, and the top-1
# log-probability at that position, which only Gap-K% reads.
TokenStats = tuple[float, float, float, float]


def min_k_percent(logprobs: Sequence[float], *, k: float = 20.0) -> float:
    """Mean log-probability of the least likely k percent of tokens."""
    if not logprobs:
        raise ValueError("min_k_percent needs at least one token")
    count = max(1, math.floor(len(logprobs) * k / 100.0))
    return fmean(sorted(logprobs)[:count])


def min_k_plus_plus_scores(tokens: Sequence[TokenStats]) -> tuple[list[float], int]:
    """Standardized token scores, and how many positions had no defined score.

    Min-K%++ (Zhang et al., ICLR 2025), token score (log p(x_t) - mu) / sigma, where mu and
    sigma^2 are the mean and variance of log p(z) under z ~ p(. | x_<t). Checked against
    the paper's equation 3 and the authors' reference implementation, which computes
    mu = sum(p * log p) and sigma^2 = sum(p * (log p)^2) - mu^2 over the vocabulary.

    A position whose variance is not positive has no score: a near one-hot distribution
    leaves sigma^2 at zero or, after float error, slightly negative, and the reference code
    turns that into NaN. Those positions are excluded and counted rather than allowed to
    poison the mean or silently vanish.
    """
    scores: list[float] = []
    undefined = 0
    for logprob, mean, variance, *_ in tokens:
        if variance > 0.0 and math.isfinite(logprob) and math.isfinite(mean):
            scores.append((logprob - mean) / math.sqrt(variance))
        else:
            undefined += 1
    return scores, undefined


def min_k_plus_plus(tokens: Sequence[TokenStats], *, k: float = 20.0) -> float:
    """Mean of the lowest k percent of Min-K%++ token scores. Higher suggests membership."""
    scores, _ = min_k_plus_plus_scores(tokens)
    if not scores:
        raise ValueError("min_k_plus_plus needs at least one position with a defined score")
    return min_k_percent(scores, k=k)


# One scored position for Gap-K%: log p(x_t | x_<t), the top-1 log-probability at that
# position, and the standard deviation of log p(z | x_<t) over the vocabulary.
def gap_k_percent(tokens: Sequence[TokenStats], *, k: float = 20.0, window: int = 3) -> float:
    """Gap-K% (Kwak and Kim, arXiv:2601.19936), the mean of its lowest k percent of scores.

    Per token, the paper's g_t is the normalised distance from the model's own top-1 prediction,
    `(log p(x_t) - max_v log p(v)) / sigma_t`, which is at most zero and is nearer zero for text
    the model was trained on. Adjacent scores are smoothed over a sliding window of `window`,
    since membership shows up over contiguous stretches rather than single tokens, and the score
    is the mean of the lowest k percent of the smoothed values.

    Window 3 is the paper's default outside the LLaMA family; k follows Min-K%'s 20. Positions
    whose variance is not positive have no score, as in Min-K%++, and are dropped.
    """
    if window < 1:
        raise ValueError(f"the smoothing window must be positive, got {window}")
    scores = [
        (logprob - top1) / math.sqrt(variance)
        for logprob, _, variance, top1 in tokens
        if variance > 0.0 and math.isfinite(logprob) and math.isfinite(top1)
    ]
    if not scores:
        raise ValueError("gap_k_percent needs at least one position with a positive variance")
    smoothed = [fmean(scores[t : t + window]) for t in range(max(1, len(scores) - window + 1))]
    return min_k_percent(smoothed, k=k)


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
    post_tokens: Sequence[Sequence[TokenStats]],
    pre_tokens: Sequence[Sequence[TokenStats]],
    post_completions: Sequence[tuple[str, str]],
    pre_completions: Sequence[tuple[str, str]],
    corpus_starts: str,
    model_published: str,
    k: float = 20.0,
    gap_window: int = 3,
) -> dict[str, Any]:
    """Evidence from every method. Deliberately returns no verdict.

    Min-K%++ is the registered membership method. Plain Min-K% is computed from the same token
    log-probabilities and reported beside it, because the design once described one while naming
    the other and both cost nothing extra. Gap-K% (arXiv:2601.19936) reads the same positions'
    distance from the model's own top choice, and joins them for the same reason: it is the
    later method, and a registered instrument should be read against its successor rather than
    quietly left behind.
    """

    def undefined(windows: Sequence[Sequence[TokenStats]]) -> int:
        return sum(min_k_plus_plus_scores(seq)[1] for seq in windows)

    return {
        "time_partition": {
            "corpus_starts": corpus_starts,
            "model_published": model_published,
            "corpus_starts_after_model": corpus_starts > model_published,
        },
        "min_k_plus_plus": {
            "k": k,
            "undefined_positions": {"post": undefined(post_tokens), "pre": undefined(pre_tokens)},
            **compare_windows(
                post_cutoff=[min_k_plus_plus(seq, k=k) for seq in post_tokens],
                pre_cutoff=[min_k_plus_plus(seq, k=k) for seq in pre_tokens],
            ),
        },
        "min_k_percent": {
            "k": k,
            **compare_windows(
                post_cutoff=[min_k_percent([t[0] for t in seq], k=k) for seq in post_tokens],
                pre_cutoff=[min_k_percent([t[0] for t in seq], k=k) for seq in pre_tokens],
            ),
        },
        "gap_k_percent": {
            "k": k,
            "window": gap_window,
            **compare_windows(
                post_cutoff=[gap_k_percent(seq, k=k, window=gap_window) for seq in post_tokens],
                pre_cutoff=[gap_k_percent(seq, k=k, window=gap_window) for seq in pre_tokens],
            ),
        },
        "guided_completion": {
            **compare_windows(
                post_cutoff=[guided_completion_rate(post_completions)],
                pre_cutoff=[guided_completion_rate(pre_completions)],
            ),
        },
        "limitation": (
            "Min-K%, Min-K%++ and Gap-K% detect verbatim overlap and degrade on paraphrase; a "
            "clean result bounds verbatim memorization and does not rule out paraphrased exposure."
        ),
    }
