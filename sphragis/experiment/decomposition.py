"""The granularity decomposition: at what boundary does learned house style transfer.

Each organization is split into two halves of its own projects (`scripts/placebo_corpus.py`)
and every adapter is trained on one half. On a refinement from half `a` of organization `O`:

- the half-split contrast (H1) is own half against sibling half, `EM(O-a) - EM(O-b)`;
- the organization beyond the evaluated projects (H2) is sibling half against the mean of a
  foreign organization's halves, `EM(O-b) - mean EM(P-x)`. Neither adapter has seen the
  evaluated projects.

Each contrast weights the two halves equally and resamples changes within each half. Which
cells are confirmatory is registered here, not passed in. Design of record:
`docs/superpowers/specs/2026-09-22-granularity-redesign.md`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import fmean
from typing import Any

from sphragis.experiment.grid import EvalRun, run_id
from sphragis.experiment.runner import require_unique_ids, to_clusters
from sphragis.experiment.walk import _require_registered_seeds
from sphragis.measure.stats import (
    Cluster,
    Estimator,
    paired_difference,
    percentile_interval,
    stratified_crossed_draws,
)

# Smallest effect of interest, in exact match. About 4% of the pilot's adaptation gain and the
# size of the seed-effect upper bound. Fixed in advance; never derived from a contrast.
SESOI = 0.01

# One-sided family-wise level across the confirmatory hypotheses, held by Holm's step-down.
FAMILY_ALPHA = 0.025

# The summed contrast is reported, never tested, so it reads the family's own level.
SUMMED_CONFIDENCE = 0.95

# Below this the percentile ranks of neighbouring Holm levels can coincide.
MIN_RESAMPLES = 1_000

# The confirmatory cells, by design. H1 cells are organizations; H2 cells are (evaluated,
# foreign) pairs, confirmatory only where both write the same language. `without_chromium` is
# the registered fallback if Chromium's split does not qualify by 2026-10-23.
DESIGNS: Mapping[str, Mapping[str, tuple[Any, ...]]] = {
    "registered": {
        "H1": ("openstack", "qt", "chromium"),
        "H2": (("qt", "chromium"), ("chromium", "qt")),
    },
    "without_chromium": {
        "H1": ("openstack", "qt"),
        "H2": (),
    },
}

# Computed and reported, never part of a verdict: OpenStack writes Python and every foreign
# organization available to it writes C++.
EXPLORATORY_H2: Mapping[str, tuple[tuple[str, str], ...]] = {
    "registered": (("openstack", "qt"),),
    "without_chromium": (("openstack", "qt"), ("qt", "openstack")),
}

READINGS = {
    ("pass", "absent"): "within-half",
    ("pass", "inconclusive"): "within-half, organization unresolved",
    ("absent", "pass"): "organization",
    ("inconclusive", "pass"): "organization",
    ("pass", "pass"): "nested",
    ("absent", "absent"): "neither",
}


def halves(org: str) -> tuple[str, str]:
    """The two pseudo-organizations `placebo_corpus.py` writes for one organization."""
    return (f"{org}-a", f"{org}-b")


def reading(h1: str, h2: str | None) -> str:
    """The pre-committed reading of one combination of hypothesis verdicts.

    `h2` is None under the fallback design, where only H1 is confirmatory.
    """
    if h2 is None:
        return {"pass": "within-half", "absent": "no within-half transfer"}.get(h1, "unresolved")
    return READINGS.get((h1, h2), "unresolved")


def holm_levels(hypotheses: int) -> list[float]:
    """Two-sided confidence levels Holm reads, strictest first: 1 - 2 * alpha / k, k = m..1."""
    if hypotheses < 1:
        raise ValueError("Holm needs at least one hypothesis")
    return [1.0 - 2.0 * FAMILY_ALPHA / k for k in range(hypotheses, 0, -1)]


def cell_verdict(low: float, high: float, *, sesoi: float = SESOI) -> str:
    """Supported above zero, absent within the SESOI band, or inconclusive.

    An interval above zero and inside the band is supported; `below_sesoi` flags it.
    """
    if low > 0.0:
        return "supported"
    if -sesoi < low and high < sesoi:
        return "absent"
    return "inconclusive"


def _cell(
    results: Mapping[str, Sequence[Mapping[str, Any]]], trained: str, window: str, seed: int
) -> Sequence[Mapping[str, Any]]:
    key = run_id(EvalRun(f"adapter:{trained}", window, seed))
    if key not in results:
        raise ValueError(
            f"no results for {key!r}: the {trained} adapter was not scored on {window}"
        )
    return results[key]


def _mean_arm(arms: Sequence[Sequence[Mapping[str, Any]]], *, metric: str) -> list[dict[str, Any]]:
    """One comparator from several arms scored on the same examples: their per-example mean.

    Averaging rather than stacking keeps each change a single cluster. Stacking the two
    foreign halves would enter every change twice, and the bootstrap would resample the
    copies as if they were independent changes. Every arm is held to `to_clusters`'s id
    guard, since a duplicated id in any arm but the first would otherwise be dropped.
    """
    for position, arm in enumerate(arms):
        require_unique_ids(arm, label=f"foreign arm {position}")
    first, *rest = arms
    by_id = [{r["id"]: r for r in arm} for arm in rest]
    for other in by_id:
        if set(other) != {r["id"] for r in first}:
            raise ValueError("the foreign halves must be evaluated on the same examples")
        for row in first:
            if other[row["id"]]["change_id"] != row["change_id"]:
                raise ValueError(
                    f"example {row['id']!r} belongs to different changes in the foreign halves"
                )
    return [
        {**row, metric: fmean([float(row[metric])] + [float(o[row["id"]][metric]) for o in by_id])}
        for row in first
    ]


def _disjoint(per_half: Sequence[list[Cluster]]) -> list[list[Cluster]]:
    """The halves' clusters, refusing a change that appears in both."""
    first, second = ({c.change_id for c in clusters} for clusters in per_half)
    shared = first & second
    if shared:
        raise ValueError(
            f"changes {sorted(shared)[:3]} appear in both halves; projects split whole, so a "
            "shared change means the halves were not built from one split"
        )
    return list(per_half)


def project_clusters(
    results: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    org: str,
    seed: int,
    metric: str = "exact_match",
) -> list[list[Cluster]]:
    """Own half against sibling half, one cluster list per evaluated half."""
    first, second = halves(org)
    return _disjoint(
        [
            to_clusters(
                _cell(results, window, window, seed),
                _cell(results, sibling, window, seed),
                metric=metric,
            )
            for window, sibling in ((first, second), (second, first))
        ]
    )


def organization_clusters(
    results: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    org: str,
    foreign: str,
    seed: int,
    metric: str = "exact_match",
) -> list[list[Cluster]]:
    """Sibling half against the foreign organization's halves, one list per evaluated half."""
    if foreign == org:
        raise ValueError(f"the comparator organization must be foreign to {org!r}")
    first, second = halves(org)
    return _disjoint(
        [
            to_clusters(
                _cell(results, sibling, window, seed),
                _mean_arm(
                    [_cell(results, other, window, seed) for other in halves(foreign)],
                    metric=metric,
                ),
                metric=metric,
            )
            for window, sibling in ((first, second), (second, first))
        ]
    )


def _strata(per_seed: Sequence[list[list[Cluster]]]) -> list[list[list[Cluster]]]:
    """`[seed][half]` to the `[half][seed]` layout the stratified bootstrap reads."""
    return [[run[h] for run in per_seed] for h in range(len(per_seed[0]))]


def _hypothesis(cells: Mapping[str, Mapping[str, Any]], confidence: float) -> str:
    """Intersection-union over cells at one level: pass, absent, or inconclusive."""
    if not cells:
        raise ValueError("a hypothesis with no confirmatory cells has no verdict")
    verdicts = [cell["verdicts"][confidence] for cell in cells.values()]
    if all(v == "supported" for v in verdicts):
        return "pass"
    if all(v == "absent" for v in verdicts):
        return "absent"
    return "inconclusive"


def holm_steps(
    per_hypothesis: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> tuple[dict[str, str], dict[str, float | None]]:
    """Holm's step-down over the hypotheses, and the level at which each one passed.

    Every hypothesis is first read at the strictest level. Those that pass leave the family,
    and the rest are read again at the next level, until a step passes nothing. A later step
    can only turn a verdict into a pass: absence is an equivalence claim, read at the
    strictest level alone.
    """
    levels = holm_levels(len(per_hypothesis))
    verdicts = {name: _hypothesis(cells, levels[0]) for name, cells in per_hypothesis.items()}
    passed_at: dict[str, float | None] = {
        name: levels[0] if verdict == "pass" else None for name, verdict in verdicts.items()
    }
    remaining = [name for name, verdict in verdicts.items() if verdict != "pass"]
    step = len(per_hypothesis) - len(remaining)
    while remaining and 0 < step < len(levels):
        passed = [n for n in remaining if _hypothesis(per_hypothesis[n], levels[step]) == "pass"]
        if not passed:
            break
        for name in passed:
            verdicts[name] = "pass"
            passed_at[name] = levels[step]
        remaining = [n for n in remaining if n not in passed]
        step += len(passed)
    return verdicts, passed_at


def holm_verdicts(per_hypothesis: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, str]:
    """The verdicts of `holm_steps` alone."""
    return holm_steps(per_hypothesis)[0]


def below_sesoi(
    per_hypothesis: Mapping[str, Mapping[str, Mapping[str, Any]]],
    passed_at: Mapping[str, float | None],
) -> list[str]:
    """Passing cells whose interval, at the level the hypothesis passed at, sits inside SESOI."""
    return sorted(
        f"{name}:{org}"
        for name, cells in per_hypothesis.items()
        if passed_at[name] is not None
        for org, cell in cells.items()
        if cell["intervals"][passed_at[name]]["high"] < SESOI
    )


def decomposition_gate(
    results: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    design: str = "registered",
    seeds: Sequence[int],
    metric: str = "exact_match",
    bootstrap_seed: int,
    resamples: int = 10_000,
    estimator: Estimator = paired_difference,
) -> dict[str, Any]:
    """The registered verdicts, their reading, and every cell behind them.

    Within an organization all of its contrasts are bootstrapped on the same draws, so the
    share of draws in which each is above zero, and their sum, are read jointly.
    """
    if design not in DESIGNS:
        raise ValueError(f"unknown design {design!r}; registered designs are {sorted(DESIGNS)}")
    if resamples < MIN_RESAMPLES:
        raise ValueError(f"at least {MIN_RESAMPLES} resamples, got {resamples}")
    _require_registered_seeds(seeds)
    cells = DESIGNS[design]
    confirmatory = {name: tuple(c) for name, c in cells.items() if c}
    levels = holm_levels(len(confirmatory))

    h1_orgs = set(cells["H1"])
    h2_pairs = {org: (foreign, "confirmatory") for org, foreign in cells["H2"]}
    for org, foreign in EXPLORATORY_H2[design]:
        h2_pairs.setdefault(org, (foreign, "exploratory"))

    per_org: dict[str, dict[str, Any]] = {"H1": {}, "H2": {}}
    joint: dict[str, dict[str, Any]] = {}
    for org in sorted(h1_orgs | set(h2_pairs)):
        contrasts: dict[str, list[list[list[Cluster]]]] = {}
        roles: dict[str, str] = {}
        if org in h1_orgs:
            contrasts["H1"] = _strata(
                [project_clusters(results, org=org, seed=s, metric=metric) for s in seeds]
            )
            roles["H1"] = "confirmatory"
        if org in h2_pairs:
            foreign, roles["H2"] = h2_pairs[org]
            try:
                contrasts["H2"] = _strata(
                    [
                        organization_clusters(
                            results, org=org, foreign=foreign, seed=s, metric=metric
                        )
                        for s in seeds
                    ]
                )
            except ValueError as error:
                if roles["H2"] == "confirmatory":
                    raise
                # An exploratory cell never decides a verdict, so it cannot withhold one either.
                per_org["H2"][org] = {
                    "role": "exploratory",
                    "foreign": foreign,
                    "error": str(error),
                }
        if not contrasts:
            continue
        estimates, draws = stratified_crossed_draws(
            contrasts, seed=bootstrap_seed, resamples=resamples, estimator=estimator
        )
        for name, values in draws.items():
            intervals = {c: percentile_interval(values, c) for c in levels}
            per_org[name][org] = {
                "role": roles[name],
                "foreign": h2_pairs[org][0] if name == "H2" else None,
                "estimate": estimates[name],
                "intervals": {c: {"low": lo, "high": hi} for c, (lo, hi) in intervals.items()},
                "verdicts": {c: cell_verdict(lo, hi) for c, (lo, hi) in intervals.items()},
                "clusters_per_half": [len(runs[0]) for runs in contrasts[name]],
                "seeds": len(seeds),
            }
        if len(draws) == 2:
            pairs = list(zip(draws["H1"], draws["H2"], strict=True))
            summed = [a + b for a, b in pairs]
            low, high = percentile_interval(summed, SUMMED_CONFIDENCE)
            joint[org] = {
                "shares": {
                    f"H1{'>' if a else '<='}0,H2{'>' if b else '<='}0": sum(
                        1 for x, y in pairs if (x > 0) == a and (y > 0) == b
                    )
                    / len(pairs)
                    for a in (True, False)
                    for b in (True, False)
                },
                "summed": {
                    "estimate": estimates["H1"] + estimates["H2"],
                    "low": low,
                    "high": high,
                    "confidence": SUMMED_CONFIDENCE,
                },
            }

    tested = {
        name: {o: cell for o, cell in per_org[name].items() if cell["role"] == "confirmatory"}
        for name in confirmatory
    }
    verdicts, passed_at = holm_steps(tested)
    return {
        "design": design,
        "verdicts": verdicts,
        "passed_at": passed_at,
        "reading": reading(verdicts["H1"], verdicts.get("H2")),
        "below_sesoi": below_sesoi(tested, passed_at),
        "per_org": per_org,
        "joint": joint,
        "sesoi": SESOI,
        "holm_levels": levels,
    }
