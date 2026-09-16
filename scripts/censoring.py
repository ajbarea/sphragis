"""How much of each window's true cohort the collection boundary removes.

Snapshots select changes by *last update*; windows assign them by *creation*. A change
created inside a window whose last update falls after the final collected month is absent
from the corpus altogether, and the absence is not random: it is exactly the slow reviews.
The two organizations settle at different speeds, so a fixed boundary censors them by
different amounts, which makes it a confound in a comparison between them rather than
shared noise.

Nothing here is observable directly, because a truncated change leaves no record. What is
observable is the lag from creation to last update, for changes whose lag was short enough
to be seen. That is classical right truncation, and Lynden-Bell (1971) gives the
nonparametric maximum-likelihood estimate of the lag distribution from exactly this: pairs
(lag, horizon) observed only when lag <= horizon.

Run: uv run --no-active python scripts/censoring.py
"""

from __future__ import annotations

import gzip
import json
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from sphragis.corpus.cli import WINDOWS as _CLI_WINDOWS


def _month_before(bound: str) -> str:
    """The last month a half-open window covers: "2026-11-01" -> "2026-10"."""
    year, month = int(bound[:4]), int(bound[5:7])
    year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return f"{year:04d}-{month:02d}"


RESULTS = Path("datasets/results/censoring.json")
RAW = "datasets/gerrit/{org}/raw"
EXAMPLES = "datasets/gerrit/{org}/examples"
ORGS = ("openstack", "qt")

# Imported, not mirrored: these had been copied by hand, which is how a study parameter
# comes to have two values. cli states half-open day bounds; the months here are inclusive.
WINDOWS = {name: (start[:7], _month_before(end)) for name, (start, end) in _CLI_WINDOWS.items()}


# Derived per organization from the months actually built, never shared: Qt's examples stop
# at 2025-09 because the ban blocked 2025-10, and a shared constant gave every Qt observation
# a horizon one month too long, inflating every risk set with a cohort-month that could not
# have produced an observation and understating Qt's loss.
def last_collected(org: str) -> str:
    months = sorted(p.stem for p in Path(EXAMPLES.format(org=org)).glob("*.jsonl"))
    if not months:
        raise SystemExit(f"no built months under {EXAMPLES.format(org=org)}")
    return months[-1]


class Observation(NamedTuple):
    lag: int  # months from creation to last update
    horizon: int  # the largest lag this change could have shown and still been collected


def month_index(stamp: str) -> int:
    d = datetime.strptime(stamp[:7], "%Y-%m")
    return d.year * 12 + d.month - 1


def change_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    """Identity of one change, keyed so that siblings are not confused for each other.

    A Gerrit Change-Id is shared across cherry-picks and relation chains, so it names a
    family rather than a change: keying on it alone admitted every sibling of an
    example-bearing change, including the siblings that carry no example and settle faster.
    That inflated Qt's sample by 69% and biased its lag distribution short.
    """
    return (str(row["project"]), str(row["change_id"]), str(row["created"]))


def example_bearing(org: str) -> set[tuple[str, str, str]]:
    """Changes the corpus actually keeps: those with a comment anchored to a code hunk."""
    kept: set[tuple[str, str, str]] = set()
    for path in sorted(Path(EXAMPLES.format(org=org)).glob("*.jsonl")):
        with path.open() as handle:
            for line in handle:
                kept.add(change_key(json.loads(line)))
    return kept


def observations(
    org: str, last_collected: int, *, only: set[str] | None = None
) -> list[Observation]:
    """(lag, horizon) per change, optionally restricted to a set of change ids.

    The restriction is the point rather than a convenience. About 10% of merged changes
    carry a reviewer comment anchored to a code hunk, and only those become examples. They
    are the changes someone argued about, and they settle much more slowly: fitting the lag
    model to every merged change and applying it to the corpus understates the loss.
    """
    seen: set[str] = set()
    out: list[Observation] = []
    for path in sorted(Path(RAW.format(org=org)).glob("*.ndjson.gz")):
        with gzip.open(path, "rt") as handle:
            for line in handle:
                row = json.loads(line)
                if row["id"] in seen:
                    continue
                seen.add(row["id"])
                if only is not None and change_key(row) not in only:
                    continue
                created = month_index(row["created"])
                out.append(
                    Observation(month_index(row["updated"]) - created, last_collected - created)
                )
    return out


def lynden_bell(obs: list[Observation]) -> dict[int, float]:
    """P(lag <= t) for right-truncated (lag <= horizon) data.

    F is built downward from the largest observed lag: for each distinct lag value, the
    risk set is every observation that could have been seen at that value and was not seen
    beyond it. Returns the CDF at each distinct lag.
    """
    values = sorted({o.lag for o in obs}, reverse=True)
    survivor = 1.0
    cdf: dict[int, float] = {}
    for value in values:
        deaths = sum(1 for o in obs if o.lag == value)
        risk = sum(1 for o in obs if o.lag <= value <= o.horizon)
        cdf[value] = survivor
        survivor *= 1.0 - deaths / risk if risk else 1.0
    # cdf[v] currently holds F(v); fill the gaps so F is a step function over 0..max.
    full: dict[int, float] = {}
    carry = 0.0
    for t in range(0, max(values) + 1):
        if t in cdf:
            carry = cdf[t]
        full[t] = carry
    return full


def window_capture(cdf: dict[int, float], first: str, last: str, collected_through: str) -> dict:
    """Expected share of each cohort's true population that a collection boundary keeps."""
    end = month_index(f"{collected_through}-01")
    months = []
    lo, hi = month_index(f"{first}-01"), month_index(f"{last}-01")
    ceiling = max(cdf.values())
    for m in range(lo, hi + 1):
        horizon = end - m
        # Carry the CDF's own supremum past its support rather than a hardcoded 1.0. On an
        # untailed fit these agree; on a `with_tail` fit the hardcoded value reinstated
        # exactly the tail mass that was removed, and made capture jump back up above the
        # support -- understating the sensitivity row that exists to be a lower bound.
        keep = min(ceiling, cdf.get(horizon, ceiling)) if horizon >= 0 else 0.0
        months.append(
            {"month": f"{m // 12}-{m % 12 + 1:02d}", "horizon": horizon, "captured": keep}
        )
    mean = sum(m["captured"] for m in months) / len(months)
    return {"months": months, "mean_captured": mean, "mean_missing": 1.0 - mean}


def longest_horizon_cohort(obs: list[Observation]) -> dict[int, float]:
    """The empirical CDF of the one cohort old enough to be almost untruncated.

    Independent of Lynden-Bell and computed by counting, so agreement between the two is
    evidence the estimator is implemented right rather than merely plausible.
    """
    horizon = max(o.horizon for o in obs)
    cohort = [o.lag for o in obs if o.horizon == horizon]
    return {t: sum(1 for lag in cohort if lag <= t) / len(cohort) for t in range(horizon + 1)}


def with_tail(cdf: dict[int, float], tail_mass: float) -> dict[int, float]:
    """Lynden-Bell imposes F(max observed lag) = 1, because no cohort could see beyond it.

    Thirteen months of snapshots cannot observe a lag of fourteen. If real mass sits past
    the horizon, every F(t) here is too high and every missing fraction too low, so the
    headline numbers are lower bounds. This rescales F by an assumed surviving tail.
    """
    return {t: value * (1.0 - tail_mass) for t, value in cdf.items()}


def main() -> None:
    report: dict[str, dict] = {}

    for org in ORGS:
        collected_through = last_collected(org)
        end = month_index(f"{collected_through}-01")
        every = observations(org, end)
        obs = observations(org, end, only=example_bearing(org))
        cdf = lynden_bell(obs)
        every_cdf = lynden_bell(every)
        empirical = longest_horizon_cohort(obs)
        drift = max(abs(cdf.get(t, 1.0) - empirical[t]) for t in empirical)
        counts = Counter(o.lag for o in obs)
        # The gap against the longest-horizon cohort, reported without a verdict. A binomial
        # two-standard-error band is the wrong yardstick for it: the statistic is a MAXIMUM
        # over every lag, which is larger under the null than any single comparison, and the
        # band called OpenStack a clear failure on that basis. `quasi_independence.py`
        # calibrates it against its own null by simulation instead.
        reference = max(1, sum(1 for o in obs if o.horizon == max(o2.horizon for o2 in obs)))
        report[org] = {
            "collected_through": collected_through,
            "changes_merged": len(every),
            "changes_example_bearing": len(obs),
            "estimator_check": {
                "max_gap_vs_untruncated_cohort": round(drift, 5),
                "reference_cohort": reference,
                "calibrated_by": "scripts/quasi_independence.py",
            },
            "lag_cdf": {str(t): round(cdf[t], 5) for t in sorted(cdf)},
            "lag_cdf_all_merged": {str(t): round(every_cdf[t], 5) for t in sorted(every_cdf)},
            "lag_counts": {str(t): counts[t] for t in sorted(counts)},
            "windows": {},
        }
        share = 100 * len(obs) / len(every)
        print(f"\n=== {org}: {len(obs)} example-bearing of {len(every)} merged ({share:.1f}%) ===")
        print(
            f"collected through {collected_through}; Lynden-Bell against the untruncated "
            f"cohort (n={reference}), max gap {drift:.4f} "
            f"(calibrated in scripts/quasi_independence.py)"
        )
        print("lag  P(lag <= t)   all merged   observed")
        for t in sorted(cdf):
            if t <= 6 or t == max(cdf):
                print(f"{t:3d}  {cdf[t]:10.4f}   {every_cdf.get(t, 1.0):10.4f}   {counts[t]:7d}")

        for name in ("pilot", "train", "dev"):
            first, last = WINDOWS[name]
            w = window_capture(cdf, first, last, collected_through)
            report[org]["windows"][name] = w
            kept, lost = 100 * w["mean_captured"], 100 * w["mean_missing"]
            print(f"{name:6s} captured {kept:.1f}%  missing {lost:.1f}%")

        # The sealed test window is fetched after in-principle acceptance, not at its close.
        first, last = WINDOWS["test"]
        report[org]["test_by_fetch_month"] = {}
        print("test window, by the month it is finally fetched:")
        for fetch in ("2026-09", "2026-12", "2027-02", "2027-05"):
            w = window_capture(cdf, first, last, fetch)
            report[org]["test_by_fetch_month"][fetch] = w
            kept, lost = 100 * w["mean_captured"], 100 * w["mean_missing"]
            print(f"  fetched {fetch}: captured {kept:.1f}%  missing {lost:.1f}%")

        report[org]["tail_sensitivity"] = {}
        print("if mass sits beyond the twelve months any cohort could observe:")
        for tail in (0.005, 0.01, 0.02):
            shifted = with_tail(cdf, tail)
            dev = window_capture(shifted, *WINDOWS["dev"], collected_through)
            test = window_capture(shifted, *WINDOWS["test"], "2027-02")
            report[org]["tail_sensitivity"][str(tail)] = {
                "dev_missing": dev["mean_missing"],
                "test_missing_2027_02": test["mean_missing"],
            }
            print(
                f"  tail {100 * tail:.1f}%: dev missing {100 * dev['mean_missing']:.1f}%, "
                f"test missing {100 * test['mean_missing']:.1f}%"
            )

    gap = {
        name: abs(
            report["openstack"]["windows"][name]["mean_missing"]
            - report["qt"]["windows"][name]["mean_missing"]
        )
        for name in ("pilot", "train", "dev")
    }
    gap_test = {
        fetch: abs(
            report["openstack"]["test_by_fetch_month"][fetch]["mean_missing"]
            - report["qt"]["test_by_fetch_month"][fetch]["mean_missing"]
        )
        for fetch in report["openstack"]["test_by_fetch_month"]
    }
    report["differential_missing"] = {"windows": gap, "test_by_fetch_month": gap_test}

    print("\n=== the confound: |OpenStack missing - Qt missing| ===")
    for name, value in gap.items():
        print(f"{name:6s} {100 * value:.2f} points")
    for fetch, value in gap_test.items():
        print(f"test fetched {fetch}: {100 * value:.2f} points")

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nwrote {RESULTS}")


if __name__ == "__main__":
    main()
