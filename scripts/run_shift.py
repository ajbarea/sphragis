"""How much run-wide shift a likelihood pilot carries beyond its change-by-run noise.

`power.averaged_truth` reads every within-change cross-product of an example's residual (its
own-minus-sibling contrast less its mean over runs) as noise shared by a change, so a shift the
whole run shares would be counted there and redrawn per change rather than per run. The mean
residual over a run's examples has variance per_example²/N + shared²·Σn²/N² from change noise
(N examples, n per change) plus that of any run-wide shift; the excess over the first two terms,
over the pilot's runs, estimates the run-wide part, with its one-sided upper bound at
`SIZING_CONFIDENCE` (chi-squared over the runs), beside the worst case in which all of the shared
part were run-wide. A shift favouring one half's adapter cancels in the run mean and is not
measured here; redrawn per change, it only widens the simulated interval.

    R=datasets/results
    uv run --no-sync --no-active python scripts/run_shift.py --org openstack \\
        --pilot $R/partition-pilot-openstack-likelihood.json \\
        --simulation $R/partition-sensitivity-openstack-rp1.0-logprob_per_token.json \\
        --out $R/run-shift-openstack-likelihood.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean, variance

from sphragis.experiment.decomposition import halves
from sphragis.experiment.partitions import SIZING_CONFIDENCE, sd_bound
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--org", required=True)
parser.add_argument("--pilot", type=Path, required=True, help="the likelihood pilot")
parser.add_argument("--simulation", type=Path, required=True, help="its simulation's noise")
parser.add_argument("--out", type=Path, required=True)


def run_wide(
    runs: list[tuple[dict, int]], *, pair: tuple[str, str], metric: str, noise: tuple[float, float]
) -> dict[str, float]:
    """The run mean's variance, the part change noise (shared, per example) implies, the rest."""
    contrast: dict[str, dict[int, float]] = defaultdict(dict)
    change_of: dict[str, str] = {}
    for results, seed in runs:
        for window, sibling in (pair, pair[::-1]):
            mine = {r["id"]: r for r in results[f"adapter:{window}|{window}|s{seed}"]}
            theirs = {r["id"]: r for r in results[f"adapter:{sibling}|{window}|s{seed}"]}
            for example, row in mine.items():
                contrast[example][seed] = float(row[metric]) - float(theirs[example][metric])
                change_of[example] = row["change_id"]
    seeds = [seed for _, seed in runs]
    residual = {e: {s: v - fmean(by.values()) for s, v in by.items()} for e, by in contrast.items()}
    run_mean = [fmean(residual[e][s] for e in residual) for s in seeds]
    examples = len(residual)
    squares = sum(n * n for n in Counter(change_of.values()).values())
    shared, per_example = noise
    observed = variance(run_mean)
    upper = sd_bound(run_mean, SIZING_CONFIDENCE, upper=True) ** 2
    from_noise = per_example**2 / examples + shared**2 * squares / examples**2
    weight = squares / examples**2
    return {
        "runs": len(seeds),
        "examples": examples,
        "changes": len(set(change_of.values())),
        "sum_squared_change_sizes": squares,
        "run_mean_variance": observed,
        "from_change_noise": from_noise,
        "run_wide_variance": observed - from_noise,
        "run_wide_share_of_shared": (observed - from_noise) / shared**2,
        "confidence": SIZING_CONFIDENCE,
        "run_mean_variance_upper": upper,
        "run_wide_variance_upper": upper - from_noise,
        "run_wide_share_of_shared_upper": (upper - from_noise) / shared**2,
        # What a run-wide shift inside the shared term moves onto a run's mean, at the upper
        # bound, against the worst case in which all of the shared part were run-wide.
        "misplaced_on_run_mean_upper": max(upper - from_noise, 0.0) * weight,
        "worst_case_on_run_mean": shared**2 * weight,
    }


def main() -> None:
    args = parser.parse_args()
    pilot = json.loads(args.pilot.read_text())
    simulation = json.loads(args.simulation.read_text())
    metric = simulation["metric"]
    if pilot.get("metric") != metric:
        raise SystemExit(f"{args.pilot} is on {pilot.get('metric')}, the simulation on {metric}")
    noise = simulation["change_by_run_noise"]
    runs = []
    for path in pilot["run_files"]:
        run = json.loads(Path(path).read_text())
        runs.append((run["results"], run["seeds"][0]))
    read = run_wide(
        runs,
        pair=halves(args.org),
        metric=metric,
        noise=(noise["shared_by_change"], noise["per_example"]),
    )
    out = {
        "org": args.org,
        "metric": metric,
        "pilot": str(args.pilot),
        "simulation": str(args.simulation),
        **read,
        "provenance": provenance_header(),
    }
    print(
        f"{args.org}: run-mean variance {read['run_mean_variance']:.3g}, "
        f"{read['from_change_noise']:.3g} from change noise, run-wide "
        f"{read['run_wide_variance']:.3g} ({read['run_wide_share_of_shared']:+.1%} of shared)"
    )
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
