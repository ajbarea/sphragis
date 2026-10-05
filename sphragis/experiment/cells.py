"""What makes an H1 cell readable: its level keys, its numbers, its draws, its test-window read.

Shared by the confirmatory gates (`decomposition.py`), the readings beside them (`across.py`) and
`scripts/partition_pilot.py`, so each checks a cell by the same rules. Standard library only.
"""

from __future__ import annotations

import math
import numbers
from collections.abc import Mapping, Sequence
from typing import Any

from sphragis.measure.stats import one_sided_alpha

# Below this the percentile ranks of neighbouring Holm levels can coincide.
MIN_RESAMPLES = 1_000
# A test-window read uses these and no others, so its interval cannot be redrawn until it
# clears zero (registered-decisions.md, "Reading the test window").
TEST_RESAMPLES = 10_000
TEST_BOOTSTRAP_SEED = 7


def level_key(confidence: Any) -> float:
    """A confidence level as a dictionary key: float noise rounded off, a JSON string key read."""
    if isinstance(confidence, bool):
        raise ValueError(f"{confidence!r} is not a confidence level")
    try:
        level = float(confidence)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{confidence!r} is not a confidence level") from error
    if not 0.0 < level < 1.0:
        raise ValueError(f"{confidence!r} is not a confidence level")
    return round(level, 9)


def by_level(values: Mapping[Any, Any]) -> dict[float, Any]:
    """A mapping keyed by confidence level, its keys passed through `level_key`.

    Two keys naming one level (0.975 and "0.975") are refused rather than one silently kept.
    """
    if not isinstance(values, Mapping):
        raise ValueError(f"expected a mapping by level, got {type(values).__name__}")
    out: dict[float, Any] = {}
    for c, v in values.items():
        key = level_key(c)
        if key in out:
            raise ValueError(f"two keys name level {key}")
        out[key] = v
    return out


def below_level(p: float, alpha: float) -> bool:
    """Whether a p-value is below a level, rounded so float noise cannot decide a tie.

    (1 - 0.975) / 2 is 0.012500000000000011, which would count a p-value of exactly 0.0125 as below.
    """
    return round(p, 12) < round(alpha, 12)


def is_real(value: Any) -> bool:
    """A finite number (a numpy scalar too), never a bool."""
    if not isinstance(value, numbers.Real) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def require_resamples(resamples: Any, *, alpha: float, org: str) -> None:
    """Refuse a resample count below `MIN_RESAMPLES` or whose product with `alpha` is not whole.

    Only a whole product (10,000 draws at 0.0125 or 0.025) makes a p-value fall below `alpha`
    exactly when the percentile interval's lower bound lies above zero.
    """
    if not isinstance(resamples, numbers.Integral) or isinstance(resamples, bool):
        raise ValueError(f"{org}: resamples must be a whole number, got {resamples!r}")
    if resamples < MIN_RESAMPLES:
        raise ValueError(f"{org}: at least {MIN_RESAMPLES} resamples, got {resamples}")
    excluded = resamples * alpha
    if abs(excluded - round(excluded)) > 1e-9 * max(1.0, excluded):
        raise ValueError(
            f"{org}: {resamples} resamples at one-sided {alpha:g} put the interval's "
            "bound between draws, so its p-value and its verdict could disagree"
        )


def require_readable(
    cells: Mapping[str, Mapping[str, Any]], *, confidence: float, fields: Sequence[str] = ()
) -> dict[str, dict[float, Any]]:
    """Refuse any H1 cell (`h1_over_partitions`) that cannot be read at `confidence`.

    Each must be filed under the organization it was computed for and hold `fields`, any
    estimate and bootstrap standard error finite; a resample
    count `require_resamples` accepts; a p-value in [0, 1]; a finite interval at that level, low
    at most high; and a p-value and interval that agree on whether the effect is above zero at
    the one-sided level, as draws from one bootstrap do. Returns each cell's intervals by level.
    """
    alpha = one_sided_alpha(confidence)
    level = level_key(confidence)
    normalized: dict[str, dict[float, Any]] = {}
    for org, cell in cells.items():
        if not isinstance(cell, Mapping):
            raise ValueError(f"{org}: the cell is not a mapping")
        absent = [
            f for f in ("org", *fields, "p_one_sided", "resamples", "intervals") if f not in cell
        ]
        if absent:
            raise ValueError(f"{org}: the cell has no {absent}")
        if cell["org"] != org:
            raise ValueError(f"the cell filed under {org} was computed for {cell['org']!r}")
        for name in ("estimate", "bootstrap_se"):
            if name in cell and not is_real(cell[name]):
                raise ValueError(f"{org}: {name} {cell[name]!r} is not a finite number")
        require_resamples(cell["resamples"], alpha=alpha, org=org)
        p = cell["p_one_sided"]
        if not (is_real(p) and 0.0 <= p <= 1.0):
            raise ValueError(f"{org}: p-value {p!r} is not in [0, 1]")
        try:
            normalized[org] = by_level(cell["intervals"])
        except ValueError as error:
            raise ValueError(f"{org}: intervals: {error}") from error
        interval = normalized[org].get(level)
        if not isinstance(interval, Mapping) or not {"low", "high"} <= set(interval):
            raise ValueError(f"{org}: no interval at {confidence}")
        low, high = interval["low"], interval["high"]
        if not (is_real(low) and is_real(high) and low <= high):
            raise ValueError(f"{org}: interval [{low!r}, {high!r}] at {confidence}")
        if below_level(p, alpha) != (low > 0.0):
            raise ValueError(
                f"{org}: p-value {p} and interval low {low} disagree at one-sided "
                f"{alpha:g}, so they are not from the same draws"
            )
    return normalized


def require_test_read(report: Mapping[str, Any], *, org: str) -> None:
    """Refuse a `partition_pilot.py` report unless it is a test-window read registered in advance.

    "Reading the test window" (registered-decisions.md): read on the test window with check 5
    passed; K from the organization's own development pilot (`k_from`), and bounds from its own
    simulation, run at that same K (`sensitivity`); at `TEST_RESAMPLES` draws and
    `TEST_BOOTSTRAP_SEED`. The pilot runs this on its report before writing it, and the gates on
    reading it, so both hold one rule.
    """
    absent = [
        f
        for f in ("window", "planted_convention", "k_from", "sensitivity", "runs", "resamples")
        if f not in report
    ]
    if "bootstrap_seed" not in report:
        absent.append("bootstrap_seed")
    if absent:
        raise ValueError(f"{org}: the report has no {absent}")
    if report["window"] != "test":
        raise ValueError(f"{org}: read on the {report['window']} window, not the test window")
    planted = report["planted_convention"]
    if not (isinstance(planted, Mapping) and planted.get("passed") is True):
        raise ValueError(f"{org}: outcome-neutral check 5 did not pass, so H1 is not read")
    for name, what in (("k_from", "K"), ("sensitivity", "bounds")):
        source = report[name]
        if not (isinstance(source, Mapping) and source.get("org") == org):
            raise ValueError(f"{org}: its {what} are not from its own {name}")
        if source.get("runs") != report["runs"]:
            raise ValueError(
                f"{org}: {name} is at K = {source.get('runs')!r}, the read at {report['runs']!r}"
            )
    if (report["resamples"], report["bootstrap_seed"]) != (TEST_RESAMPLES, TEST_BOOTSTRAP_SEED):
        raise ValueError(
            f"{org}: read at {report['resamples']} resamples, seed {report['bootstrap_seed']}; "
            f"registered {TEST_RESAMPLES} at {TEST_BOOTSTRAP_SEED}"
        )
