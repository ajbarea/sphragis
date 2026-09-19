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
def _gap_scores(tokens: Sequence[TokenStats]) -> list[float | None]:
    """Each position's normalised distance from the model's top-1 choice, None where undefined."""
    return [
        (logprob - top1) / math.sqrt(variance)
        if variance > 0.0 and math.isfinite(logprob) and math.isfinite(top1)
        else None
        for logprob, _, variance, top1 in tokens
    ]


def gap_k_percent(tokens: Sequence[TokenStats], *, k: float = 20.0, window: int = 3) -> float:
    """Gap-K% (Kwak and Kim, arXiv:2601.19936), the mean of its lowest k percent of scores.

    Per token, the paper's g_t is the normalised distance from the model's own top-1 prediction,
    `(log p(x_t) - max_v log p(v)) / sigma_t`, which is at most zero and is nearer zero for text
    the model was trained on. Adjacent scores are smoothed over a sliding window of `window`,
    since membership shows up over contiguous stretches rather than single tokens, and the score
    is the mean of the lowest k percent of the smoothed values.

    Window 3 is the paper's default outside the LLaMA family; k follows Min-K%'s 20.

    A position whose variance is not positive has no score, as in Min-K%++. Such positions are
    held in place rather than removed, and a window covering one is skipped: compacting them out
    first would average tokens that are not adjacent in the text, which is the opposite of what
    the smoothing is for. A sequence shorter than the window yields one window over what it has,
    where k cannot select and the score is the plain mean, so the window is capped instead.

    When gaps leave no run of `window` scored positions the window shrinks until some run
    survives, down to single positions, rather than refusing: this is called once an example, in
    a comprehension over a corpus, after the GPU work that produced the statistics, and one
    unscorable example must not throw the run away. `gap_k_windows` reports how many windows the
    gaps cost, which the battery records.
    """
    if window < 1:
        raise ValueError(f"the smoothing window must be positive, got {window}")
    scores = _gap_scores(tokens)
    if not any(s is not None for s in scores):
        raise ValueError("gap_k_percent needs at least one position with a positive variance")
    smoothed, _ = _smoothed_gaps(scores, window)
    return min_k_percent(smoothed, k=k)


def _smoothed_gaps(scores: Sequence[float | None], window: int) -> tuple[list[float], int]:
    """Windows of `window` consecutive scored positions, and how many windows the gaps cost.

    The window shrinks if no run of that length exists, so a sequence riddled with unscored
    positions still yields a score rather than an exception.
    """
    for span in range(min(window, len(scores)), 0, -1):
        starts = range(len(scores) - span + 1)
        smoothed: list[float] = []
        for start in starts:
            piece = scores[start : start + span]
            if all(value is not None for value in piece):
                smoothed.append(fmean(value for value in piece if value is not None))
        if smoothed:
            return smoothed, len(starts) - len(smoothed)
    raise ValueError("gap_k_percent needs at least one position with a positive variance")


def _window_cost(sequences: Sequence[Sequence[TokenStats]], window: int) -> dict[str, int]:
    """What the smoothing costs over a whole side, not over its first example.

    This reported one example's counts under a plural name, which reads as though the corpus
    had been certified. An unscored position removes the windows that would have covered it,
    so the number that matters is how much of the side went unscored and how many examples
    lost anything at all.
    """
    costs = [gap_k_windows(seq, window=window) for seq in sequences]
    total = {
        "examples": len(costs),
        "positions": sum(c["positions"] for c in costs),
        "undefined_positions": sum(c["undefined_positions"] for c in costs),
        "windows": sum(c["windows"] for c in costs),
        "windows_dropped": sum(c["windows_dropped"] for c in costs),
    }
    total["examples_losing_a_window"] = sum(1 for c in costs if c["windows_dropped"])
    total["examples_scoring_nothing"] = sum(1 for c in costs if not c["windows"])
    return total


def gap_k_windows(tokens: Sequence[TokenStats], *, window: int = 3) -> dict[str, int]:
    """How many smoothed windows Gap-K% scored, and how many the unscored positions cost."""
    scores = _gap_scores(tokens)
    smoothed, dropped = _smoothed_gaps(scores, window)
    return {
        "positions": len(scores),
        "undefined_positions": sum(1 for s in scores if s is None),
        "windows": len(smoothed),
        "windows_dropped": dropped,
    }


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
            "windows": {
                "post": _window_cost(post_tokens, gap_window),
                "pre": _window_cost(pre_tokens, gap_window),
            },
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
