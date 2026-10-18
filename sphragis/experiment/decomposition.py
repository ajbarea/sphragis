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
from sphragis.experiment.across import across_organizations, partial_conjunction
from sphragis.experiment.cells import (
    MIN_RESAMPLES,
    REGISTERED_METRIC,
    REGISTERED_SPREAD_TARGET,
    by_level,
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


def metric_sesoi(metric: str) -> float | None:
    """A metric's registered SESOI, or None where none is registered.

    Exact match's is the cost-benefit constant; the per-token log-probability's is carried from it
    at the registered exchange rate, and its artifact must exist. Total log-probability, a
    sensitivity, has none.
    """
    if metric == "exact_match":
        return SESOI
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

# Every confirmatory H2 cell reads its organization's C++ examples (`CPP_SUFFIXES`), its foreign
# arm one adapter per evaluated half matched to that sibling's C++ count (`foreign_arm`).
H2_POPULATION = "cpp"


def design(
    admitted: Iterable[str], foreign_source: str | None = None, *, h2_withdrawn: bool = False
) -> dict[str, Any]:
    """The registered cells for whichever organizations are admitted.

    `admitted` must hold only names from `ORGANIZATIONS`, no duplicate, and always "openstack";
    input order does not matter, every result is ordered by `ORGANIZATIONS`. H1 is every admitted
    organization. H2 is confirmatory for `H2_PAIR`, both ways, when both are admitted; otherwise
    for `H2_ORGANIZATION` against `foreign_source`, when Qt is admitted and a source is given;
    `h2_withdrawn` (a foreign pool over check 4's threshold) leaves H2 with no confirmatory cell.
    Every other admitted organization gets one exploratory H2 cell instead, paired with the first
    other admitted organization in registration order: computed and reported, never part of a
    verdict.
    """
    ordered = _registered(admitted, ORGANIZATIONS, role="admitted")
    admitted_set = set(ordered)
    if "openstack" not in admitted_set:
        raise ValueError("openstack must always be admitted")
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
        "admitted": ordered,
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
FOREIGN_SOURCE: str | None = None

# Why H2 was withdrawn from the confirmatory family after its source was fixed (a foreign pool
# over check 4's threshold), set by the commit recording it; None while H2 stands.
H2_WITHDRAWN: str | None = None


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


def registered_read(org: str) -> dict[str, Any]:
    """What `org`'s test-window read is registered at: its levels, cell count and spread target.

    A Gerrit organization is read at the Holm levels of
    `registered_design(ADMITTED_ORGANIZATIONS)`, its bound from a simulation of that design's H1
    cells; a GitHub member at `REPLICATION_CONFIDENCE` alone, as one cell. Both take the bound at
    the registered spread target. Refused before the organizations are frozen, and for one
    outside them.
    """
    if org in REPLICATION_FAMILY:
        if REPLICATION_MEMBERS is None or org not in REPLICATION_MEMBERS:
            raise ValueError(f"{org} is not a frozen replication member")
        return _read_at([REPLICATION_CONFIDENCE], cells=1)
    if ADMITTED_ORGANIZATIONS is None or org not in ADMITTED_ORGANIZATIONS:
        raise ValueError(f"{org} is not among the frozen admitted organizations")
    return _gerrit_read(registered_design(ADMITTED_ORGANIZATIONS))


def registered_design(admitted: Iterable[str]) -> dict[str, Any]:
    """`design` for `admitted` with the registered `FOREIGN_SOURCE` and `H2_WITHDRAWN`: what every
    read uses. The source applies only with Qt admitted; with the pair admitted, a source other
    than Chromium is refused by `design`."""
    admitted = tuple(admitted)
    if FOREIGN_SOURCE is not None and FOREIGN_SOURCE not in FOREIGN_SOURCES:
        raise ValueError(
            f"registered FOREIGN_SOURCE {FOREIGN_SOURCE!r} is not in {FOREIGN_SOURCES}"
        )
    source = FOREIGN_SOURCE if H2_ORGANIZATION in admitted else None
    return design(admitted, source, h2_withdrawn=H2_WITHDRAWN is not None)


def _read_at(levels: list[float], *, cells: int) -> dict[str, Any]:
    return {
        "levels": levels,
        "cells": cells,
        "spread_target": REGISTERED_SPREAD_TARGET,
        "metric": REGISTERED_METRIC,
    }


def _sesoi_of(metric: str) -> float:
    """The SESOI a reading on `metric` compares against, refused where none is registered."""
    sesoi = metric_sesoi(metric)
    if sesoi is None:
        raise ValueError(f"{metric} has no registered SESOI")
    return sesoi


def _gerrit_read(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The registered read of every H1 cell in `spec`: its Holm levels and cell count."""
    return _read_at(holm_levels(len(confirmatory(spec))), cells=len(spec["H1"]))


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
        out[c] = {"power": p, "mc_se": se}
    return out


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


def _registered_bounds(
    org: str, cell: Mapping[str, Any], simulation: Any, expected: Mapping[str, Any]
) -> dict[float, float]:
    """`org`'s bound at each registered level, read from its simulation artifact.

    `require_test_read` has checked the simulation the cell records against the organization, K
    and the pilot's calibration; the artifact given must be that simulation, every bound a
    detectable effect, and each the bound the cell was computed under.
    """
    recorded = cell["sensitivity"]
    if not (
        isinstance(simulation, Mapping)
        and simulation.get("org") == recorded.get("org")
        and simulation.get("runs") == recorded.get("runs")
        and simulation.get("planned_changes") == recorded.get("planned_changes")
        and same_calibration(simulation.get("spread_targets"), recorded.get("spread_targets"))
    ):
        raise ValueError(f"{org}: the simulation given is not the one its cell was read under")
    try:
        bounds = sensitivity_bounds(
            simulation, expected["spread_target"], expected["levels"], cells=expected["cells"]
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


def h1_test_gate(
    reports: Mapping[str, Mapping[str, Any]],
    *,
    simulations: Mapping[str, Mapping[str, Any]],
    powers: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """H1's registered verdict over the admitted organizations' test-window reads.

    The organizations are `ADMITTED_ORGANIZATIONS`, never an argument; the gate refuses to run
    before they are frozen, and while H2 is confirmatory, since H2's test read over partitions is
    not built and Holm cannot be read without it. `reports` holds each admitted organization's
    `partition_pilot.py` test-window report and `simulations` its simulation artifact, both for
    exactly the admitted set; each report is checked by `require_readable` and by
    `require_test_read` against the registered read, and its bounds read from its simulation. H1
    is the intersection-union over the cells at the registered Holm levels (`holm_steps`).

    Beside the verdict, binding none: the readings across organizations, and each cell's realised
    test size against its simulation's projection ("Fetch horizon", registered-decisions.md).
    An organization in `below_projection` is reported with the simulation's power at its realised
    size, read from its `power_at_size.py` artifact in `powers`, which it must have.
    """
    if ADMITTED_ORGANIZATIONS is None:
        raise ValueError("the admitted organizations are not frozen yet")
    spec = registered_design(ADMITTED_ORGANIZATIONS)
    if "H2" in confirmatory(spec):
        raise ValueError("H2 is confirmatory, and its test read over partitions is not built")
    orgs = spec["H1"]
    for what, given in (("reports", reports), ("simulations", simulations)):
        _require_exactly(given, orgs, what=what)
    expected = _gerrit_read(spec)
    levels = expected["levels"]
    sesoi = _sesoi_of(expected["metric"])
    intervals = {
        c: require_readable(reports, confidence=c, fields=_CELL_FIELDS + ("bootstrap_se",))
        for c in levels
    }
    cells: dict[str, dict[str, Any]] = {}
    detectable: dict[str, dict[float, float]] = {}
    for org in orgs:
        report, simulation = reports[org], simulations[org]
        readings = require_test_read(report, org=org, expected=expected)
        detectable[org] = _registered_bounds(org, report, simulation, expected)
        at = {c: intervals[c][org][level_key(c)] for c in levels}
        cells[org] = {
            "estimate": report["estimate"],
            **read_intervals(
                {c: (i["low"], i["high"]) for c, i in at.items()}, detectable[org], sesoi=sesoi
            ),
            "p_one_sided": report["p_one_sided"],
            "size": _test_size(org, report, simulation, powers, detectable[org], expected["cells"]),
            # The registered sensitivities the report read (partition_pilot.py --without).
            "without": readings,
        }
    per_hypothesis = {"H1": cells}
    verdicts, passed_at = holm_steps(per_hypothesis)
    return {
        "design": spec,
        "verdicts": verdicts,
        "passed_at": passed_at,
        "reading": reading(verdicts["H1"], None),
        "below_sesoi": below_sesoi(per_hypothesis, passed_at, sesoi=sesoi),
        "cells": cells,
        "metric": expected["metric"],
        "sesoi": sesoi,
        "detectable": {"H1": detectable},
        "holm_levels": levels,
        "below_projection": _below_projection(cells, powers),
        "across": across_organizations(reports, confidence=levels[0]),
    }


def replication_gate(
    cells: Mapping[str, Mapping[str, Any]],
    *,
    simulations: Mapping[str, Mapping[str, Any]],
    powers: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Each frozen member's H1 verdict and the family's partial conjunction r.

    The members are `REPLICATION_MEMBERS`, never an argument, so none joins after Stage 1; the
    gate refuses to run before they are frozen. `cells` holds each member's
    `partition_pilot.py --replication` test-window report and no other organization (read back
    from JSON is accepted); each is checked by `require_readable` and by `require_test_read`
    against `registered_read`. `simulations` holds each member's partition-simulation artifact:
    the bound is read from it at the registered spread target, level and cell count, and must be
    the one the cell was computed under. It checks the artifacts as the scripts write them; that
    none was edited afterwards is the git history's to show. Each verdict is read from the cell's
    interval at `REPLICATION_CONFIDENCE`, and r at its one-sided level from p-values that must
    agree with the intervals. Each member's realised test size is reported against its
    simulation's projection, and one below it with its power at the realised size from `powers`,
    as `h1_test_gate` reports them.
    """
    if REPLICATION_MEMBERS is None:
        raise ValueError("the replication family's members are not frozen yet (Stage 1)")
    family = replication(REPLICATION_MEMBERS)
    for what, given in (("cells", cells), ("simulations", simulations)):
        _require_exactly(given, family["members"], what=what)
    level = level_key(REPLICATION_CONFIDENCE)
    intervals = require_readable(cells, confidence=REPLICATION_CONFIDENCE, fields=_CELL_FIELDS)
    read: dict[str, dict[str, Any]] = {}
    for org in family["members"]:
        cell, simulation = cells[org], simulations[org]
        expected = registered_read(org)
        readings = require_test_read(cell, org=org, expected=expected)
        bounds = _registered_bounds(org, cell, simulation, expected)
        bound = bounds[level]
        interval = intervals[org][level]
        read[org] = {
            "estimate": cell["estimate"],
            "interval": {"low": interval["low"], "high": interval["high"]},
            "bound": bound,
            "verdict": cell_verdict(
                interval["low"],
                interval["high"],
                margin=_sesoi_of(expected["metric"]) if valid_bound(bound) else None,
            ),
            "meaningful": meaningful(interval["low"], _sesoi_of(expected["metric"])),
            "p_one_sided": cell["p_one_sided"],
            "size": _test_size(org, cell, simulation, powers, bounds, expected["cells"]),
            # The registered sensitivities the report read (partition_pilot.py --without).
            "without": readings,
        }
    return {
        **family,
        "cells": read,
        "below_projection": _below_projection(read, powers),
        "partial_conjunction": partial_conjunction(
            {o: c["p_one_sided"] for o, c in read.items()},
            alpha=one_sided_alpha(REPLICATION_CONFIDENCE),
        )
        if read
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


def reading(h1: str, h2: str | None) -> str:
    """The pre-committed reading of one combination of hypothesis verdicts.

    `h2` is None under a design with no confirmatory H2 cell, where only H1 is confirmatory.
    """
    if h2 is None:
        return {"pass": "within-half", "bounded": "no within-half transfer"}.get(h1, "unresolved")
    return READINGS.get((h1, h2), "unresolved")


def holm_levels(hypotheses: int) -> list[float]:
    """Two-sided confidence levels Holm reads, strictest first: 1 - 2 * alpha / k, k = m..1."""
    if hypotheses < 1:
        raise ValueError("Holm needs at least one hypothesis")
    return [1.0 - 2.0 * FAMILY_ALPHA / k for k in range(hypotheses, 0, -1)]


def cell_verdict(low: float, high: float, *, margin: float | None) -> str:
    """Supported above zero, bounded below the equivalence margin, or inconclusive.

    Bounded is a one-sided equivalence test at `margin`, the SESOI (Lakens, Scheel and Isager,
    2018): its upper bound excludes every effect as large as the smallest of interest. `margin` is
    None for a cell without a registered sensitivity analysis, which can be supported but never
    bounded; the analysis is the power justification, not the margin (research log, 2026-10-14).
    """
    if low > 0.0:
        return "supported"
    if margin is not None and high < margin:
        return "bounded"
    return "inconclusive"


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
) -> dict[str, dict[float, Any]]:
    """A cell's interval at each level, its verdict, and the readings beside it. A level with a
    registered bound in `bounds` (keyed by `level_key`) is read bounded against the SESOI; a level
    without one is never read bounded."""
    return {
        "intervals": {c: {"low": lo, "high": hi} for c, (lo, hi) in intervals.items()},
        "verdicts": {
            c: cell_verdict(lo, hi, margin=sesoi if valid_bound(bounds.get(level_key(c))) else None)
            for c, (lo, hi) in intervals.items()
        },
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
    """Passing cells whose interval, at the level the hypothesis passed at, sits inside SESOI."""
    return sorted(
        f"{name}:{org}"
        for name, cells in per_hypothesis.items()
        if passed_at[name] is not None
        for org, cell in cells.items()
        if cell["intervals"][passed_at[name]]["high"] < sesoi
    )


def _joint(
    drawn: tuple[dict[str, float], dict[str, list[float]]],
    *,
    foreign: str,
    role: str,
    population: str,
) -> dict[str, Any]:
    """H1 and H2 read jointly on shared draws: the share of draws in each sign quadrant and the
    summed contrast; on the C++ population also H1 restricted there, reported only."""
    estimates, draws = drawn
    pairs = list(zip(draws["H1"], draws["H2"], strict=True))
    low, high = percentile_interval([a + b for a, b in pairs], SUMMED_CONFIDENCE)
    joint: dict[str, Any] = {
        "h2": {"foreign": foreign, "role": role},
        "population": population,
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
    `detectable` holds each cell's
    registered bound by Holm level (`detectable_effects`); a confirmatory cell without one at
    every level the design reads is refused, so no bound is chosen after the data.
    It reads single-partition development pilots (`scripts/decomposition_pilot.py`). The H2
    test-window read over partitions is not built yet; when it is, it goes through
    `require_test_read` as the H1 read does.
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
                joint[org] = _joint(
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
    return {
        "design": cells,
        "verdicts": verdicts,
        "passed_at": passed_at,
        "reading": reading(verdicts["H1"], verdicts.get("H2")),
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
