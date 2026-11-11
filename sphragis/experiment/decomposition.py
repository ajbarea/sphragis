"""The granularity decomposition: at what boundary does learned house style transfer.

Each organization is split into two halves of its own projects (`scripts/placebo_corpus.py`)
and every adapter is trained on one half. On a refinement from half `a` of organization `O`:

- the half-split contrast (H1) is own half against sibling half, `EM(O-a) - EM(O-b)`;
- the organization beyond the evaluated projects (H2) is sibling half against a foreign
  adapter, `s(O-b) - s(P-x)`. A confirmatory H2 cell reads its organization's C++ examples
  against one foreign adapter per evaluated half, matched to that sibling's C++ count
  (`foreign_arm`); an exploratory cell reads every example against the mean of a foreign
  organization's halves. Neither adapter has seen the evaluated projects.

Each contrast weights the two halves equally and resamples changes within each half. Which
cells are confirmatory is registered here, not passed in. Design of record:
`docs/superpowers/specs/2026-09-22-granularity-redesign.md`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from statistics import fmean
from typing import Any

from sphragis.corpus.halves import suffix
from sphragis.corpus.windows import WINDOWS
from sphragis.experiment.across import across_organizations, partial_conjunction
from sphragis.experiment.cells import (
    MIN_RESAMPLES,
    POSITIVE_CONTROL_CONFIDENCE,
    REGISTERED_METRIC,
    REGISTERED_RANK,
    REGISTERED_SPREAD_TARGET,
    RERUN_RANK,
    below_level,
    by_level,
    cluster_of,
    is_count,
    is_real,
    level_key,
    require_readable,
    require_test_read,
    same_calibration,
    sensitivity_bounds,
)
from sphragis.experiment.grid import EvalRun, run_id
from sphragis.experiment.runner import require_unique_ids, to_clusters
from sphragis.experiment.walk import _require_registered_seeds
from sphragis.measure.stats import (
    Cluster,
    Estimator,
    one_sided_alpha,
    paired_difference,
    percentile_interval,
    stratified_crossed_draws,
)

# Smallest effect of interest, in exact match: one more exact refinement per hundred review
# comments, the least gain taken to repay an organization training and governing an adapter of
# its own (cost-benefit, research log 2026-09-30). Fixed in advance; never derived from a
# contrast. It sets K through `partitions.XI`; beside each cell it is reported and decides no
# verdict.
SESOI = 0.01

#: Where the per-token log-probability's SESOI is registered (`likelihood_sesoi.py`), carried from
#: SESOI at the exchange rate the registered rule picks (research log 2026-10-15, 2026-10-16).
LIKELIHOOD_SESOI = Path(__file__).resolve().parents[2] / "datasets/results/likelihood-sesoi.json"


#: H1's co-primary score: the reference's per-token log-probability summed over its
#: non-identifier tokens alone (`tokens`), so a gain in vocabulary cannot pass H1 by itself
#: (research log, 2026-10-30).
COPRIMARY_METRIC = "logprob_per_token:other"

#: The scores every H1 organization is read on, the registered one first.
COPRIMARY_SCORES = ("logprob_per_token", COPRIMARY_METRIC)


def metric_sesoi(metric: str) -> float | None:
    """A metric's registered SESOI, or None where none is registered.

    Exact match's is the cost-benefit constant; the per-token log-probability's is carried from it
    at the registered exchange rate, and its artifact must exist. The non-identifier part of that
    score, H1's co-primary, takes the same SESOI: the parts are in the whole score's nats per
    token, and the planted quote-style convention the rate was carried at sits in string
    delimiters, which `tokens` classes as other (research log, 2026-10-30). Total
    log-probability, a sensitivity, and the other parts have none.
    """
    if metric == "exact_match":
        return SESOI
    if metric == COPRIMARY_METRIC:
        return metric_sesoi("logprob_per_token")
    if metric == "logprob_per_token":
        if not LIKELIHOOD_SESOI.is_file():
            raise FileNotFoundError(
                f"{LIKELIHOOD_SESOI} registers {metric}'s SESOI and is missing "
                "(scripts/likelihood_sesoi.py writes it)"
            )
        return float(json.loads(LIKELIHOOD_SESOI.read_text())["sesoi"])
    return None


# One-sided family-wise level across the confirmatory hypotheses, held by Holm's step-down.
FAMILY_ALPHA = 0.025

# The summed contrast is reported, never tested, so it reads the family's own level.
SUMMED_CONFIDENCE = 0.95

# Registration order. OpenStack is always admitted; Wikimedia when its split qualifies; Qt and
# Chromium only with written permission for automated access, Chromium's split qualifying too.
ORGANIZATIONS = ("openstack", "wikimedia", "qt", "chromium")

# The one language-matched pair of evaluated organizations: both ways when both are admitted.
H2_PAIR = ("qt", "chromium")

# Otherwise H2 is one cell on Qt, against the first of these sources whose host is admitted for
# the foreign draw when the first H2 development contrast is read and whose pool covers every
# matched draw (research log, 2026-10-14).
H2_ORGANIZATION = "qt"
FOREIGN_SOURCES = ("chromium", "fuchsia", "aosp")
#: Each source's pool by created date, `created_from` inclusive and `created_before` exclusive, as
#: item 1 of the H2 plan and each pool's rule fix them: Chromium and Fuchsia over Qt's own
#: training-window months (2026-10-19), and AOSP's changes before aosp-main went read-only
#: (`cli.REST_PERMITTED` scopes them the same).
FOREIGN_SPANS = {
    "chromium": WINDOWS["train"],
    "fuchsia": WINDOWS["train"],
    "aosp": ("2024-10-01", "2025-03-27"),
}

# Every confirmatory H2 cell reads its organization's C++ examples (`CPP_SUFFIXES`), its foreign
# arm one adapter per evaluated half matched to that sibling's C++ count (`foreign_arm`).
H2_POPULATION = "cpp"


def design(
    admitted: Iterable[str],
    foreign_source: str | None = None,
    *,
    h2_withdrawn: bool = False,
    withdrawn: Iterable[str] = (),
) -> dict[str, Any]:
    """The registered cells for whichever organizations are admitted.

    `admitted` must hold only names from `ORGANIZATIONS`, no duplicate, and always "openstack";
    input order does not matter, every result is ordered by `ORGANIZATIONS`. H1 is every admitted
    organization. H2 is confirmatory for `H2_PAIR`, both ways, when both are admitted; otherwise
    for `H2_ORGANIZATION` against `foreign_source`, when Qt is admitted and a source is given;
    `h2_withdrawn` (a foreign arm's failed check, `H2_WITHDRAWN`) leaves H2 with no confirmatory
    cell.
    Every other admitted organization gets one exploratory H2 cell instead, paired with the first
    other admitted organization in registration order: computed and reported, never part of a
    verdict.

    `withdrawn` names admitted organizations that failed an outcome-neutral check: each is
    reported as a deviation and the cells are those of the rest ("Outcome-Neutral Tests" and "An
    organization lost after acceptance", Stage 1 report), so without Qt H2 has no cell. OpenStack
    can be among them; at least one organization must remain.
    """
    ordered = _registered(admitted, ORGANIZATIONS, role="admitted")
    if "openstack" not in ordered:
        raise ValueError("openstack must always be admitted")
    lost = _registered(withdrawn, ORGANIZATIONS, role="withdrawn")
    outside = [o for o in lost if o not in ordered]
    if outside:
        raise ValueError(f"withdrawn organization(s) {outside} were never admitted")
    if len(lost) == len(ordered):
        raise ValueError("every admitted organization is withdrawn, so no hypothesis is read")
    admitted_list = ordered
    ordered = tuple(o for o in ordered if o not in lost)
    if H2_ORGANIZATION in lost or foreign_source in lost:
        foreign_source = None
    admitted_set = set(ordered)
    if foreign_source is not None and foreign_source not in FOREIGN_SOURCES:
        raise ValueError(f"unknown foreign source {foreign_source!r}; registered {FOREIGN_SOURCES}")
    h2: tuple[tuple[str, str], ...]
    if set(H2_PAIR) <= admitted_set:
        if foreign_source not in (None, H2_PAIR[1]):
            raise ValueError(f"with {H2_PAIR} both admitted, H2 is that pair, not {foreign_source}")
        # Each cell's foreign side is the other organization; no single source to report.
        h2, foreign_source = (H2_PAIR, H2_PAIR[::-1]), None
    elif foreign_source is not None:
        if H2_ORGANIZATION not in admitted_set:
            raise ValueError(f"a foreign source needs {H2_ORGANIZATION} admitted")
        h2 = ((H2_ORGANIZATION, foreign_source),)
    else:
        h2 = ()
    if h2_withdrawn:
        h2, foreign_source = (), None
    paired = {org for pair in h2 for org in pair}
    exploratory = []
    for org in ordered:
        if org in paired:
            continue
        others = [o for o in ordered if o != org]
        if others:
            exploratory.append((org, others[0]))
    return {
        "admitted": admitted_list,
        "withdrawn": lost,
        "H1": ordered,
        "H2": h2,
        "foreign_source": foreign_source,
        "h2_population": H2_POPULATION if h2 else None,
        "exploratory_h2": tuple(exploratory),
    }


def foreign_arm(source: str, sibling: str) -> str:
    """The matched foreign adapter for an H2 cell: trained on `source`'s examples, as many C++
    examples as `sibling`'s draw in the same run, filled to N from the source's other files."""
    if "-" in source:
        raise ValueError(f"a source name carries no hyphen, got {source!r}")
    return f"foreign-{source}-for-{sibling}"


def matched_foreign_arms(
    train: Mapping[str, Sequence[Mapping[str, Any]]],
    pool: Sequence[Mapping[str, Any]],
    *,
    org: str,
    source: str,
    seed: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    """H2's two matched foreign arms for one run over `org`'s halves: per evaluated half, `pool`
    drawn against the sibling half's training draw in `train` (`holdout.matched_foreign_draw`, C++
    by `is_cpp`), and the half each arm is scored on, as `walk` takes them (`foreign`)."""
    from sphragis.experiment.holdout import matched_foreign_draw

    first, second = halves(org)
    if set(train) != {first, second}:
        raise ValueError(f"foreign arms are drawn for {org}'s halves, not {sorted(train)}")
    rows: dict[str, list[dict[str, Any]]] = {}
    scored_on: dict[str, str] = {}
    for evaluated, sibling in ((first, second), (second, first)):
        arm = foreign_arm(source, sibling)
        rows[arm] = matched_foreign_draw(
            train[sibling], pool, in_language=is_cpp, seed=seed, sibling=sibling
        )
        scored_on[arm] = evaluated
    return rows, scored_on


def _registered(names: Iterable[str], registry: tuple[str, ...], *, role: str) -> tuple[str, ...]:
    """`names` in registration order, refused if any is outside `registry` or repeated."""
    listed = list(names)
    seen = set(listed)
    unknown = sorted(seen - set(registry))
    if unknown:
        raise ValueError(f"unknown organization(s) {unknown} in {role}; registered are {registry}")
    if len(seen) != len(listed):
        duplicates = sorted({o for o in seen if listed.count(o) > 1})
        raise ValueError(f"duplicate organization(s) {duplicates} in {role}")
    return tuple(o for o in registry if o in seen)


def valid_bound(bound: Any) -> bool:
    """A detectable effect: a finite number above zero, never a bool."""
    return is_real(bound) and bound > 0


# The Gerrit organizations admitted, set once by the commit that freezes them before any test
# window is read; None until then. A test read's Holm levels and cell count follow from them.
ADMITTED_ORGANIZATIONS: tuple[str, ...] | None = None

# Qt's foreign source, set by the commit that fixes it before the first H2 development contrast
# is read, or None while no source qualifies. Every read takes it from here, never from a
# caller, so no source can be chosen after a contrast is seen.
FOREIGN_SOURCE: str | None = "chromium"

# Why H2 was withdrawn from the confirmatory family after its source was fixed (a foreign arm's
# failed check: its pool over check 4's threshold, or its adapter failing check 3), set by the
# commit recording it; None while H2 stands.
H2_WITHDRAWN: str | None = None

# `holdout.pool_digest` of the registered source's pool as the stop rule found it covering,
# cleared of Qt's copies (research log, 2026-10-27). The foreign arms train on that pool alone.
FOREIGN_POOL_SHA256: str = "75d9f7f37e1faafb863f0fd3bcc3508506309e3c2e4edbe9b631d05258fe5ba9"


#: H2's exploratory second foreign source, next in the registered order after Chromium, whose pool
#: also covers every matched draw (research log, 2026-10-20). Its arm runs beside the registered
#: one on the same runs and reads sibling minus foreign again, binding no verdict and never in the
#: family: it separates the organization from Chromium's domain (research log, 2026-10-30).
EXPLORATORY_FOREIGN_SOURCE = "fuchsia"

# `holdout.pool_digest` of the exploratory source's pool as the stop rule found it covering, cleared
# of Qt's copies (research log, 2026-10-20; `foreign-pool-coverage-fuchsia.json`), fixed before
# the test window is fetched. Its arm trains on that pool alone.
EXPLORATORY_POOL_SHA256: str = "a17c70972046f23d811d3b3a8f04107fcdf59eec4cf6fe6af0de7195c601aacc"

#: H2's K: the registered grid's cap, where its simulated null misses nominal at 97.5% by under half
#: a Monte Carlo SE; its critical value is calibrated from that null (`partition_sensitivity.py
#: --calibrate`; research log, 2026-10-30).
H2_RUNS = 40


def require_registered_source(source: str, *, exploratory: bool = False) -> None:
    """Refuse an H2 step against `source` unless it is the registered `FOREIGN_SOURCE`, or, for
    the exploratory arm alone, `EXPLORATORY_FOREIGN_SOURCE`: no H2 development contrast is read,
    simulated or trained for before the source is fixed."""
    if FOREIGN_SOURCE is None:
        raise ValueError("no foreign source is registered, so no H2 step runs")
    if exploratory:
        if source != EXPLORATORY_FOREIGN_SOURCE:
            raise ValueError(
                f"H2's exploratory foreign source is {EXPLORATORY_FOREIGN_SOURCE}, not {source}"
            )
        return
    if source != FOREIGN_SOURCE:
        raise ValueError(f"H2's registered foreign source is {FOREIGN_SOURCE}, not {source}")


def registered_pool(source: str, *, exploratory: bool = False) -> str:
    """The digest of `source`'s cleared pool, the only pool its arms train on, refused unless
    `require_registered_source` admits the source (the exploratory arm's with `exploratory`)."""
    require_registered_source(source, exploratory=exploratory)
    return EXPLORATORY_POOL_SHA256 if exploratory else FOREIGN_POOL_SHA256


# The GitHub replication family in registration order (registered-decisions.md, "GitHub
# organizations"): read on H1 alone, after the confirmatory hypotheses, binding no verdict.
REPLICATION_FAMILY = ("apache", "llvm", "dotnet", "grafana")

# The members, set once by the commit that freezes them at Stage 1 (2026-11-20): those whose
# pilot, train and development windows are frozen and whose split meets the criteria at N.
# None until then, and `replication_gate` refuses to read the family.
REPLICATION_MEMBERS: tuple[str, ...] | None = None

# Each member's two-sided interval; its one-sided level, 0.0125, is the partial conjunction's.
# Registered as a fixed level, not a Holm step: it does not move with the number of confirmatory
# hypotheses on the Gerrit side.
REPLICATION_CONFIDENCE = 0.975

_CELL_FIELDS = ("estimate", "bounds")


def replication(members: Iterable[str]) -> dict[str, Any]:
    """The replication family for a set of members.

    `members` must hold only names from `REPLICATION_FAMILY`, no duplicate, and may be empty;
    every registered organization not among them is reported as not collected. A Gerrit
    organization is refused here as a GitHub one is by `design`.
    """
    ordered = _registered(members, REPLICATION_FAMILY, role="members")
    return {
        "members": ordered,
        "not_collected": tuple(o for o in REPLICATION_FAMILY if o not in ordered),
        "confidence": REPLICATION_CONFIDENCE,
    }


def confirmatory(spec: Mapping[str, Any]) -> dict[str, Any]:
    """A design's confirmatory hypotheses and their cells: H1, and H2 when it has a cell.

    With no H2 cell H1 is the whole family and carries the full one-sided `FAMILY_ALPHA`.
    """
    return {name: spec[name] for name in ("H1", "H2") if spec[name]}


def registered_read(org: str, *, hypothesis: str = "H1") -> dict[str, Any]:
    """What `org`'s test-window read is registered at: its levels, cell count, spread target,
    metric and cluster unit; for `hypothesis` "H2" also its cell, H2's against its source.

    A Gerrit organization is read at the Holm levels of
    `registered_design(ADMITTED_ORGANIZATIONS)`, its bound from a simulation of that design's H1
    cells, resampling relation chains (research log, 2026-10-21); a GitHub member, which has no
    chains, at `REPLICATION_CONFIDENCE` alone, as one cell, by change. Both take the bound at
    the registered spread target. Refused before the organizations are frozen, and for one
    outside them.
    """
    if org in REPLICATION_FAMILY:
        if hypothesis != "H1":
            raise ValueError(f"{org} is a replication member, read on H1 alone")
        if REPLICATION_MEMBERS is None or org not in REPLICATION_MEMBERS:
            raise ValueError(f"{org} is not a frozen replication member")
        return _read_at([REPLICATION_CONFIDENCE], cells=1, cluster_unit="change")
    if ADMITTED_ORGANIZATIONS is None or org not in ADMITTED_ORGANIZATIONS:
        raise ValueError(f"{org} is not among the frozen admitted organizations")
    spec = registered_design(ADMITTED_ORGANIZATIONS)
    if hypothesis == "H2":
        return _h2_read(spec, org)
    if hypothesis != "H1":
        raise ValueError(f"no hypothesis {hypothesis!r}; registered are H1 and H2")
    return _gerrit_read(spec)


def _h2_read(spec: Mapping[str, Any], org: str) -> dict[str, Any]:
    """The registered read of `org`'s confirmatory H2 cell: the design's Holm levels, sized for
    H2's intersection over its cells, on the registered score alone ("H2 on s' at its s level" is
    secondary), resampling relation chains."""
    sources = [source for evaluated, source in spec["H2"] if evaluated == org]
    if not sources:
        raise ValueError(f"{org} has no confirmatory H2 cell in the registered design")
    return {
        **_read_at(
            holm_levels(len(confirmatory(spec))), cells=len(spec["H2"]), cluster_unit="chain"
        ),
        "cell": ["H2", sources[0]],
    }


def registered_design(admitted: Iterable[str], *, withdrawn: Iterable[str] = ()) -> dict[str, Any]:
    """`design` for `admitted` with the registered `FOREIGN_SOURCE` and `H2_WITHDRAWN`: what every
    read uses. The source applies only with Qt admitted, and `design` drops it with Qt or the
    source withdrawn; with the pair admitted, a source other than Chromium is refused by
    `design`."""
    admitted, withdrawn = tuple(admitted), tuple(withdrawn)
    if FOREIGN_SOURCE is not None and FOREIGN_SOURCE not in FOREIGN_SOURCES:
        raise ValueError(
            f"registered FOREIGN_SOURCE {FOREIGN_SOURCE!r} is not in {FOREIGN_SOURCES}"
        )
    # `design` drops the source with Qt or the source withdrawn.
    source = FOREIGN_SOURCE if H2_ORGANIZATION in admitted else None
    return design(admitted, source, h2_withdrawn=H2_WITHDRAWN is not None, withdrawn=withdrawn)


def _read_at(levels: list[float], *, cells: int, cluster_unit: str) -> dict[str, Any]:
    return {
        "lora_rank": REGISTERED_RANK,
        "levels": levels,
        "cells": cells,
        "spread_target": REGISTERED_SPREAD_TARGET,
        "metric": REGISTERED_METRIC,
        "cluster_unit": cluster_unit,
    }


def _sesoi_of(metric: str) -> float:
    """The SESOI a reading on `metric` compares against, refused where none is registered."""
    sesoi = metric_sesoi(metric)
    if sesoi is None:
        raise ValueError(f"{metric} has no registered SESOI")
    return sesoi


def _gerrit_read(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The registered read of every H1 cell in `spec`: its Holm levels and cell count.

    H1 is read on two co-primary scores in each organization, the whole score and
    `COPRIMARY_METRIC`, and passes only when all of them pass, so its tests number twice its
    organizations and each is sized at that count (research log, 2026-10-30).
    """
    return _read_at(
        holm_levels(len(confirmatory(spec))),
        cells=len(COPRIMARY_SCORES) * len(spec["H1"]),
        cluster_unit="chain",
    )


def coprimary_read(expected: Mapping[str, Any]) -> dict[str, Any]:
    """`expected`, a Gerrit H1 cell's registered read, on `COPRIMARY_METRIC`.

    The co-primary is the same cell scored again: its K source is the coverage rule read at the
    cell's K on its own pilot (`k_coverage.py --registered-runs`), its bounds its own simulation's,
    and its check 5 the cell's, which tests the apparatus once (research log, 2026-10-30).
    """
    return {**expected, "metric": COPRIMARY_METRIC}


def _require_exactly(given: Any, orgs: Sequence[str], *, what: str) -> None:
    """Refuse `given` unless it maps exactly the organizations `orgs`."""
    if not isinstance(given, Mapping):
        raise ValueError(
            f"{what} must map each organization to its own, got {type(given).__name__}"
        )
    extra = sorted(set(given) - set(orgs))
    if extra:
        raise ValueError(f"{what} for organization(s) outside {list(orgs)}: {extra}")
    missing = [o for o in orgs if o not in given]
    if missing:
        raise ValueError(f"no {what} for {missing}")


def _test_size(
    org: str,
    report: Mapping[str, Any],
    simulation: Mapping[str, Any],
    powers: Mapping[str, Any] | None,
    bounds: Mapping[float, float],
    cells: int,
) -> dict[str, Any]:
    """The changes the cell is read on beside the count its simulation projected and drew.

    Both count changes a cell is read on, so a shortfall is the one the simulation's power at the
    realised size measures, whether the window arrived short or lost changes to unscored runs. A
    window below its projection carries that power from its `power_at_size.py` artifact.
    """
    size: dict[str, Any] = {}
    for name, source, field in (
        ("projected", simulation, "planned_changes"),
        ("realised", report, "changes"),
    ):
        count = source.get(field)
        if not is_count(count):
            raise ValueError(f"{org}: {field} {count!r} is not a whole number of changes")
        size[name] = count
    if size["realised"] < size["projected"]:
        if powers is None or org not in powers:
            raise ValueError(f"{org}: below its projection, with no power-at-size artifact")
        size["power"] = _power_at_realised(org, powers[org], size, bounds, cells)
    return size


def _power_at_realised(
    org: str, power: Any, size: Mapping[str, int], bounds: Mapping[float, float], cells: int
) -> dict[float, dict[str, float]]:
    """Each level's power and Monte Carlo SE from `org`'s `power_at_size.py` artifact.

    It must be at the cell's realised and projected sizes, the registered spread target and cell
    count, and at every level the cell's own registered bound.
    """
    if not isinstance(power, Mapping):
        raise ValueError(f"{org}: the power-at-size artifact is not a mapping")
    wanted = {
        "org": org,
        "changes": size["realised"],
        "planned_changes": size["projected"],
        "spread_target": REGISTERED_SPREAD_TARGET,
        "cells": cells,
    }
    for name, value in wanted.items():
        if power.get(name) != value:
            raise ValueError(f"{org}: power at size has {name} {power.get(name)!r}, not {value!r}")
    if not is_count(power.get("trials")):
        raise ValueError(f"{org}: power at size has trials {power.get('trials')!r}")
    try:
        at = by_level(power["by_level"])
    except (KeyError, ValueError) as error:
        raise ValueError(f"{org}: power at size: {error}") from error
    if set(at) != set(bounds):
        raise ValueError(f"{org}: power at levels {sorted(at)}, registered {sorted(bounds)}")
    out: dict[float, dict[str, float]] = {}
    for c, bound in bounds.items():
        entry = at[c]
        if not isinstance(entry, Mapping) or entry.get("bound") != bound:
            raise ValueError(f"{org}: power at {c} is not at the registered bound {bound}")
        p, se = entry.get("power"), entry.get("mc_se")
        if not (
            is_real(p)
            and 0.0 <= p <= 1.0
            and is_real(se)
            and math.isclose(se, math.sqrt(p * (1.0 - p) / power["trials"]), abs_tol=1e-12)
        ):
            raise ValueError(f"{org}: power {p!r}, Monte Carlo SE {se!r} at {c}")
        # The smallest effect the cell detects at the realised size, at its registered power, or
        # the bracket's top it detects nothing below (`power_at_size.power_at`).
        detects, above = entry.get("detectable"), entry.get("detectable_above")
        if not (valid_bound(detects) if above is None else detects is None and valid_bound(above)):
            raise ValueError(
                f"{org}: no detectable effect at the realised size at {c}: {detects!r}, "
                f"above {above!r}"
            )
        out[c] = {"power": p, "mc_se": se, "detectable": detects, "detectable_above": above}
    return out


def small_window(size: Mapping[str, Any], sesoi: float, level: float) -> str | None:
    """Why a cell read below its projected size is exploratory, or None: at `level`, the family's
    first, the smallest effect it detects at the realised size exceeds δ ("Sample size", Stage 1
    report: a cell that then cannot detect δ is exploratory). The first level is where every cell
    is first read, and with H2 confirmatory it is the 97.5% Table II reports detectable effects on;
    a level the family does not read decides nothing."""
    above = []
    at_level = {c: at for c, at in (size.get("power") or {}).items() if c == level_key(level)}
    for c, at in at_level.items():
        if at["detectable"] is None:
            above.append(f"nothing up to {at['detectable_above']:+.4f} at {c}")
        elif at["detectable"] > sesoi:
            above.append(f"{at['detectable']:+.4f} at {c}")
    if not above:
        return None
    return (
        f"detects {', '.join(above)} at its realised {size['realised']} of "
        f"{size['projected']} projected, above the SESOI {sesoi}"
    )


def _below_projection(
    cells: Mapping[str, Mapping[str, Any]], powers: Mapping[str, Any] | None
) -> list[str]:
    """Organizations whose test window holds fewer changes than its simulation projected; a
    power-at-size artifact for any other organization is refused."""
    below = sorted(o for o, c in cells.items() if c["size"]["realised"] < c["size"]["projected"])
    if powers is not None:
        if not isinstance(powers, Mapping):
            raise ValueError(f"powers must map organizations, got {type(powers).__name__}")
        extra = sorted(set(powers) - set(below))
        if extra:
            raise ValueError(f"power-at-size artifacts for {extra}, not below their projection")
    return below


def require_registered_analyses(
    report: Mapping[str, Any], *, org: str, expected: Mapping[str, Any]
) -> None:
    """Refuse a test read without the analyses the Stage 1 report registers beside its cell:
    on every read, outcome-neutral check 2 on the registered score, passed on the crossed 95%
    interval over the read's own runs, since every check runs before a confirmatory comparison
    is read ("Outcome-Neutral Tests"); on H2's read of the registered score, its joint reading
    with H1 on the same C++ examples and draws over its K runs, and its s' reading at its s
    levels ("Verdicts and Readings"; Analysis, "Secondary and exploratory analyses")."""
    runs = report.get("runs")
    check = report.get("positive_control")
    if not isinstance(check, Mapping):
        raise ValueError(f"{org}: the read carries no crossed check 2")
    evidence = check.get("evidence")
    if not (
        isinstance(evidence, Mapping)
        and check.get("name") == f"positive_control_crossed:{org}"
        and evidence.get("runs") == runs
        and evidence.get("metric") == REGISTERED_METRIC
        and evidence.get("confidence") == POSITIVE_CONTROL_CONFIDENCE
    ):
        raise ValueError(f"{org}: its check 2 is not the crossed read over its own runs")
    low = evidence.get("low")
    if not is_real(low) or check.get("passed") is not (low > 0.0):
        raise ValueError(f"{org}: its check 2 says passed {check.get('passed')!r}, low {low!r}")
    if check.get("passed") is not True:
        raise ValueError(f"{org}: check 2 failed; the organization is withdrawn")
    if expected["metric"] != REGISTERED_METRIC or tuple(expected.get("cell", ("H1",)))[0] != "H2":
        return
    joint = report.get("joint")
    summed = joint.get("summed") if isinstance(joint, Mapping) else None
    if not (
        isinstance(joint, Mapping)
        and joint.get("runs") == runs
        and joint.get("metric") == REGISTERED_METRIC
        and joint.get("population") == H2_POPULATION
        and isinstance(joint.get("h2"), Mapping)
        and joint["h2"].get("foreign") == tuple(expected["cell"])[1]
        and isinstance(summed, Mapping)
        and summed.get("confidence") == SUMMED_CONFIDENCE
        and all(is_real(summed.get(k)) for k in ("estimate", "low", "high"))
        and summed["low"] <= summed["high"]
    ):
        raise ValueError(f"{org}: the H2 read carries no joint reading with H1 over its K runs")
    secondary = report.get("on_s_prime")
    if not (isinstance(secondary, Mapping) and secondary.get("metric") == COPRIMARY_METRIC):
        raise ValueError(f"{org}: the H2 read carries no s' reading")
    if secondary.get("runs") != runs:
        raise ValueError(f"{org}: its s' reading is not over its {runs} runs")
    try:
        verdicts = by_level(secondary.get("verdicts"))
    except ValueError as error:
        raise ValueError(f"{org}: its s' reading: {error}") from error
    if set(verdicts) != {level_key(c) for c in expected["levels"]}:
        raise ValueError(f"{org}: its s' reading is not at its s levels {expected['levels']}")
    if not set(verdicts.values()) <= {"supported", "inconclusive"}:
        raise ValueError(f"{org}: its s' verdicts {sorted(verdicts.values())} are not readings")
    # With no bound or calibrated p, supported means a lower bound above zero (`read_intervals`).
    try:
        lows = {c: i["low"] for c, i in by_level(secondary.get("intervals")).items()}
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{org}: its s' intervals: {error}") from error
    if set(lows) != set(verdicts) or any(
        not is_real(lows[c]) or (verdicts[c] == "supported") != (lows[c] > 0.0) for c in verdicts
    ):
        raise ValueError(f"{org}: its s' verdicts do not follow from its intervals")


def _registered_bounds(
    org: str, cell: Mapping[str, Any], simulation: Any, expected: Mapping[str, Any]
) -> dict[float, float]:
    """`org`'s bound at each registered level, read from its simulation artifact.

    `require_test_read` has checked the simulation the cell records against the organization, K,
    the pilot's calibration and the chains file it drew from; the artifact given must be that
    simulation, every bound a detectable effect, and each the bound the cell was computed under.
    """
    recorded = cell["sensitivity"]
    drawn_as_recorded = False
    if isinstance(simulation, Mapping):
        try:
            drawn_as_recorded = cluster_of(simulation) == cluster_of(recorded)
        except ValueError as error:
            raise ValueError(f"{org}: the simulation given was {error}") from error
    if not (
        isinstance(simulation, Mapping)
        and simulation.get("org") == recorded.get("org")
        and simulation.get("runs") == recorded.get("runs")
        and simulation.get("planned_changes") == recorded.get("planned_changes")
        and same_calibration(simulation.get("spread_targets"), recorded.get("spread_targets"))
        and drawn_as_recorded
    ):
        raise ValueError(f"{org}: the simulation given is not the one its cell was read under")
    # The critical p the read applies is its K's; the bounds must be powered at that same p.
    k_from = cell.get("k_from") or {}
    if bool(k_from.get("calibrated")) != bool(simulation.get("calibrated")):
        raise ValueError(f"{org}: its K and the simulation given disagree on a calibrated p")
    if k_from.get("calibrated"):
        try:
            at_k = by_level(k_from.get("critical_p") or {})
            at_bounds = by_level(
                simulation["by_target"][expected["spread_target"]].get("critical_p") or {}
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{org}: critical p: {error}") from error
        if at_k != at_bounds:
            raise ValueError(
                f"{org}: its K applies critical p {at_k}, its bounds were powered at {at_bounds}"
            )
    try:
        bounds = sensitivity_bounds(
            simulation,
            expected["spread_target"],
            expected["levels"],
            cells=expected["cells"],
            cell=tuple(expected.get("cell", ("H1", None))),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{org}: simulation: {error}") from error
    try:
        computed_under = by_level({} if cell["bounds"] is None else cell["bounds"])
    except ValueError as error:
        raise ValueError(f"{org}: bounds: {error}") from error
    out: dict[float, float] = {}
    for c, bound in bounds.items():
        if not valid_bound(bound):
            raise ValueError(f"{org}: no registered detectable effect at {c}")
        if computed_under.get(level_key(c)) != bound:
            raise ValueError(
                f"{org}: the cell was computed under bound {computed_under.get(level_key(c))!r} "
                f"at {c}, its simulation gives {bound!r}"
            )
        out[level_key(c)] = bound
    return out


def _read_cells(
    reports: Mapping[str, Mapping[str, Any]],
    simulations: Mapping[str, Mapping[str, Any]],
    powers: Mapping[str, Mapping[str, Any]] | None,
    expected: Mapping[str, Any],
    sesoi: float,
    spreads: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[float, float]]]:
    """Each organization's cell on one score, H1's or H2's as `expected` registers, and its
    registered bounds.

    The spread rule applies to every cell (`partitions.spread_reading`): above its sizing bound
    it reads the simulation in `spreads` at its test spread, and applies the critical p
    calibrated there, the smaller of it and the K's own at each level, so calibration only
    tightens. Whether the cell is exploratory, by a refused calibration or a small window, is
    decided once the family's levels are known (`_mark_exploratory`)."""
    from sphragis.experiment.partitions import spread_reading

    spreads = {} if spreads is None else spreads
    extra = sorted(set(spreads) - set(reports))
    if extra:
        raise ValueError(f"spread simulations for {extra}, which are not read here")
    levels = expected["levels"]
    intervals = {
        c: require_readable(reports, confidence=c, fields=_CELL_FIELDS + ("bootstrap_se",))
        for c in levels
    }
    cells: dict[str, dict[str, Any]] = {}
    detectable: dict[str, dict[float, float]] = {}
    for org, report in reports.items():
        simulation = simulations[org]
        readings = require_test_read(report, org=org, expected=expected)
        require_registered_analyses(report, org=org, expected=expected)
        detectable[org] = _registered_bounds(org, report, simulation, expected)
        at = {c: intervals[c][org][level_key(c)] for c in levels}
        registered = (
            by_level(report["k_from"].get("critical_p") or {})
            if report["k_from"].get("calibrated")
            else None
        )
        spread = spread_reading(report, spreads.get(org), name=org)
        critical = _tightest(registered, spread["critical_p"])
        p_bounded = _bounded_test_p(org, report, at, sesoi, calibrated=critical is not None)
        size = _test_size(org, report, simulation, powers, detectable[org], expected["cells"])
        cells[org] = {
            "estimate": report["estimate"],
            **read_intervals(
                {c: (i["low"], i["high"]) for c, i in at.items()},
                detectable[org],
                sesoi=sesoi,
                p=report["p_one_sided"],
                critical=critical,
                p_bounded=p_bounded,
            ),
            "p_one_sided": report["p_one_sided"],
            "p_bounded": p_bounded,
            # The critical p the cell is read at, by level, or None for its interval alone.
            "critical_p": critical,
            "size": size,
            "spread": spread,
            # Why the cell left the confirmatory family, or None while it stands in it.
            "exploratory": None,
            # The registered sensitivities the report read (partition_pilot.py --without).
            "without": readings,
            # Reported beside the verdicts, binding none (Stage 1 report): the change-averaged
            # estimate, the read's check 2, and on an H2 read its joint and s' readings.
            "change_averaged": report.get("change_averaged"),
            **{
                key: report.get(key)
                for key in ("positive_control", "joint", "on_s_prime")
                if key in report
            },
        }
    return cells, detectable


def _mark_exploratory(cell: dict[str, Any], sesoi: float, levels: Sequence[float]) -> None:
    """Set why `cell` leaves its hypothesis, read at the levels its family reads, strictest first:
    a small window at the first (`small_window`), or its spread's calibration refused at any of
    them (`partitions.spread_reading`); None while it stands."""
    reasons = [small_window(cell["size"], sesoi, levels[0])]
    spread = cell["spread"]
    refused = [c for c in spread["refused_at"] if c in {level_key(x) for x in levels}]
    if refused:
        why = "; ".join(f"at {c}, {spread['refused_why'][str(c)]}" for c in refused)
        reasons.append(
            f"per-run spread {spread['spread']:.5f} above its bound {spread['bound']:.5f}; its "
            f"calibration there is refused: {why}"
        )
    cell["exploratory"] = "; ".join(r for r in reasons if r) or None


def _tightest(
    first: Mapping[float, float] | None, second: Mapping[float, float] | None
) -> dict[float, float] | None:
    """The smaller critical p at each level of two calibrations, either of which may be absent."""
    given = [c for c in (first, second) if c is not None]
    if not given:
        return None
    if len(given) == 2 and set(given[0]) != set(given[1]):
        raise ValueError(f"critical p at {sorted(given[0])} and at {sorted(given[1])}")
    return {c: min(g[c] for g in given) for c in given[0]}


def _bounded_test_p(
    org: str,
    report: Mapping[str, Any],
    at: Mapping[float, Mapping[str, float]],
    sesoi: float,
    *,
    calibrated: bool,
) -> float | None:
    """The report's bounded-test p (`partitions.h1_over_partitions`), which a calibrated cell
    must carry. It must agree with the intervals at every level, as the one-sided p must
    (`require_readable`): from the same draws against the same SESOI, it is below a level's alpha
    exactly when that level's upper bound is below the SESOI."""
    p = report.get("p_bounded")
    if p is None:
        if calibrated:
            raise ValueError(f"{org}: a calibrated cell carries no bounded-test p; re-read it")
        return None
    if not (is_real(p) and 0.0 <= p <= 1.0):
        raise ValueError(f"{org}: bounded-test p {p!r} is not in [0, 1]")
    for c, interval in at.items():
        if below_level(p, one_sided_alpha(c)) != (interval["high"] < sesoi):
            raise ValueError(
                f"{org}: bounded-test p {p} and interval high {interval['high']} disagree at "
                f"{c} against the SESOI {sesoi}, so they are not from the same draws"
            )
    return p


def _critical_at(cells: Mapping[str, Mapping[str, Any]], level: float) -> dict[str, float]:
    """Each calibrated cell's critical p at `level`, its K's or its test spread's: the test the
    cell applies, so never above alpha (a level whose null held has its quantile at or above
    alpha)."""
    alpha = one_sided_alpha(level)
    return {
        org: min(alpha, cell["critical_p"][level_key(level)])
        for org, cell in cells.items()
        if cell.get("critical_p") is not None
    }


def _across(
    reports: Mapping[str, Mapping[str, Any]],
    cells: Mapping[str, Mapping[str, Any]],
    level: float,
) -> dict[str, Any] | None:
    """The readings across organizations at `level` over every cell but one whose spread
    calibration is refused there: it has no critical p to scale its p by. A cell exploratory by a
    small window stays, its p valid if underpowered, as before the spread rule. None with no cell
    left."""
    standing = {
        org: cell
        for org, cell in cells.items()
        if level_key(level) not in cell["spread"]["refused_at"]
    }
    if not standing:
        return None
    return across_organizations(
        {org: reports[org] for org in standing},
        confidence=level,
        critical=_critical_at(standing, level),
    )


def _same_runs(whole: Mapping[str, Any], other: Mapping[str, Any], org: str) -> None:
    """Refuse co-primary reads of one organization that did not score the same runs and changes."""
    for field in ("runs", "changes", "run_files"):
        if whole.get(field) != other.get(field):
            raise ValueError(f"{org}: the co-primary read has {field} unlike the whole score's")


def confirmatory_gate(
    reports: Mapping[str, Mapping[str, Any]],
    *,
    simulations: Mapping[str, Mapping[str, Any]],
    coprimary: Mapping[str, Mapping[str, Any]],
    coprimary_simulations: Mapping[str, Mapping[str, Any]],
    h2: Mapping[str, Mapping[str, Any]] | None = None,
    h2_simulations: Mapping[str, Mapping[str, Any]] | None = None,
    powers: Mapping[str, Mapping[str, Any]] | None = None,
    coprimary_powers: Mapping[str, Mapping[str, Any]] | None = None,
    h2_powers: Mapping[str, Mapping[str, Any]] | None = None,
    spreads: Mapping[str, Mapping[str, Any]] | None = None,
    coprimary_spreads: Mapping[str, Mapping[str, Any]] | None = None,
    h2_spreads: Mapping[str, Mapping[str, Any]] | None = None,
    withdrawn: Mapping[str, Any] | None = None,
    lora_rank: int = REGISTERED_RANK,
) -> dict[str, Any]:
    """The registered verdicts over the admitted organizations' test-window reads: H1 and, where
    the design has one, H2, under Holm's step-down (Stage 1 report, Execution, "Gate").

    The organizations are `ADMITTED_ORGANIZATIONS`, never an argument; the gate refuses to run
    before they are frozen. `withdrawn` maps each admitted organization that failed an
    outcome-neutral check to its recorded battery, whose failed checks are reported as the
    deviation (`_withdrawals`), and the hypotheses are read over the rest ("Outcome-Neutral
    Tests"), so a withdrawn Qt leaves H1 alone confirmatory. `reports` holds each remaining
    organization's `partition_pilot.py` test-window report on the registered score and `coprimary`
    its report on `COPRIMARY_METRIC`, the same runs and changes; `h2` holds each confirmatory H2
    cell's report (`partition_pilot.py --hypothesis H2`), keyed by its evaluated organization,
    exactly when the design has one. Each comes with its simulation, and every report is checked by
    `require_readable` and by `require_test_read` against the registered read, which withdrawal
    does not change, and its bounds read from its simulation. Each hypothesis is the
    intersection-union over its cells (H1: every organization on both scores, research log
    2026-10-30), read by `holm_steps` at the Holm levels of the hypotheses that remain tested.

    A cell is exploratory, reported and left out of its hypothesis, when it was read below its
    projected size and its detectable effect there exceeds δ (`small_window`, from `powers`), or
    when its per-run test spread exceeds its sizing bound and the calibration at that spread was
    refused (`partitions.spread_reading`, from `spreads`); above the bound and calibrated, it is
    read at the critical p calibrated there. Either is read only at the levels its family reads
    (`_mark_exploratory`). A hypothesis with every cell exploratory is not tested; an H1 score with
    none left is named in the reading.

    Beside the verdicts, binding none: each H1 score's own reading, each cell's `FOUR_WAY`
    outcome, the readings across organizations (`_across`: without a cell whose spread
    calibration is refused at the level read, and None with none left), and each cell's realised
    test size against its simulation's projection ("Fetch horizon", registered-decisions.md).

    `lora_rank` `RERUN_RANK` reads the grid's rerun at 256 the same way, every report of it at
    that rank; its verdicts are supplementary and change no registered one (`rank_reading`).
    """
    _require_rank(lora_rank)
    if ADMITTED_ORGANIZATIONS is None:
        raise ValueError("the admitted organizations are not frozen yet")
    lost = _withdrawals(withdrawn)
    registered = registered_design(ADMITTED_ORGANIZATIONS)
    spec = registered_design(ADMITTED_ORGANIZATIONS, withdrawn=lost)
    orgs = spec["H1"]
    h2_orgs = tuple(org for org, _ in spec["H2"])
    for what, given in (
        ("reports", reports),
        ("simulations", simulations),
        ("co-primary reports", coprimary),
        ("co-primary simulations", coprimary_simulations),
    ):
        _require_exactly(given, orgs, what=what)
    if h2_orgs:
        for what, given in (("H2 reports", h2), ("H2 simulations", h2_simulations)):
            _require_exactly(given, h2_orgs, what=what)
    elif h2 or h2_simulations or h2_powers or h2_spreads:
        raise ValueError("H2 reads given, and the design has no confirmatory H2 cell")
    for org in orgs:
        _same_runs(reports[org], coprimary[org], org)
    # Every report was read at the registration's levels and cell count; the verdicts at the Holm
    # levels of what remains tested, a subset of them.
    expected = _gerrit_read(registered)
    expected["lora_rank"] = lora_rank
    sesoi = _sesoi_of(expected["metric"])
    cells, detectable = _read_cells(reports, simulations, powers, expected, sesoi, spreads)
    other = coprimary_read(expected)
    other_cells, other_detectable = _read_cells(
        coprimary,
        coprimary_simulations,
        coprimary_powers,
        other,
        _sesoi_of(COPRIMARY_METRIC),
        coprimary_spreads,
    )
    extra = sorted(set(h2_spreads or {}) - set(h2_orgs))
    if extra:
        raise ValueError(f"H2 spread simulations for {extra}, which have no H2 cell")
    h2_cells: dict[str, dict[str, Any]] = {}
    h2_detectable: dict[str, dict[float, float]] = {}
    for org in h2_orgs:
        read, bounds = _read_cells(
            {org: (h2 or {})[org]},
            {org: (h2_simulations or {})[org]},
            h2_powers,
            _h2_read(registered, org) | {"lora_rank": lora_rank},
            sesoi,
            {o: sim for o, sim in (h2_spreads or {}).items() if o == org},
        )
        h2_cells |= read
        h2_detectable |= bounds
    every = {
        "H1": {**cells, **{f"{org}:{COPRIMARY_METRIC}": c for org, c in other_cells.items()}},
        "H2": h2_cells,
    }
    # Each cell's SESOI: its score's (H2 and the whole score's H1 cells share the registered one).
    sesois = {
        f"{name}:{cell}": _sesoi_of(
            COPRIMARY_METRIC if cell.endswith(COPRIMARY_METRIC) else expected["metric"]
        )
        for name, by_cell in every.items()
        for cell in by_cell
    }
    # Exploratory cells, read in two steps so no level the family does not read decides one: at
    # the design's levels, a hypothesis left with no cell drops out; the rest are read again at
    # the levels of the family that remains.
    first = holm_levels(len(confirmatory(spec)))
    for name, by_cell in every.items():
        for cell, c in by_cell.items():
            _mark_exploratory(c, sesois[f"{name}:{cell}"], first)
    remaining = [
        name
        for name in confirmatory(spec)
        if any(c["exploratory"] is None for c in every[name].values())
    ]
    if 0 < len(remaining) < len(confirmatory(spec)):
        for name in remaining:
            for cell, c in every[name].items():
                _mark_exploratory(c, sesois[f"{name}:{cell}"], holm_levels(len(remaining)))
    standing = {
        name: {cell: c for cell, c in by_cell.items() if c["exploratory"] is None}
        for name, by_cell in every.items()
        if name in confirmatory(spec)
    }
    tested = {name: by_cell for name, by_cell in standing.items() if by_cell}
    exploratory = sorted(
        f"{name}:{cell}"
        for name, by_cell in every.items()
        for cell, c in by_cell.items()
        if c["exploratory"] is not None
    )
    verdicts, passed_at = holm_steps(tested) if tested else ({}, {})
    for name in standing:
        verdicts.setdefault(name, "not tested")
        passed_at.setdefault(name, None)
    levels = holm_levels(max(1, len(tested)))
    h2_verdict = None if verdicts.get("H2", "not tested") == "not tested" else verdicts["H2"]
    # Each H1 score's confirmatory cells read alone at the strictest level, binding no verdict:
    # the whole score passing where the non-identifier score does not says the gain may be
    # vocabulary alone.
    scores = {
        metric: _hypothesis(standing_, levels[0]) if standing_ else "not tested"
        for metric, standing_ in (
            (expected["metric"], {o: c for o, c in cells.items() if c["exploratory"] is None}),
            (COPRIMARY_METRIC, {o: c for o, c in other_cells.items() if c["exploratory"] is None}),
        )
    }
    vocabulary_only = verdicts["H1"] != "pass" and scores[expected["metric"]] == "pass"
    # A pass whose every cell reads below δ is an effect too small to matter (`FOUR_WAY`).
    too_small = {
        name: verdicts[name] == "pass"
        and all(c["four_way"][passed_at[name]] == FOUR_WAY[1] for c in tested[name].values())
        for name in tested
    }
    mixed = "H1" in tested and verdicts["H1"] != "pass" and is_mixed(tested["H1"], levels[0])
    said = reading(verdicts["H1"], h2_verdict, mixed=mixed)
    if h2_verdict is None and not mixed:
        # H1 alone: its qualifiers replace the bare row, as they always have.
        if vocabulary_only:
            said = "within-half on the whole score alone"
        elif too_small.get("H1"):
            said = "within-half, below the SESOI in every cell"
    # H1 is registered on both scores and s' qualifies s (Table III legend): a score with no
    # confirmatory cell left is said, never dropped silently.
    untested = [m for m, read in scores.items() if read == "not tested"]
    if verdicts["H1"] != "not tested" and untested:
        said += f"; {', '.join(untested)} not tested (every cell exploratory)"
    return {
        "lora_rank": lora_rank,
        "design": spec,
        "withdrawn": dict(lost),
        "verdicts": verdicts,
        "passed_at": passed_at,
        "reading": said,
        "mixed": mixed,
        "vocabulary_only": vocabulary_only,
        # Per H1 cell and level, binding no verdict: an s cell supported where its s' reading at
        # the same level is not reads as names (Stage 1 report, "Verdicts and Readings").
        "names": {
            org: {
                c: cells[org]["verdicts"][c] == "supported"
                and other_cells[org]["verdicts"][c] != "supported"
                for c in cells[org]["verdicts"]
            }
            for org in cells
        },
        # Per H2 cell and level, binding no verdict: its s cell supported where its s' reading at
        # the same level is not reads as Qt's vocabulary (Stage 1 report, "Verdicts and Readings").
        "vocabulary": {
            org: {
                c: cell["verdicts"][c] == "supported"
                and by_level(cell["on_s_prime"]["verdicts"])[level_key(c)] != "supported"
                for c in cell["verdicts"]
            }
            for org, cell in h2_cells.items()
        },
        "too_small": too_small,
        "scores": scores,
        "below_sesoi": below_sesoi(tested, passed_at, sesoi=sesoi),
        "exploratory": exploratory,
        "cells": cells,
        "coprimary_cells": other_cells,
        "h2_cells": h2_cells,
        "metric": expected["metric"],
        "coprimary_metric": COPRIMARY_METRIC,
        "sesoi": sesoi,
        "detectable": {
            "H1": detectable,
            f"H1:{COPRIMARY_METRIC}": other_detectable,
            "H2": h2_detectable,
        },
        "holm_levels": levels,
        "below_projection": _below_projection(cells, powers),
        "coprimary_below_projection": _below_projection(other_cells, coprimary_powers),
        "h2_below_projection": _below_projection(h2_cells, h2_powers),
        "across": _across(reports, cells, levels[0]),
        "coprimary_across": _across(coprimary, other_cells, levels[0]),
    }


def _withdrawals(
    withdrawn: Mapping[str, Any] | None,
    among: Sequence[str] | None = None,
    *,
    who: str = "the admitted; a foreign arm's failed check is recorded in H2_WITHDRAWN",
) -> dict[str, list[str]]:
    """Each withdrawn organization with the outcome-neutral checks it failed, read from the
    battery recorded for it (`neutral.withdrawing_failures`): refused unless every one is one of
    `among` (the admitted organizations when None) whose battery failed a check that withdraws,
    2 to 6. Chromium's pool is withdrawn from H2 by `H2_WITHDRAWN`, not here."""
    from sphragis.experiment.neutral import withdrawing_failures

    if withdrawn is None:
        return {}
    if not isinstance(withdrawn, Mapping):
        raise ValueError(f"withdrawn must map organizations to batteries, got {type(withdrawn)}")
    order = ORGANIZATIONS if among is None else tuple(among)
    among = tuple(ADMITTED_ORGANIZATIONS or ()) if among is None else tuple(among)
    outside = sorted(set(withdrawn) - set(among))
    if outside:
        raise ValueError(f"withdrawn organization(s) {outside} are not among {who}")
    failed = {o: withdrawing_failures(battery, org=o) for o, battery in withdrawn.items()}
    unfailed = sorted(o for o, checks in failed.items() if not checks)
    if unfailed:
        raise ValueError(
            f"withdrawn organization(s) {unfailed} failed no check that withdraws (2 to 6, not a "
            "foreign arm's: a foreign arm's failed check is recorded in H2_WITHDRAWN)"
        )
    return {o: failed[o] for o in order if o in failed}


def _require_rank(lora_rank: Any) -> None:
    if lora_rank not in (REGISTERED_RANK, RERUN_RANK) or isinstance(lora_rank, bool):
        raise ValueError(f"rank {lora_rank!r}: registered {REGISTERED_RANK}, rerun {RERUN_RANK}")


#: The rank-256 rerun's readings (Stage 1 report, "Rank 256"), per cell and level.
CAPACITY_LIMITED = "capacity-limited"
BOUNDED_AT_BOTH = "bounded at both ranks"
BOUNDED_AT_32_ONLY = "bounded at 32 only"


def rank_reading(registered: Mapping[str, Any], rerun: Mapping[str, Any]) -> dict[str, Any]:
    """The rank-256 rerun read beside the registered gate, binding no verdict: "a cell
    inconclusive at 32 and supported at 256 is capacity-limited, and one bounded at 32 counts
    against a learnable house style only if bounded at 256" (Stage 1 report, "Rank 256").

    `registered` and `rerun` are `confirmatory_gate` (or `replication_gate`) outputs at
    `REGISTERED_RANK` and `RERUN_RANK`. Each cell present in both, at each level read in both, is
    `CAPACITY_LIMITED`, `BOUNDED_AT_BOTH` (counts against), `BOUNDED_AT_32_ONLY` (does not), or
    None. The verdicts returned are the registered gate's, unchanged.
    """
    if registered.get("lora_rank") != REGISTERED_RANK or rerun.get("lora_rank") != RERUN_RANK:
        raise ValueError(
            f"read at ranks {registered.get('lora_rank')!r} and {rerun.get('lora_rank')!r}, not "
            f"{REGISTERED_RANK} and {RERUN_RANK}"
        )
    groups = [g for g in ("cells", "coprimary_cells", "h2_cells") if g in registered]
    readings: dict[str, dict[str, dict[float, str | None]]] = {}
    for group in groups:
        at_32 = registered[group]
        at_256 = rerun.get(group) or {}
        if set(at_32) != set(at_256):
            raise ValueError(
                f"{group}: the rerun reads {sorted(at_256)}, the registered gate {sorted(at_32)}"
            )
        for cell, read in at_32.items():
            if "verdicts" not in read:
                continue  # a replication member's single verdict is read below
            verdicts_32 = by_level(read["verdicts"])
            verdicts_256 = by_level(at_256[cell]["verdicts"])
            if set(verdicts_32) != set(verdicts_256):
                raise ValueError(f"{group}:{cell}: read at other levels in the rerun")
            readings.setdefault(group, {})[cell] = {
                c: _rank_cell(v, verdicts_256[c]) for c, v in verdicts_32.items()
            }
    if "cells" in registered and all("verdict" in r for r in registered["cells"].values()):
        readings["cells"] = {
            org: {
                level_key(REPLICATION_CONFIDENCE): _rank_cell(
                    read["verdict"], rerun["cells"][org]["verdict"]
                )
            }
            for org, read in registered["cells"].items()
        }
    return {"verdicts": registered.get("verdicts"), "readings": readings}


def _rank_cell(at_32: str, at_256: str) -> str | None:
    if at_32 == "inconclusive" and at_256 == "supported":
        return CAPACITY_LIMITED
    if at_32 == "bounded":
        return BOUNDED_AT_BOTH if at_256 == "bounded" else BOUNDED_AT_32_ONLY
    return None


def replication_gate(
    cells: Mapping[str, Mapping[str, Any]],
    *,
    simulations: Mapping[str, Mapping[str, Any]],
    powers: Mapping[str, Mapping[str, Any]] | None = None,
    spreads: Mapping[str, Mapping[str, Any]] | None = None,
    withdrawn: Mapping[str, Any] | None = None,
    lora_rank: int = REGISTERED_RANK,
) -> dict[str, Any]:
    """Each frozen member's H1 verdict and the family's partial conjunction r.

    `lora_rank` `RERUN_RANK` reads the rerun at 256, supplementary (`rank_reading`).

    `withdrawn` maps each member that failed an outcome-neutral check to its recorded battery (a
    failed crossed check 2 is written by `partition_pilot.py`), reported as the deviation; the
    family is read over the rest, as `confirmatory_gate` reads the admitted organizations.

    The members are `REPLICATION_MEMBERS`, never an argument, so none joins after Stage 1; the
    gate refuses to run before they are frozen. `cells` holds each remaining member's
    `partition_pilot.py --replication` test-window report and no other organization (read back
    from JSON is accepted); each is checked by `require_readable` and by `require_test_read`
    against `registered_read`. `simulations` holds each member's partition-simulation artifact:
    the bound is read from it at the registered spread target, level and cell count, and must be
    the one the cell was computed under. It checks the artifacts as the scripts write them; that
    none was edited afterwards is the git history's to show. Each verdict is read from the cell's
    interval at `REPLICATION_CONFIDENCE`, and r at its one-sided level from p-values that must
    agree with the intervals. Each member's realised test size is reported against its
    simulation's projection, and one below it with its power at the realised size from `powers`,
    as `confirmatory_gate` reports them. The family is read "with the Gerrit cells' windows, rules
    and estimand" (Stage 1 report), so a member that cannot detect δ at its realised size, or
    whose spread calibration is refused, is exploratory and leaves the conjunction, and one above
    its sizing bound is read at the critical p calibrated at its spread (`spreads`), its p in the
    conjunction scaled by alpha over that critical p as `across_organizations` scales one.
    """
    from sphragis.experiment.partitions import spread_reading

    _require_rank(lora_rank)
    if REPLICATION_MEMBERS is None:
        raise ValueError("the replication family's members are not frozen yet (Stage 1)")
    family = replication(REPLICATION_MEMBERS)
    lost = _withdrawals(withdrawn, family["members"], who="the frozen members")
    members = tuple(o for o in family["members"] if o not in lost)
    for what, given in (("cells", cells), ("simulations", simulations)):
        _require_exactly(given, members, what=what)
    spreads = {} if spreads is None else spreads
    extra = sorted(set(spreads) - set(members))
    if extra:
        raise ValueError(f"spread simulations for {extra}, which are not members")
    level = level_key(REPLICATION_CONFIDENCE)
    alpha = one_sided_alpha(REPLICATION_CONFIDENCE)
    intervals = require_readable(cells, confidence=REPLICATION_CONFIDENCE, fields=_CELL_FIELDS)
    read: dict[str, dict[str, Any]] = {}
    for org in members:
        cell, simulation = cells[org], simulations[org]
        expected = registered_read(org)
        expected["lora_rank"] = lora_rank
        readings = require_test_read(cell, org=org, expected=expected)
        require_registered_analyses(cell, org=org, expected=expected)
        # Only H2's K and a co-primary score's are registered as calibrated.
        if (cell.get("k_from") or {}).get("calibrated"):
            raise ValueError(f"{org}: a replication member's K is never calibrated")
        bounds = _registered_bounds(org, cell, simulation, expected)
        bound = bounds[level]
        interval = intervals[org][level]
        sesoi = _sesoi_of(expected["metric"])
        size = _test_size(org, cell, simulation, powers, bounds, expected["cells"])
        spread = spread_reading(cell, spreads.get(org), name=org)
        critical = spread["critical_p"]
        p_bounded = _bounded_test_p(
            org, cell, {level: interval}, sesoi, calibrated=critical is not None
        )
        at = read_intervals(
            {level: (interval["low"], interval["high"])},
            {level: bound},
            sesoi=sesoi,
            p=cell["p_one_sided"],
            critical=critical,
            p_bounded=p_bounded,
        )
        read[org] = {
            "estimate": cell["estimate"],
            "interval": {"low": interval["low"], "high": interval["high"]},
            "bound": bound,
            "verdict": at["verdicts"][level],
            "four_way": at["four_way"][level],
            "meaningful": meaningful(interval["low"], sesoi),
            "p_one_sided": cell["p_one_sided"],
            "p_bounded": p_bounded,
            "critical_p": critical,
            "size": size,
            "spread": spread,
            # The registered sensitivities the report read (partition_pilot.py --without).
            "without": readings,
            # Beside the verdict, binding none, as `confirmatory_gate` carries them.
            "change_averaged": cell.get("change_averaged"),
            "positive_control": cell.get("positive_control"),
        }
        _mark_exploratory(read[org], sesoi, [level])
    counted = {
        o: min(1.0, c["p_one_sided"] * alpha / min(alpha, c["critical_p"][level]))
        if c["critical_p"] is not None
        else c["p_one_sided"]
        for o, c in read.items()
        if c["exploratory"] is None
    }
    return {
        **family,
        "lora_rank": lora_rank,
        "withdrawn": lost,
        "cells": read,
        "exploratory": sorted(o for o, c in read.items() if c["exploratory"] is not None),
        "below_projection": _below_projection(read, powers),
        "partial_conjunction": partial_conjunction(
            counted, alpha=one_sided_alpha(REPLICATION_CONFIDENCE)
        )
        if counted
        else None,
    }


# C++ source and header suffixes. Qt writes .cpp and Chromium .cc, so a suffix match that
# compared file types directly would find the two organizations sharing only headers.
CPP_SUFFIXES = frozenset({".cc", ".cpp", ".cxx", ".c++", ".h", ".hh", ".hpp", ".hxx", ".inl"})

READINGS = {
    ("pass", "bounded"): "within-half",
    ("pass", "inconclusive"): "within-half, organization unresolved",
    ("bounded", "pass"): "organization",
    ("inconclusive", "pass"): "organization",
    ("pass", "pass"): "nested",
    ("bounded", "bounded"): "neither",
}


def halves(org: str) -> tuple[str, str]:
    """The two pseudo-organizations `placebo_corpus.py` writes for one organization."""
    return (f"{org}-a", f"{org}-b")


#: Table III's mixed row: some H1 cells supported, the rest bounded, H2 anything but a pass.
MIXED_READING = (
    "style transfers within a half in some cells, not all; read cell by cell, s against s′"
)


def reading(h1: str, h2: str | None, *, mixed: bool = False) -> str:
    """The pre-committed reading of one combination of hypothesis verdicts (Table III).

    `h2` is None under a design with no confirmatory H2 cell, where only H1 is confirmatory.
    `mixed` says H1's cells are mixed (`is_mixed`); with H1 not passing and H2 anything but a pass
    (the legend's "not pass", H2 untested included) that is the mixed row.
    """
    if mixed and h1 != "pass" and h2 != "pass":
        return MIXED_READING
    # "not pass & pass": H1 anything but a pass, untested included (the legend's "not pass").
    if h2 == "pass" and h1 != "pass":
        return READINGS[("bounded", "pass")]
    if h2 is None:
        return {"pass": "within-half", "bounded": "no within-half transfer"}.get(h1, "unresolved")
    return READINGS.get((h1, h2), "unresolved")


def is_mixed(cells: Mapping[str, Mapping[str, Any]], confidence: float) -> bool:
    """Whether a hypothesis's cells are mixed at `confidence`: some supported and every other one
    bounded (Table III's legend). Read at the family's first level, the only one a bounded reading
    is made at."""
    verdicts = [cell["verdicts"][confidence] for cell in cells.values()]
    return (
        "supported" in verdicts
        and "bounded" in verdicts
        and all(v in ("supported", "bounded") for v in verdicts)
    )


def holm_levels(hypotheses: int) -> list[float]:
    """Two-sided confidence levels Holm reads, strictest first: 1 - 2 * alpha / k, k = m..1."""
    if hypotheses < 1:
        raise ValueError("Holm needs at least one hypothesis")
    return [1.0 - 2.0 * FAMILY_ALPHA / k for k in range(hypotheses, 0, -1)]


def cell_verdict(
    low: float,
    high: float,
    *,
    margin: float | None,
    above: bool | None = None,
    below: bool | None = None,
) -> str:
    """Supported above zero, bounded below the equivalence margin, or inconclusive.

    Bounded is a one-sided equivalence test at `margin`, the SESOI (Lakens, Scheel and Isager,
    2018): its upper bound excludes every effect as large as the smallest of interest. `margin` is
    None for a cell without a registered sensitivity analysis, which can be supported but never
    bounded; the analysis is the power justification, not the margin (research log, 2026-10-14).
    `above` and `below` replace the lower and upper bound's tests where a critical p is calibrated
    (`read_intervals`). A cell both supported and bounded reads supported.
    """
    if (low > 0.0) if above is None else above:
        return "supported"
    if _below(high, margin, below):
        return "bounded"
    return "inconclusive"


def _below(high: float, margin: float | None, below: bool | None) -> bool:
    """The bounded test: the upper bound below a registered margin, and `below` where given."""
    return margin is not None and high < margin and (below is None or below)


#: The four outcomes of a test against zero beside an equivalence test against δ (Lakens 2017,
#: "four possible outcomes"), each binding no verdict beyond the pass rule's: above zero and not
#: shown below δ; above zero and below δ, an effect too small to matter; below δ and not above
#: zero; neither (research log, 2026-10-30).
FOUR_WAY = ("supported", "supported, below SESOI", "bounded", "inconclusive")


def four_way(
    low: float,
    high: float,
    *,
    margin: float | None,
    above: bool | None = None,
    below: bool | None = None,
) -> str:
    """A cell's `FOUR_WAY` outcome; with no registered margin, never one that reads below δ.
    `above` and `below` as in `cell_verdict`."""
    under = _below(high, margin, below)
    if (low > 0.0) if above is None else above:
        return FOUR_WAY[1] if under else FOUR_WAY[0]
    return FOUR_WAY[2] if under else FOUR_WAY[3]


def meaningful(low: float, sesoi: float = SESOI) -> bool:
    """Whether an interval's lower bound clears the SESOI: reported, never a verdict."""
    return low > sesoi


def within_sesoi(low: float, high: float, sesoi: float = SESOI) -> bool:
    """Whether the interval sits inside the SESOI band: reported, never a verdict."""
    return low > -sesoi and high < sesoi


def read_intervals(
    intervals: Mapping[float, tuple[float, float]],
    bounds: Mapping[float, Any],
    *,
    sesoi: float = SESOI,
    p: float | None = None,
    critical: Mapping[float, float] | None = None,
    p_bounded: float | None = None,
) -> dict[str, Any]:
    """A cell's interval at each level, its verdict, and the readings beside it. A level with a
    registered bound in `bounds` (keyed by `level_key`) is read bounded against the SESOI; a level
    without one is never read bounded. With a `critical` p calibrated from the simulated null
    (keyed by `level_key`), a level reads above zero only where its interval does and the one-sided
    `p` is below it too (research log, 2026-10-30), and bounded only where its upper bound is below
    the SESOI and the bounded test's `p_bounded`, the share of draws at or above it, is below it
    too, so calibration only tightens either reading (Stage 1 report, "Verdicts")."""
    margins = {c: sesoi if valid_bound(bounds.get(level_key(c))) else None for c in intervals}
    if critical is not None and (p is None or p_bounded is None):
        raise ValueError("a calibrated critical p needs the cell's one-sided p and bounded-test p")
    above = {
        c: lo > 0.0 and (critical is None or (p is not None and p < critical[level_key(c)]))
        for c, (lo, _) in intervals.items()
    }
    below = {
        c: _below(hi, margins[c], None)
        and (critical is None or (p_bounded is not None and p_bounded < critical[level_key(c)]))
        for c, (_, hi) in intervals.items()
    }
    return {
        "intervals": {c: {"low": lo, "high": hi} for c, (lo, hi) in intervals.items()},
        "verdicts": {
            c: cell_verdict(lo, hi, margin=margins[c], above=above[c], below=below[c])
            for c, (lo, hi) in intervals.items()
        },
        "four_way": {
            c: four_way(lo, hi, margin=margins[c], above=above[c], below=below[c])
            for c, (lo, hi) in intervals.items()
        },
        # Whether each level reads below δ: the bounded test, calibrated where `critical` is.
        "below": below,
        "calibrated": critical is not None,
        "within_sesoi": {c: within_sesoi(lo, hi, sesoi) for c, (lo, hi) in intervals.items()},
        # Readings beside the pass rule (registered-decisions.md): a supported cell whose lower
        # bound also clears the SESOI.
        "meaningful": {c: meaningful(lo, sesoi) for c, (lo, hi) in intervals.items()},
    }


def detectable_effects(sensitivity: Mapping[str, Any]) -> dict[str, dict[str, dict[float, float]]]:
    """The registered bounds, by hypothesis and cell, read from a `decomposition_sensitivity.py`
    artifact so no figure is transcribed. Only H1 cells are simulated there; an H2 cell's bound
    is registered the same way once its cells exist.
    """
    return {
        "H1": {
            org: by_level({c: v["minimum_detectable_effect"] for c, v in cell["by_level"].items()})
            for org, cell in sensitivity["cells"].items()
        }
    }


def _cell(
    results: Mapping[str, Sequence[Mapping[str, Any]]],
    trained: str,
    window: str,
    seed: int | None,
    *,
    condition: str = "adapter",
) -> Sequence[Mapping[str, Any]]:
    key = run_id(EvalRun(f"{condition}:{trained}", window, seed))
    if key not in results:
        raise ValueError(_unscored(key, f"the {trained} {condition}", window))
    return results[key]


def _unscored(key: str, arm: str, window: str) -> str:
    """The refusal for an arm the results lack: its key, and what was not scored where."""
    return f"no results for {key!r}: {arm} was not scored on {window}"


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
    seed: int | None,
    metric: str = "exact_match",
    condition: str = "adapter",
) -> list[list[Cluster]]:
    """Own half against sibling half, one cluster list per evaluated half.

    `condition` names what was conditioned on each half: its adapter, or a retrieval pool.
    """
    first, second = halves(org)
    return _disjoint(
        [
            to_clusters(
                _cell(results, window, window, seed, condition=condition),
                _cell(results, sibling, window, seed, condition=condition),
                metric=metric,
            )
            for window, sibling in ((first, second), (second, first))
        ]
    )


def paired_clusters(
    results: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    org: str,
    treatment: Callable[[str], str],
    control: Callable[[str], str],
    metric: str = "exact_match",
    missing: Callable[[str, str], str] = lambda key, window: f"no results for {key!r} on {window}",
) -> list[list[Cluster]]:
    """Two arms on each evaluated half, keyed by `treatment(half)` and `control(half)`, one
    cluster list per half, refusing a change in both halves; `missing(key, half)` words the
    refusal of an arm not scored."""
    per_half = []
    for window in halves(org):
        for key in (treatment(window), control(window)):
            if key not in results:
                raise ValueError(missing(key, window))
        per_half.append(
            to_clusters(results[treatment(window)], results[control(window)], metric=metric)
        )
    return _disjoint(per_half)


def base_clusters(
    results: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    org: str,
    seed: int | None,
    metric: str = "exact_match",
    condition: str = "adapter",
) -> list[list[Cluster]]:
    """Each half's own arm against the base model on that half, one list per evaluated half."""
    return paired_clusters(
        results,
        org=org,
        treatment=lambda window: run_id(EvalRun(f"{condition}:{window}", window, seed)),
        control=lambda window: run_id(EvalRun("base", window, None)),
        metric=metric,
        missing=lambda key, window: _unscored(
            key,
            "the base model"
            if key == run_id(EvalRun("base", window, None))
            else f"the {window} {condition}",
            window,
        ),
    )


def organization_clusters(
    results: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    org: str,
    foreign: str,
    seed: int | None,
    metric: str = "exact_match",
    condition: str = "adapter",
) -> list[list[Cluster]]:
    """Sibling half against the foreign organization's halves, one list per evaluated half."""
    if foreign == org:
        raise ValueError(f"the comparator organization must be foreign to {org!r}")
    first, second = halves(org)
    return _disjoint(
        [
            to_clusters(
                _cell(results, sibling, window, seed, condition=condition),
                _mean_arm(
                    [
                        _cell(results, other, window, seed, condition=condition)
                        for other in halves(foreign)
                    ],
                    metric=metric,
                ),
                metric=metric,
            )
            for window, sibling in ((first, second), (second, first))
        ]
    )


def matched_clusters(
    results: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    org: str,
    source: str,
    seed: int | None,
    metric: str = "exact_match",
    condition: str = "adapter",
) -> list[list[Cluster]]:
    """Sibling half against the foreign adapter matched to that sibling, one list per evaluated
    half: the confirmatory H2 contrast (`foreign_arm`)."""
    if source == org:
        raise ValueError(f"the foreign source must be foreign to {org!r}")
    first, second = halves(org)
    sibling = {first: second, second: first}
    return paired_clusters(
        results,
        org=org,
        treatment=lambda w: run_id(EvalRun(f"{condition}:{sibling[w]}", w, seed)),
        control=lambda w: run_id(
            EvalRun(f"{condition}:{foreign_arm(source, sibling[w])}", w, seed)
        ),
        metric=metric,
        missing=lambda key, w: _unscored(key, "the matched foreign arm or sibling", w),
    )


class Restricted(Mapping[str, Sequence[Mapping[str, Any]]]):
    """`results` with each cell's rows filtered by `keep` when the cell is read, so only the
    cells a contrast reads are ever filtered."""

    def __init__(
        self,
        results: Mapping[str, Sequence[Mapping[str, Any]]],
        keep: Callable[[Mapping[str, Any]], bool],
    ) -> None:
        self._results = results
        self._keep = keep

    def __getitem__(self, key: str) -> list[Mapping[str, Any]]:
        return [row for row in self._results[key] if self._keep(row)]

    def __contains__(self, key: object) -> bool:
        return key in self._results

    def __iter__(self) -> Iterator[str]:
        return iter(self._results)

    def __len__(self) -> int:
        return len(self._results)


def _strata(per_seed: Sequence[list[list[Cluster]]]) -> list[list[list[Cluster]]]:
    """`[seed][half]` to the `[half][seed]` layout the stratified bootstrap reads."""
    return [[run[h] for run in per_seed] for h in range(len(per_seed[0]))]


def _hypothesis(cells: Mapping[str, Mapping[str, Any]], confidence: float) -> str:
    """Intersection-union over cells at one level: pass, bounded, or inconclusive."""
    if not cells:
        raise ValueError("a hypothesis with no confirmatory cells has no verdict")
    verdicts = [cell["verdicts"][confidence] for cell in cells.values()]
    if all(v == "supported" for v in verdicts):
        return "pass"
    if all(v == "bounded" for v in verdicts):
        return "bounded"
    return "inconclusive"


def holm_steps(
    per_hypothesis: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> tuple[dict[str, str], dict[str, float | None]]:
    """Holm's step-down over the hypotheses, and the level at which each one passed.

    Every hypothesis is first read at the strictest level. Those that pass leave the family,
    and the rest are read again at the next level, until a step passes nothing. A later step
    can only turn a verdict into a pass: bounded is an equivalence claim, read at the
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
    *,
    sesoi: float = SESOI,
) -> list[str]:
    """Passing cells that read below δ at the level the hypothesis passed at: the Table III legend's
    "below δ if every upper bound is". A cell that records its bounded test (`read_intervals`'s
    `below`, calibrated where its critical p is) is read by it; otherwise its upper bound is
    compared with `sesoi`."""
    return sorted(
        f"{name}:{org}"
        for name, cells in per_hypothesis.items()
        if passed_at[name] is not None
        for org, cell in cells.items()
        if (
            cell["below"][passed_at[name]]
            if "below" in cell
            else cell["intervals"][passed_at[name]]["high"] < sesoi
        )
    )


def joint_reading(
    drawn: tuple[dict[str, float], dict[str, list[float]]],
    *,
    foreign: str,
    role: str,
    population: str,
) -> dict[str, Any]:
    """H1 and H2 read jointly on shared draws: the share of draws above zero for each and in
    each sign quadrant, and the summed contrast; on the C++ population also H1 restricted there,
    reported only."""
    estimates, draws = drawn
    pairs = list(zip(draws["H1"], draws["H2"], strict=True))
    low, high = percentile_interval([a + b for a, b in pairs], SUMMED_CONFIDENCE)
    joint: dict[str, Any] = {
        "h2": {"foreign": foreign, "role": role},
        "population": population,
        "above_zero": {
            name: sum(1 for d in draws[name] if d > 0) / len(draws[name]) for name in ("H1", "H2")
        },
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
    if population == H2_POPULATION:
        h1_low, h1_high = percentile_interval(draws["H1"], SUMMED_CONFIDENCE)
        joint["h1_on_cpp"] = {
            "estimate": estimates["H1"],
            "low": h1_low,
            "high": h1_high,
            "confidence": SUMMED_CONFIDENCE,
        }
    return joint


def decomposition_gate(
    results: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    admitted: Iterable[str],
    seeds: Sequence[int],
    metric: str = "exact_match",
    bootstrap_seed: int,
    resamples: int = 10_000,
    estimator: Estimator = paired_difference,
    detectable: Mapping[str, Mapping[str, Mapping[float, float]]],
) -> dict[str, Any]:
    """The registered verdicts, their reading, and every cell behind them.

    Within an organization all of its contrasts are bootstrapped on the same draws, so the
    share of draws in which each is above zero, and their sum, are read jointly. A confirmatory
    H2 cell reads its organization's C++ examples against the matched foreign arm
    (`matched_clusters`); its H1 verdict stays on every example, and its joint reading is taken on
    the C++ examples, where both contrasts share one example set. The cells are
    `registered_design(admitted)`, so Qt's foreign source is the registered one, never an argument.
    `detectable` holds each cell's registered bound by Holm level (`detectable_effects`); a
    confirmatory cell without one at every level the design reads is refused, so no bound is
    chosen after the data. It reads single-partition development pilots
    (`scripts/decomposition_pilot.py`). The confirmatory test-window reads over partitions, H2's
    included, are `confirmatory_gate`'s.
    """
    if resamples < MIN_RESAMPLES:
        raise ValueError(f"at least {MIN_RESAMPLES} resamples, got {resamples}")
    sesoi = _sesoi_of(metric)
    _require_registered_seeds(seeds)
    admitted = tuple(admitted)
    if ADMITTED_ORGANIZATIONS is not None and set(admitted) != set(ADMITTED_ORGANIZATIONS):
        raise ValueError(
            f"admitted {sorted(admitted)} is not the frozen set {ADMITTED_ORGANIZATIONS}"
        )
    cells = registered_design(admitted)
    hypotheses = confirmatory(cells)
    levels = holm_levels(len(hypotheses))
    bounds = {
        name: {org: by_level(levels_) for org, levels_ in cells_.items()}
        for name, cells_ in detectable.items()
    }
    for name, units in hypotheses.items():
        for unit in units:
            org = unit if name == "H1" else unit[0]
            registered = bounds.get(name, {}).get(org, {})
            missing = [c for c in levels if not valid_bound(registered.get(level_key(c)))]
            if missing:
                raise ValueError(f"no registered detectable effect for {name}:{org} at {missing}")

    h1_orgs = set(cells["H1"])
    h2_pairs = {org: (foreign, "confirmatory") for org, foreign in cells["H2"]}
    for org, foreign in cells["exploratory_h2"]:
        h2_pairs.setdefault(org, (foreign, "exploratory"))
    cpp = Restricted(results, is_cpp)

    per_org: dict[str, dict[str, Any]] = {"H1": {}, "H2": {}}
    joint: dict[str, dict[str, Any]] = {}
    for org in sorted(h1_orgs | set(h2_pairs)):
        # Each contrast is keyed once by the population it is read on; the contrasts of one
        # population are bootstrapped together on the same draws.
        by_population: dict[str, dict[str, list[list[list[Cluster]]]]] = {}
        roles: dict[str, str] = {}
        if org in h1_orgs:
            roles["H1"] = "confirmatory"
            by_population.setdefault("all", {})["H1"] = _strata(
                [project_clusters(results, org=org, seed=s, metric=metric) for s in seeds]
            )
        if org in h2_pairs:
            foreign, roles["H2"] = h2_pairs[org]
            if roles["H2"] == "confirmatory":
                on_cpp = by_population.setdefault(H2_POPULATION, {})
                on_cpp["H2"] = _strata(
                    [
                        matched_clusters(cpp, org=org, source=foreign, seed=s, metric=metric)
                        for s in seeds
                    ]
                )
                # H1 on the same C++ examples, for the joint reading only; its verdict is on all.
                on_cpp["H1"] = _strata(
                    [project_clusters(cpp, org=org, seed=s, metric=metric) for s in seeds]
                )
            else:
                try:
                    by_population.setdefault("all", {})["H2"] = _strata(
                        [
                            organization_clusters(
                                results, org=org, foreign=foreign, seed=s, metric=metric
                            )
                            for s in seeds
                        ]
                    )
                except ValueError as error:
                    # An exploratory cell never decides a verdict, so it cannot withhold one either.
                    per_org["H2"][org] = {
                        "role": "exploratory",
                        "foreign": foreign,
                        "error": str(error),
                    }
        verdict_population = {
            name: H2_POPULATION if name == "H2" and role == "confirmatory" else "all"
            for name, role in roles.items()
        }
        drawn = {
            population: stratified_crossed_draws(
                contrasts, seed=bootstrap_seed, resamples=resamples, estimator=estimator
            )
            for population, contrasts in by_population.items()
        }
        for name, population in verdict_population.items():
            if name not in by_population.get(population, {}):
                continue  # an exploratory H2 cell that could not be built, recorded above
            estimates, draws = drawn[population]
            intervals = {c: percentile_interval(draws[name], c) for c in levels}
            per_org[name][org] = {
                "role": roles[name],
                "foreign": h2_pairs[org][0] if name == "H2" else None,
                "population": population,
                "estimate": estimates[name],
                # Bounds are registered per confirmatory cell; an exploratory cell shares its
                # organization with no registered pair, so it can be supported, never bounded.
                **read_intervals(
                    intervals,
                    bounds.get(name, {}).get(org, {}) if roles[name] == "confirmatory" else {},
                    sesoi=sesoi,
                ),
                "clusters_per_half": [len(runs[0]) for runs in by_population[population][name]],
                "seeds": len(seeds),
            }
        if "H2" in verdict_population:
            population = verdict_population["H2"]
            if set(by_population.get(population, {})) == {"H1", "H2"}:
                joint[org] = joint_reading(
                    drawn[population],
                    foreign=h2_pairs[org][0],
                    role=roles["H2"],
                    population=population,
                )

    tested = {
        name: {o: cell for o, cell in per_org[name].items() if cell["role"] == "confirmatory"}
        for name in hypotheses
    }
    verdicts, passed_at = holm_steps(tested)
    mixed = verdicts["H1"] != "pass" and is_mixed(tested["H1"], levels[0])
    return {
        "design": cells,
        "verdicts": verdicts,
        "passed_at": passed_at,
        "reading": reading(verdicts["H1"], verdicts.get("H2"), mixed=mixed),
        "mixed": mixed,
        "below_sesoi": below_sesoi(tested, passed_at, sesoi=sesoi),
        "per_org": per_org,
        "joint": joint,
        "metric": metric,
        "sesoi": sesoi,
        "detectable": {n: {o: dict(b) for o, b in c.items()} for n, c in bounds.items()},
        "holm_levels": levels,
    }


def row_path(row: Mapping[str, Any]) -> str:
    """The file an example was drawn from.

    Rows scored before `evaluate` recorded the path carry it only in the example id,
    `org:change_id:path:patch_set:start`, so it is read from there.
    """
    if row.get("path"):
        return str(row["path"])
    parts = str(row["id"]).split(":", 2)
    if len(parts) < 3:
        raise ValueError(f"example id {row['id']!r} carries no path")
    path, _, _ = parts[2].rpartition(":")
    path, _, _ = path.rpartition(":")
    if not path:
        raise ValueError(f"example id {row['id']!r} carries no path")
    return path


def is_cpp(row: Mapping[str, Any]) -> bool:
    """Whether an example comes from a C++ source or header file."""
    return suffix(row_path(row)) in CPP_SUFFIXES


def whole_hunk_supplement(
    results: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    admitted: Iterable[str],
    seeds: Sequence[int],
    metric: str = "exact_match",
    bootstrap_seed: int,
    resamples: int = 10_000,
    estimator: Estimator = paired_difference,
) -> dict[str, Any]:
    """Each confirmatory H2 cell on every example, not only its C++ ones: reported, never tested.

    The registered H2 population is C++ (`H2_POPULATION`); the same matched contrast over the
    whole hunk set is reported beside it, with the share of each evaluated half that is C++. A
    restricted population estimates a different quantity, so this carries no verdict; a cell
    that cannot be built is reported with its error.
    """
    if resamples < MIN_RESAMPLES:
        raise ValueError(f"at least {MIN_RESAMPLES} resamples, got {resamples}")
    _require_registered_seeds(seeds)
    spec = registered_design(admitted)
    if not spec["H2"]:
        return {
            "design": spec,
            "role": "supplementary",
            "cells": {},
            "note": "no confirmatory H2 cell among the admitted organizations",
        }
    cells: dict[str, Any] = {}
    for org, source in spec["H2"]:
        cells[org] = {
            "foreign": source,
            **_supplement_cell(
                lambda org=org, source=source: [
                    matched_clusters(results, org=org, source=source, seed=s, metric=metric)
                    for s in seeds
                ],
                bootstrap_seed=bootstrap_seed,
                resamples=resamples,
                estimator=estimator,
            ),
        }
        if "error" not in cells[org]:
            try:
                cells[org]["cpp_share"] = {
                    window: _share(results[_key(org, window, seeds[0])], is_cpp)
                    for window in halves(org)
                }
            except ValueError as error:
                cells[org]["cpp_share_error"] = str(error)
    return {"design": spec, "role": "supplementary", "cells": cells}


def _key(org: str, window: str, seed: int) -> str:
    """One scored cell of a window, for counting what the window holds."""
    sibling = next(h for h in halves(org) if h != window)
    return run_id(EvalRun(f"adapter:{sibling}", window, seed))


def _share(rows: Sequence[Mapping[str, Any]], keep: Callable[[Mapping[str, Any]], bool]) -> float:
    return sum(keep(r) for r in rows) / len(rows) if rows else 0.0


def _supplement_cell(
    per_seed: Callable[[], list[list[list[Cluster]]]],
    *,
    bootstrap_seed: int,
    resamples: int,
    estimator: Estimator,
) -> dict[str, Any]:
    """One supplementary contrast on a restricted population: estimate and 95% interval, or the
    error building or bootstrapping its clusters raised, since a supplement withholds nothing."""
    try:
        strata = _strata(per_seed())
        estimates, draws = stratified_crossed_draws(
            {"cell": strata}, seed=bootstrap_seed, resamples=resamples, estimator=estimator
        )
    except ValueError as error:
        return {"error": str(error)}
    low, high = percentile_interval(draws["cell"], SUMMED_CONFIDENCE)
    return {
        "estimate": estimates["cell"],
        "low": low,
        "high": high,
        "confidence": SUMMED_CONFIDENCE,
        "clusters_per_half": [len(runs[0]) for runs in strata],
    }


def matched_suffix(split_criteria_artifact: Mapping[str, Any]) -> str:
    """The file suffix both halves of an organization hold most of: the one whose smaller count
    across the two halves' training rows is largest, read from its `scripts/split_criteria.py`
    artifact; ties break by suffix."""
    first, second = (h["suffixes"] for h in split_criteria_artifact["halves"])
    both = set(first) & set(second)
    if not both:
        raise ValueError("the halves share no file suffix")
    return min(both, key=lambda k: (-min(first[k], second[k]), k))


def file_type_supplement(
    results: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    admitted: Iterable[str],
    split_criteria: Mapping[str, Mapping[str, Any]],
    seeds: Sequence[int],
    metric: str = "exact_match",
    bootstrap_seed: int,
    resamples: int = 10_000,
    estimator: Estimator = paired_difference,
) -> dict[str, Any]:
    """H1 restricted to one file type per organization: a supplementary estimand, never tested.

    Halves can differ in file-type mix (OpenStack's split 40% reStructuredText against 62%
    Python), so the half-split contrast is recomputed on one file type per organization, where
    the halves cannot differ in it. The type is derived here from each organization's
    split-criteria artifact (`matched_suffix`), never passed in.
    """
    if resamples < MIN_RESAMPLES:
        raise ValueError(f"at least {MIN_RESAMPLES} resamples, got {resamples}")
    _require_registered_seeds(seeds)
    spec = registered_design(admitted)
    missing = [org for org in spec["H1"] if org not in split_criteria]
    if missing:
        raise ValueError(f"no split-criteria artifact for {missing}")
    suffixes = {org: matched_suffix(split_criteria[org]) for org in spec["H1"]}
    cells: dict[str, Any] = {}
    for org in spec["H1"]:

        def keep(row: Mapping[str, Any], wanted: str = suffixes[org]) -> bool:
            return suffix(row_path(row)) == wanted

        restricted = Restricted(results, keep)
        cells[org] = {
            "suffix": suffixes[org],
            **_supplement_cell(
                lambda org=org, restricted=restricted: [
                    project_clusters(restricted, org=org, seed=s, metric=metric) for s in seeds
                ],
                bootstrap_seed=bootstrap_seed,
                resamples=resamples,
                estimator=estimator,
            ),
        }
        if "error" not in cells[org]:
            cells[org]["share"] = {
                window: _share(results[_key(org, window, seeds[0])], keep) for window in halves(org)
            }
    return {"design": spec, "role": "supplementary", "cells": cells}
