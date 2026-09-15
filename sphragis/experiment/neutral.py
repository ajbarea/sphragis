"""Outcome-neutral tests: checks on the apparatus, independent of which way RQ1 falls.

Pre-registered in design section 3. They run before the confirmatory comparison, and a
failure halts the study and is reported as an apparatus failure rather than a result. Each
check returns its evidence beside its pass flag, and `apparatus_holds` is the one place the
halt rule lives.

The contamination battery is test 1 and is deliberately absent: its report returns no
verdict, because with no published content cutoff a flat gap is ambiguous and the reading is
pre-committed in the report rather than computed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from statistics import fmean
from typing import Any

from sphragis.corpus.dedup import jaccard, pair_text, shingles
from sphragis.experiment.runner import to_clusters
from sphragis.measure.stats import cluster_bootstrap, supports_direction


@dataclass(frozen=True)
class Check:
    """One outcome-neutral test on one condition: whether it passed, and why."""

    name: str
    passed: bool
    evidence: dict[str, Any] = field(default_factory=dict)


def positive_control(
    adapted: Sequence[Mapping[str, Any]],
    base: Sequence[Mapping[str, Any]],
    *,
    label: str,
    bootstrap_seed: int,
) -> Check:
    """Test 2: an organization's own adapter beats the base model on its held-out data.

    Read with the same one-sided rule as the gate. If it fails, the training setup is broken
    and the comparison between adapters means nothing.
    """
    interval = cluster_bootstrap(to_clusters(adapted, base), seed=bootstrap_seed)
    return Check(f"positive_control:{label}", supports_direction(interval), {"interval": interval})


def manipulation_check(
    losses: Sequence[float], *, epochs: int, adapter_weight_norm: float, label: str
) -> Check:
    """Test 3: training loss decreases, and the adapter's weights differ from initialization.

    "Decreases" is the final epoch's mean step loss below the first epoch's, not the last step
    against the first: the last step holds only the examples left over after full batches
    (1 of 145 in the pilot), so its loss is noise. With one epoch, the halves are compared.
    """
    if not losses:
        return Check(f"manipulation:{label}", False, {"reason": "no optimizer steps recorded"})
    span = len(losses) // epochs if epochs > 1 else max(1, len(losses) // 2)
    first, final = fmean(losses[:span]), fmean(losses[-span:])
    moved = adapter_weight_norm > 0.0
    return Check(
        f"manipulation:{label}",
        final < first and moved,
        {
            "first_span_mean_loss": first,
            "final_span_mean_loss": final,
            "steps_per_span": span,
            "adapter_weight_norm": adapter_weight_norm,
        },
    )


def near_duplicate_rate(
    train: Sequence[Mapping[str, Any]],
    held_out: Sequence[Mapping[str, Any]],
    *,
    threshold: float = 0.8,
    k: int = 5,
) -> tuple[float, list[str]]:
    """Share of held-out examples near-duplicating any training example, and their ids.

    The same shingling and Jaccard threshold dedup uses within a window, applied across the
    boundary between two windows.
    """
    if not held_out:
        return 0.0, []
    train_signatures = [shingles(pair_text(r), k) for r in train]
    hits = [
        str(r["id"])
        for r in held_out
        if any(
            jaccard(signature, seen) >= threshold
            for signature in (shingles(pair_text(r), k),)
            for seen in train_signatures
        )
    ]
    return len(hits) / len(held_out), hits


def leakage_check(
    train: Sequence[Mapping[str, Any]],
    held_out: Sequence[Mapping[str, Any]],
    *,
    max_rate: float,
    label: str,
) -> Check:
    """Test 4: the near-duplicate rate across the train/test boundary is below `max_rate`.

    `max_rate` is fixed in the Stage 1 report; callers pass it rather than inheriting a
    default, so the threshold that ran is always the one written down.
    """
    rate, hits = near_duplicate_rate(train, held_out)
    return Check(
        f"leakage:{label}",
        rate < max_rate,
        {"rate": rate, "max_rate": max_rate, "near_duplicates": len(hits), "examples": hits[:10]},
    )


def non_degeneracy(results: Mapping[str, Sequence[Mapping[str, Any]]]) -> Check:
    """Test 5: exact match is neither 0 nor 1 for every condition on every held-out set."""
    rates = {
        run: fmean(float(r["exact_match"]) for r in rows) if rows else float("nan")
        for run, rows in results.items()
    }
    degenerate = sorted(run for run, rate in rates.items() if not 0.0 < rate < 1.0)
    return Check("non_degeneracy", not degenerate, {"exact_match": rates, "degenerate": degenerate})


def apparatus_holds(checks: Sequence[Check]) -> bool:
    """True only if every outcome-neutral check passed. False halts the study: H1 is not read."""
    return bool(checks) and all(check.passed for check in checks)
