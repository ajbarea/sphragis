# Measurement Implementation Plan (Plan B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build the three measurement instruments the RQ1 gate reads: the metric ladder, the contamination battery, and the pre-registered pass rule with its interval.

**Architecture:** Three modules under `sphragis/corpus/`, each a pure function over precomputed inputs. Nothing here loads a model. `contamination.py` takes token log-probabilities and generated continuations rather than producing them, so the whole battery is testable in milliseconds and the model shell belongs to plan C.

**Tech Stack:** Python 3.12-3.14, standard library only. `random.Random` for the bootstrap, seeded.

**Spec:** `docs/superpowers/specs/2026-09-13-gerrit-review-corpus-harness-design.md` sections 2 (`score`), 3, 6.

## Global Constraints

- Python `>=3.12,<3.15`. No new runtime dependency; standard library only.
- `make lint` and `make test` pass before every commit.
- `from __future__ import annotations` and full type annotations throughout.
- **No module in this plan may import `torch`, `transformers`, or `peft`.** The measurement must be runnable and testable without a GPU. Plan C supplies the inputs.
- Every random draw takes an explicit seed. A metric that moves between runs is not a metric.
- Comments are execution help only.

## Why the purity constraint matters here

The spec's outcome-neutral tests halt the study when they fail. An instrument that can only be exercised by loading a 7B model on TIGRIS is one that will not be exercised, and a halt condition nobody can test is a halt condition that never fires. Every function here is a pure transform over data a plan C shell hands it.


> **Status: executed.** Every task below is built, tested and merged. Kept as the record of how, not as a queue.

---

## File Structure

| File | Responsibility |
|---|---|
| `sphragis/measure/score.py` | the metric ladder: exact match, normalized exact match, edit similarity |
| `sphragis/measure/contamination.py` | Min-K%++, guided completion, and the pre/post window comparison |
| `sphragis/measure/stats.py` | pairs cluster bootstrap, the effect size, and the pass rule as code |

Tests mirror the module names under `tests/unit/corpus/`.

---

### Task 1: The metric ladder

**Files:**
- Create: `sphragis/measure/score.py`
- Test: `tests/unit/measure/test_score.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `normalize_formatting(text: str) -> str`
  - `exact_match(prediction: str, reference: str) -> bool`
  - `normalized_exact_match(prediction: str, reference: str) -> bool`
  - `edit_similarity(prediction: str, reference: str) -> float` — in `[0.0, 1.0]`
  - `score(prediction: str, reference: str) -> dict[str, float]` — keys `exact_match`, `normalized_exact_match`, `edit_similarity`; the first two as `0.0`/`1.0` so every value is a float and the three aggregate the same way

- [x] **Step 1: Write the failing test**

```python
"""The metric ladder: one binding metric, two reported alongside it."""

from __future__ import annotations

from sphragis.measure.score import (
    edit_similarity,
    exact_match,
    normalize_formatting,
    normalized_exact_match,
    score,
)


def test_exact_match_is_byte_identity() -> None:
    assert exact_match("x = 1", "x = 1") is True
    assert exact_match("x = 1", "x = 1 ") is False
    assert exact_match("x=1", "x = 1") is False


def test_normalize_formatting_collapses_whitespace_and_trims_blank_edges() -> None:
    assert normalize_formatting("  x   =  1  ") == "x = 1"
    assert normalize_formatting("\n\nx = 1\n\n") == "x = 1"
    assert normalize_formatting("x =\n    1") == "x = 1"


def test_normalized_exact_match_forgives_wrapping_but_not_content() -> None:
    assert normalized_exact_match("x =\n    1", "x = 1") is True
    assert normalized_exact_match("x = 1 ", "x = 1") is True
    assert normalized_exact_match("x = 2", "x = 1") is False


def test_normalized_exact_match_does_not_forgive_identifier_style() -> None:
    assert normalized_exact_match("fooBar = 1", "foo_bar = 1") is False


def test_edit_similarity_is_bounded_and_ordered() -> None:
    assert edit_similarity("abc", "abc") == 1.0
    assert edit_similarity("", "") == 1.0
    assert edit_similarity("abc", "") == 0.0
    near = edit_similarity("return x + 1", "return x + 2")
    far = edit_similarity("return x + 1", "raise ValueError()")
    assert 0.0 < far < near < 1.0


def test_score_reports_all_three_as_floats() -> None:
    result = score("x = 1", "x = 1")
    assert result == {"exact_match": 1.0, "normalized_exact_match": 1.0, "edit_similarity": 1.0}
    partial = score("x =\n    1", "x = 1")
    assert partial["exact_match"] == 0.0
    assert partial["normalized_exact_match"] == 1.0
    assert 0.0 < partial["edit_similarity"] < 1.0
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/measure/test_score.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'sphragis.measure.score'`

- [x] **Step 3: Write minimal implementation**

```python
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
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/measure/test_score.py -q`
Expected: 6 passed

- [x] **Step 5: Lint and commit**

```bash
make lint
git add sphragis/measure/score.py tests/unit/measure/test_score.py
git commit -m "feat(corpus): the metric ladder around exact match"
```

---

### Task 2: Pairs cluster bootstrap and the pass rule

Before contamination, because the pass rule is what the whole study is pinned to and it is the thing most worth getting wrong early.

**Files:**
- Create: `sphragis/measure/stats.py`
- Test: `tests/unit/measure/test_stats.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Cluster` — frozen dataclass, `change_id: str`, `treatment: tuple[float, ...]`, `control: tuple[float, ...]`
  - `paired_difference(clusters: Sequence[Cluster]) -> float` — mean(treatment) - mean(control) over the pooled examples
  - `cluster_bootstrap(clusters: Sequence[Cluster], *, seed: int, resamples: int = 10_000, confidence: float = 0.95) -> dict[str, float]` — keys `estimate`, `low`, `high`, `resamples`
  - `excludes_zero(interval: Mapping[str, float]) -> bool`
  - `gate_verdict(per_org: Mapping[str, Mapping[str, float]]) -> str` — one of `pass`, `fail`, `mixed`

`gate_verdict` is the spec's pre-registered pass rule expressed as code, so it cannot be quietly reinterpreted once numbers exist.

- [x] **Step 1: Write the failing test**

```python
"""Pairs cluster bootstrap, and the pre-registered pass rule as code."""

from __future__ import annotations

import pytest

from sphragis.measure.stats import (
    Cluster,
    cluster_bootstrap,
    excludes_zero,
    gate_verdict,
    paired_difference,
)


def _clusters(n: int, treatment: float, control: float, size: int = 4) -> list[Cluster]:
    return [Cluster(f"I{i}", tuple([treatment] * size), tuple([control] * size)) for i in range(n)]


def test_paired_difference_pools_examples_across_clusters() -> None:
    clusters = [Cluster("I1", (1.0, 0.0), (0.0, 0.0)), Cluster("I2", (1.0,), (1.0,))]
    assert paired_difference(clusters) == pytest.approx(2 / 3 - 1 / 3)


def test_paired_difference_is_zero_when_arms_agree() -> None:
    assert paired_difference(_clusters(5, 1.0, 1.0)) == 0.0


def _mixed(n: int) -> list[Cluster]:
    """Heterogeneous clusters, so different resamples actually give different draws."""
    return [
        Cluster(f"I{i}", tuple([float(i % 2)] * (1 + i % 3)), tuple([float(i % 3 == 0)] * 2))
        for i in range(n)
    ]


def test_cluster_bootstrap_is_deterministic_for_a_seed() -> None:
    clusters = _mixed(30)
    a = cluster_bootstrap(clusters, seed=7, resamples=500)
    b = cluster_bootstrap(clusters, seed=7, resamples=500)
    assert a == b
    assert cluster_bootstrap(clusters, seed=8, resamples=500) != a


def test_a_homogeneous_sample_gives_the_same_interval_under_any_seed() -> None:
    # Not a determinism bug: if every cluster is identical, every resample is identical.
    clusters = _clusters(30, 1.0, 0.0)
    assert cluster_bootstrap(clusters, seed=7, resamples=200) == cluster_bootstrap(
        clusters, seed=8, resamples=200
    )


def test_cluster_bootstrap_interval_brackets_the_estimate() -> None:
    result = cluster_bootstrap(_clusters(40, 1.0, 0.0), seed=1, resamples=500)
    assert result["low"] <= result["estimate"] <= result["high"]
    assert result["resamples"] == 500


def test_a_clear_effect_produces_an_interval_excluding_zero() -> None:
    result = cluster_bootstrap(_clusters(40, 1.0, 0.0), seed=1, resamples=1000)
    assert excludes_zero(result) is True


def test_no_effect_produces_an_interval_containing_zero() -> None:
    result = cluster_bootstrap(_clusters(40, 1.0, 1.0), seed=1, resamples=1000)
    assert excludes_zero(result) is False


def test_resampling_is_by_cluster_not_by_example() -> None:
    # One cluster carries every positive outcome. Resampling clusters must sometimes
    # drop it entirely, so the interval reaches below the point estimate.
    clusters = [Cluster("big", tuple([1.0] * 20), tuple([0.0] * 20))]
    clusters += [Cluster(f"I{i}", (0.0,), (0.0,)) for i in range(20)]
    result = cluster_bootstrap(clusters, seed=3, resamples=1000)
    assert result["low"] < result["estimate"]


def test_cluster_bootstrap_rejects_an_empty_sample() -> None:
    with pytest.raises(ValueError, match="at least one cluster"):
        cluster_bootstrap([], seed=1)


def test_gate_verdict_passes_only_when_every_organization_excludes_zero() -> None:
    both = {"openstack": {"low": 0.01, "high": 0.2}, "qt": {"low": 0.02, "high": 0.3}}
    assert gate_verdict(both) == "pass"


def test_gate_verdict_fails_when_no_organization_excludes_zero() -> None:
    neither = {"openstack": {"low": -0.01, "high": 0.2}, "qt": {"low": -0.05, "high": 0.3}}
    assert gate_verdict(neither) == "fail"


def test_gate_verdict_is_mixed_when_exactly_one_excludes_zero() -> None:
    one = {"openstack": {"low": 0.01, "high": 0.2}, "qt": {"low": -0.05, "high": 0.3}}
    assert gate_verdict(one) == "mixed"


def test_gate_verdict_rejects_a_single_organization() -> None:
    with pytest.raises(ValueError, match="two organizations"):
        gate_verdict({"openstack": {"low": 0.01, "high": 0.2}})
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/measure/test_stats.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'sphragis.measure.stats'`

- [x] **Step 3: Write minimal implementation**

```python
"""Pairs cluster bootstrap over changes, and the RQ1 pass rule expressed as code."""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean


@dataclass(frozen=True)
class Cluster:
    """One change: its per-example outcomes under each arm, on the same test items."""

    change_id: str
    treatment: tuple[float, ...]
    control: tuple[float, ...]


def paired_difference(clusters: Sequence[Cluster]) -> float:
    """Mean treatment outcome minus mean control outcome, pooled over examples."""
    treatment = [value for cluster in clusters for value in cluster.treatment]
    control = [value for cluster in clusters for value in cluster.control]
    if not treatment or not control:
        return 0.0
    return fmean(treatment) - fmean(control)


def cluster_bootstrap(
    clusters: Sequence[Cluster],
    *,
    seed: int,
    resamples: int = 10_000,
    confidence: float = 0.95,
) -> dict[str, float]:
    """Percentile interval for the paired difference, resampling whole changes."""
    if not clusters:
        raise ValueError("cluster_bootstrap needs at least one cluster")
    rng = random.Random(seed)
    n = len(clusters)
    draws = sorted(
        paired_difference([clusters[rng.randrange(n)] for _ in range(n)]) for _ in range(resamples)
    )
    tail = (1.0 - confidence) / 2.0
    low = draws[max(0, int(tail * resamples) - 1)]
    high = draws[min(resamples - 1, int((1.0 - tail) * resamples))]
    return {
        "estimate": paired_difference(clusters),
        "low": low,
        "high": high,
        "resamples": float(resamples),
    }


def excludes_zero(interval: Mapping[str, float]) -> bool:
    """True when the whole interval sits on one side of zero."""
    return interval["low"] > 0.0 or interval["high"] < 0.0


def gate_verdict(per_org: Mapping[str, Mapping[str, float]]) -> str:
    """The pre-registered RQ1 pass rule. Fixed before the experiment; do not reinterpret."""
    if len(per_org) != 2:
        raise ValueError("the RQ1 gate is defined over exactly two organizations")
    passing = sum(1 for interval in per_org.values() if excludes_zero(interval))
    if passing == 2:
        return "pass"
    if passing == 0:
        return "fail"
    return "mixed"
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/measure/test_stats.py -q`
Expected: 13 passed

- [x] **Step 5: Lint and commit**

```bash
make lint
git add sphragis/measure/stats.py tests/unit/measure/test_stats.py
git commit -m "feat(corpus): pairs cluster bootstrap and the RQ1 pass rule"
```

---

### Task 3: The contamination battery

**Files:**
- Create: `sphragis/measure/contamination.py`
- Test: `tests/unit/measure/test_contamination.py`

**Interfaces:**
- Consumes: nothing at runtime. Plan C supplies log-probabilities and generated continuations.
- Produces:
  - `min_k_percent(logprobs: Sequence[float], *, k: float = 20.0) -> float` — mean log-probability of the least likely `k` percent of tokens
  - `guided_completion_rate(completions: Sequence[tuple[str, str]]) -> float` — fraction of `(generated, reference)` pairs that match after formatting normalization
  - `compare_windows(*, post_cutoff: Sequence[float], pre_cutoff: Sequence[float]) -> dict[str, float]` — keys `post_cutoff`, `pre_cutoff`, `gap`
  - `battery_report(*, post_logprobs, pre_logprobs, post_completions, pre_completions, corpus_starts, model_published) -> dict[str, Any]`

`battery_report` returns evidence, never a verdict. Which way the gap falls is reportable either way, which is what makes the battery outcome-neutral.

- [x] **Step 1: Write the failing test**

```python
"""The contamination battery: three methods, each a pre/post comparison."""

from __future__ import annotations

import pytest

from sphragis.measure.contamination import (
    battery_report,
    compare_windows,
    guided_completion_rate,
    min_k_percent,
)


def test_min_k_percent_averages_only_the_least_likely_tokens() -> None:
    logprobs = [-0.1, -0.2, -5.0, -6.0]
    assert min_k_percent(logprobs, k=50.0) == pytest.approx(-5.5)


def test_min_k_percent_keeps_at_least_one_token() -> None:
    assert min_k_percent([-1.0, -2.0, -3.0], k=1.0) == pytest.approx(-3.0)


def test_min_k_percent_over_memorized_text_is_higher_than_over_novel_text() -> None:
    memorized = min_k_percent([-0.01] * 10)
    novel = min_k_percent([-4.0] * 10)
    assert memorized > novel


def test_min_k_percent_rejects_an_empty_sequence() -> None:
    with pytest.raises(ValueError, match="at least one token"):
        min_k_percent([])


def test_guided_completion_rate_counts_verbatim_matches_after_normalization() -> None:
    assert guided_completion_rate([("x = 1", "x = 1"), ("a", "b")]) == 0.5
    assert guided_completion_rate([("x =  1", "x = 1")]) == 1.0
    assert guided_completion_rate([]) == 0.0


def test_compare_windows_reports_both_sides_and_the_gap() -> None:
    result = compare_windows(post_cutoff=[1.0, 2.0], pre_cutoff=[3.0, 5.0])
    assert result["post_cutoff"] == 1.5
    assert result["pre_cutoff"] == 4.0
    assert result["gap"] == pytest.approx(-2.5)


def test_battery_report_carries_evidence_and_never_a_verdict() -> None:
    report = battery_report(
        post_logprobs=[[-4.0] * 5],
        pre_logprobs=[[-0.01] * 5],
        post_completions=[("a", "b")],
        pre_completions=[("a", "a")],
        corpus_starts="2024-10-01",
        model_published="2024-09-17",
    )
    assert report["time_partition"]["corpus_starts_after_model"] is True
    assert report["min_k_percent"]["gap"] < 0
    assert report["guided_completion"]["gap"] < 0
    assert "verdict" not in report and "contaminated" not in report


def test_battery_report_flags_a_corpus_that_predates_the_model() -> None:
    report = battery_report(
        post_logprobs=[[-1.0]],
        pre_logprobs=[[-1.0]],
        post_completions=[("a", "a")],
        pre_completions=[("a", "a")],
        corpus_starts="2024-01-01",
        model_published="2024-09-17",
    )
    assert report["time_partition"]["corpus_starts_after_model"] is False
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/measure/test_contamination.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'sphragis.measure.contamination'`

- [x] **Step 3: Write minimal implementation**

```python
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
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/measure/test_contamination.py -q`
Expected: 8 passed

- [x] **Step 5: Run the whole suite and lint**

Run: `make lint && make test`
Expected: lint clean, every test passes.

- [x] **Step 6: Commit**

```bash
git add sphragis/measure/contamination.py tests/unit/measure/test_contamination.py
git commit -m "feat(corpus): contamination battery over precomputed model outputs"
```

---

### Task 4: Enforce the purity constraint

The "no GPU stack" rule in Global Constraints is worth nothing as prose. Make it a test.

**Files:**
- Create: `tests/unit/corpus/test_measurement_purity.py`

- [x] **Step 1: Parse the imports rather than trusting a grep**

Walk each measurement module's AST and assert its top-level import roots do not intersect
`{torch, transformers, peft, datasets, flwr}`.

- [x] **Step 2: Check the real import graph in a fresh interpreter**

An in-process check of `sys.modules` passes or fails on test ordering, because the rest of
the suite has already imported `flwr` and its dependencies. Run the probe with
`subprocess.run([sys.executable, "-c", ...])` from the repo root and assert it prints
nothing. This was a real failure, not a hypothetical: the in-process version passed alone
and failed under `make test`.

- [x] **Step 3: Run the whole suite**

Run: `make lint && make test`
Expected: lint clean, 89 passed.

- [x] **Step 4: Commit**

```bash
git add tests/unit/corpus/test_measurement_purity.py
git commit -m "test(corpus): enforce the measurement purity constraint"
```

---

## After this plan

Plan C supplies the inputs: it loads the base model and the adapters, runs the 3 by 2 grid over the frozen windows, and hands `stats.Cluster` objects and `contamination` sequences to the instruments built here. Plan C is the only part that needs TIGRIS.

The pilot power analysis also lives in plan C, because it needs real per-change exact-match variance, which needs a real training pass.
