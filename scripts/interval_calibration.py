"""False-positive rate of the registered pairs cluster bootstrap, under a true null.

The report states this rate, and until now it lived in a comment in `measure/stats.py`
that no artifact backs. A registered report cannot quote a calibration figure it cannot
re-derive, so this measures it against the same `cluster_bootstrap` the gate calls, and
writes the ladder out.

The null. Both arms score the same Bernoulli rate, so the true difference is zero, and
clusters carry the real dev window's example counts rather than a round number: the whole
point of a cluster bootstrap is that a change with many examples is one draw, and a
fabricated size distribution would measure a design the study does not run. The two arms
are drawn independently given the rate. Real arms share a change and correlate positively,
which narrows the difference's true spread; an independent null is therefore the harder
case for the interval, not the flattering one.

What the rate answers: how often a 95% interval excludes zero when nothing is there. The
gate reads one side at alpha 0.025, so its own error is about half of what this reports.

Run: uv run --no-active python scripts/interval_calibration.py --clusters 19
     (one cluster count per process; the ladder merges as each lands)
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path

from sphragis.measure.stats import Cluster, cluster_bootstrap
from sphragis.provenance import provenance_header

RESULTS = Path("datasets/results/interval-calibration.json")
WINDOW = Path("datasets/results/rq1-windows-qtfull-fp32.json")
ARM = "adapter:openstack|openstack|s1"
LADDER = (10, 19, 30, 45, 91, 200)
RATES = (0.05, 0.15, 0.50)
SENSITIVITY_CLUSTERS = 19
TRIALS = 1500
RESAMPLES = 2000
SEED = 20260919

parser = argparse.ArgumentParser()
parser.add_argument("--clusters", type=int, action="append", help="repeatable; default the ladder")
parser.add_argument("--rate", type=float, action="append", help="repeatable; the rate sweep")
parser.add_argument("--sensitivity-clusters", type=int, default=SENSITIVITY_CLUSTERS)
parser.add_argument("--trials", type=int, default=TRIALS)
parser.add_argument("--resamples", type=int, default=RESAMPLES)
parser.add_argument("--window", type=Path, default=WINDOW)
parser.add_argument("--arm", default=ARM)


def null_shape(window: Path, arm: str) -> tuple[list[int], float]:
    """Example counts per change, and the exact-match rate both arms will score.

    Taken from a real window so the sizes are the ones the gate meets. The rate is that
    window's own MATCHED-ADAPTER accuracy, not the base arm's: calibration is a property
    of the binary outcome's variance, which peaks near one half and collapses at the
    extremes, and the gate contrasts two adapter arms near 0.3 rather than a base arm
    near 0.05. Measured at the base rate the same interval looks far more conservative
    than it is where the gate reads it, which is why `--rate` sweeps that dependence.
    """
    payload = json.loads(window.read_text())
    rows = payload["results"][arm]
    sizes = Counter(row["change_id"] for row in rows)
    rate = sum(row["exact_match"] for row in rows) / len(rows)
    return sorted(sizes.values()), rate


def excludes_zero(
    sizes: list[int], rate: float, *, count: int, trials: int, resamples: int
) -> dict[str, float]:
    """Share of trials whose 95% interval excludes zero although the truth is zero."""
    rng = random.Random(SEED + count)
    excluded = 0
    for trial in range(trials):
        clusters = []
        for index in range(count):
            size = sizes[rng.randrange(len(sizes))]
            clusters.append(
                Cluster(
                    change_id=f"c{index}",
                    treatment=tuple(float(rng.random() < rate) for _ in range(size)),
                    control=tuple(float(rng.random() < rate) for _ in range(size)),
                )
            )
        interval = cluster_bootstrap(clusters, seed=SEED + trial, resamples=resamples)
        if interval["low"] > 0.0 or interval["high"] < 0.0:
            excluded += 1
    rate_out = excluded / trials
    return {
        "clusters": count,
        "false_positive_rate": rate_out,
        "standard_error": (rate_out * (1.0 - rate_out) / trials) ** 0.5,
        "trials": trials,
        "resamples": resamples,
    }


def main() -> None:
    args = parser.parse_args()
    sizes, rate = null_shape(args.window, args.arm)
    counts = args.clusters or list(LADDER)

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    report = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    report["provenance"] = provenance_header()
    report["null"] = {
        "window": str(args.window),
        "arm": args.arm,
        "exact_match_rate": rate,
        "changes_in_window": len(sizes),
        "largest_change": sizes[-1],
        "nominal_two_sided": 0.05,
    }
    ladder = report.setdefault("ladder", {})
    sweep = report.setdefault("rate_sensitivity", {})

    def measure(into: dict, key: str, count: int, at: float) -> None:
        row = excludes_zero(sizes, at, count=count, trials=args.trials, resamples=args.resamples)
        row["exact_match_rate"] = at
        into[key] = row
        print(
            f"{count:>4} clusters at rate {at:.3f}: {100 * row['false_positive_rate']:.1f}% "
            f"(+/- {100 * row['standard_error']:.1f}) against a nominal 5.0%"
        )
        RESULTS.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    for count in counts:
        measure(ladder, str(count), count, rate)
    for at in args.rate or list(RATES):
        measure(sweep, f"{at:.2f}", args.sensitivity_clusters, at)

    print(f"wrote {RESULTS}")


if __name__ == "__main__":
    main()
