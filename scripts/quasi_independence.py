"""Does the lag distribution hold still across creation cohorts?

Lynden-Bell's estimate of a right-truncated distribution requires the variable and its
truncation limit to be quasi-independent. Here the truncation limit is a deterministic
decreasing function of creation month, so this is the assumption that review latency is
stationary over the eighteen months the corpus spans.

The obvious diagnostic is biased and should not be used. Comparing P(lag <= 1) across
cohorts looks damning -- OpenStack rises from 0.667 in the oldest cohort to 0.956 in a
recent one -- but each cohort's rate is conditional on that cohort's own horizon, so a
short-horizon cohort reports P(lag <= 1 | lag <= h) rather than P(lag <= 1). Simulating from
a single STATIONARY law with the real cohort sizes reproduces most of that slope, 0.753 to
0.904, out of truncation alone.

Three things are reported instead. The conditional Kendall tau of Tsai (1990), which is
restricted to pairs whose order the truncation could not have hidden and is therefore the
diagnostic that survives the bias above; a goodness-of-fit statistic calibrated against its
own null by simulation, because `censoring.py` compares a MAXIMUM over lags and no
single-comparison band is the right yardstick for it; and a refit on recent cohorts only,
which trades the identified tail for cohorts resembling the windows being estimated.

Run: uv run --no-active python scripts/quasi_independence.py
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from censoring import (  # noqa: E402
    WINDOWS,
    Observation,
    example_bearing,
    last_collected,
    longest_horizon_cohort,
    lynden_bell,
    month_index,
    observations,
    window_capture,
)

RESULTS = Path("datasets/results/quasi-independence.json")
RECENT_COHORTS = 6
RESAMPLES = 400
TRIALS = 300
SEED = 17


def conditional_tau(obs: list[Observation]) -> tuple[float, int]:
    """Tsai's conditional Kendall tau. Zero under quasi-independence.

    A pair is comparable only where the truncation could not have hidden either order:
    both lags must be at most the smaller of the two horizons. Among those, concordance
    between lag and horizon is what a trend in latency across cohorts shows up as, because
    horizon runs backwards with creation month.
    """
    concordant = discordant = comparable = 0
    for i, a in enumerate(obs):
        for b in obs[i + 1 :]:
            if max(a.lag, b.lag) > min(a.horizon, b.horizon):
                continue
            if a.lag == b.lag or a.horizon == b.horizon:
                comparable += 1
                continue
            comparable += 1
            same = (a.lag - b.lag) * (a.horizon - b.horizon) > 0
            concordant += same
            discordant += not same
    if not comparable:
        return 0.0, 0
    return (concordant - discordant) / comparable, comparable


def tau_interval(obs: list[Observation], *, resamples: int, seed: int) -> dict[str, float]:
    rng = random.Random(seed)
    n = len(obs)
    draws = sorted(
        conditional_tau([obs[rng.randrange(n)] for _ in range(n)])[0] for _ in range(resamples)
    )
    return {"low": draws[round(0.025 * resamples)], "high": draws[round(0.975 * resamples) - 1]}


def calibrated_fit_check(obs: list[Observation], *, trials: int, seed: int) -> dict[str, float]:
    """How unusual is the fit's gap against the longest-horizon cohort, if the model is true?

    Simulates from the fitted law, held stationary across cohorts, with the real cohort sizes
    and horizons, and reads the null distribution of the same maximum off the simulation. A
    binomial band would answer a question nobody asked: the statistic maximises over thirteen
    correlated lags, so its null is much wider than any one of them.
    """
    rng = random.Random(seed)
    cdf = lynden_bell(obs)
    pmf = [cdf[0]] + [cdf[t] - cdf[t - 1] for t in range(1, max(cdf) + 1)]
    lags = list(range(len(pmf)))
    sizes = Counter(o.horizon for o in obs)

    def gap(sample: list[Observation]) -> float:
        fit = lynden_bell(sample)
        return max(abs(fit.get(t, 1.0) - e) for t, e in longest_horizon_cohort(sample).items())

    draws = []
    for _ in range(trials):
        sim: list[Observation] = []
        for horizon, n in sizes.items():
            kept = 0
            while kept < n:
                drawn = rng.choices(lags, weights=pmf)[0]
                if drawn <= horizon:
                    sim.append(Observation(drawn, horizon))
                    kept += 1
        draws.append(gap(sim))
    draws.sort()
    observed = gap(obs)
    return {
        "observed": observed,
        "null_median": draws[trials // 2],
        "null_p95": draws[round(0.95 * trials) - 1],
        "p_value": sum(1 for d in draws if d >= observed) / trials,
    }


def observable_rate(obs: list[Observation], lag: int) -> dict[str, float]:
    """P(lag <= k) per cohort, using only cohorts that could have observed it."""
    per: dict[int, list[int]] = {}
    for o in obs:
        if o.horizon >= lag:
            per.setdefault(o.horizon, []).append(o.lag <= lag)
    return {str(h): sum(v) / len(v) for h, v in sorted(per.items(), reverse=True) if len(v) >= 30}


def main() -> None:
    report: dict[str, dict] = {}
    for org in ("openstack", "qt"):
        collected_through = last_collected(org)
        end = month_index(f"{collected_through}-01")
        obs = observations(org, end, only=example_bearing(org))
        # Subsample for the tau: it is O(n^2) in pairs and the estimate is stable well
        # below the full sample.
        rng = random.Random(SEED)
        sample = rng.sample(obs, min(1200, len(obs)))
        tau, comparable = conditional_tau(sample)
        interval = tau_interval(sample, resamples=RESAMPLES, seed=SEED)

        fit = calibrated_fit_check(obs, trials=TRIALS, seed=SEED)
        pooled = lynden_bell(obs)
        recent = [o for o in obs if o.horizon < RECENT_COHORTS]
        recent_fit = lynden_bell(recent) if len(recent) >= 100 else {}

        print(f"\n=== {org} ===")
        print(
            f"conditional Kendall tau {tau:+.4f} [{interval['low']:+.4f}, {interval['high']:+.4f}] "
            f"over {comparable} comparable pairs of {len(sample)} sampled observations"
        )
        rejected = not (interval["low"] <= 0 <= interval["high"])
        print(f"  quasi-independence {'REJECTED' if rejected else 'not rejected'}")
        print(
            f"goodness of fit: max gap {fit['observed']:.4f} against a null median "
            f"{fit['null_median']:.4f} and 95th percentile {fit['null_p95']:.4f}, "
            f"p = {fit['p_value']:.3f}"
        )
        print(
            "  the model is "
            + ("rejected" if fit["p_value"] < 0.05 else "not rejected")
            + " by its own simulated null"
        )
        print("P(lag <= 1) by cohort horizon (conditional on each horizon, so biased upward):")
        rates = observable_rate(obs, 1)
        print("  " + "  ".join(f"h={h}:{r:.3f}" for h, r in rates.items()))

        row: dict[str, object] = {
            "conditional_kendall_tau": round(tau, 5),
            "tau_interval": {k: round(v, 5) for k, v in interval.items()},
            "comparable_pairs": comparable,
            "sampled": len(sample),
            "goodness_of_fit": {k: round(v, 5) for k, v in fit.items()},
            "p_lag_le_1_by_horizon": {k: round(v, 4) for k, v in rates.items()},
        }
        if recent_fit:
            print(f"refit on the {RECENT_COHORTS} most recent cohorts ({len(recent)} changes):")
            for name in ("dev",):
                pooled_w = window_capture(pooled, *WINDOWS[name], collected_through)
                recent_w = window_capture(recent_fit, *WINDOWS[name], collected_through)
                print(
                    f"  {name}: missing {100 * pooled_w['mean_missing']:.1f}% pooled, "
                    f"{100 * recent_w['mean_missing']:.1f}% recent-cohort"
                )
                row[f"{name}_missing_pooled"] = pooled_w["mean_missing"]
                row[f"{name}_missing_recent"] = recent_w["mean_missing"]
            test_pooled = window_capture(pooled, *WINDOWS["test"], "2027-02")
            test_recent = window_capture(recent_fit, *WINDOWS["test"], "2027-02")
            print(
                f"  test at acceptance: missing {100 * test_pooled['mean_missing']:.1f}% pooled, "
                f"{100 * test_recent['mean_missing']:.1f}% recent-cohort"
            )
            row["test_missing_pooled"] = test_pooled["mean_missing"]
            row["test_missing_recent"] = test_recent["mean_missing"]
            row["recent_changes"] = len(recent)
        report[org] = row

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nwrote {RESULTS}")


if __name__ == "__main__":
    main()
