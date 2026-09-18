"""How large is the seed main effect: the shift a retrained seed gives every change at once?

Registration rule (ROADMAP, "Pending on a measurement"): three seeds if the seed main effect is at
or below 0.01, five if above, because that is where the crossed interval at three seeds stops
holding nominal (`datasets/results/crossed-coverage.json`).

Input is single-seed runs of one study at different seeds, merged exactly as
`scripts/crossed_reread.py` merges them. Per organization, the contrast at each seed is
matched minus mismatched, pooled. Two seeds' contrasts differ by the seed effect and by
seed-by-change noise; the second is measured directly, as the change-clustered bootstrap
variance of the per-example difference between the two seeds. The moment estimate is

    sigma_b^2 = (between-seed variance of the contrasts) - (mean pair noise variance) / 2,

each pair's noise variance covering two seeds' worth of seed-by-change terms. Negative values
are reported as they are and floored at zero only in the SD.

    uv run --no-sync --no-active python scripts/seed_effect.py \
        datasets/results/calibration-sym-0.json datasets/results/calibration-sym-0-s2.json \
        datasets/results/calibration-sym-0-s3.json --out datasets/results/seed-effect-sym-0.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import fmean, variance

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crossed_reread import merge  # noqa: E402

from sphragis.experiment.grid import EvalRun, run_id  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("runs", type=Path, nargs="+")
parser.add_argument("--resamples", type=int, default=10_000)
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--out", type=Path, required=True)


def per_example_contrast(results, org: str, other: str, seed: int) -> dict[str, tuple[str, float]]:
    matched = results[run_id(EvalRun(f"adapter:{org}", org, seed))]
    mismatched = {
        r["id"]: r["exact_match"] for r in results[run_id(EvalRun(f"adapter:{other}", org, seed))]
    }
    return {r["id"]: (r["change_id"], r["exact_match"] - mismatched[r["id"]]) for r in matched}


def pair_noise_variance(
    first: dict[str, tuple[str, float]],
    second: dict[str, tuple[str, float]],
    *,
    resamples: int,
    rng: random.Random,
) -> tuple[float, float]:
    """The shift between two seeds' contrasts, and its variance with no seed effect."""
    by_change: dict[str, list[float]] = defaultdict(list)
    for example, (change, value) in first.items():
        by_change[change].append(second[example][1] - value)
    clusters = list(by_change.values())
    shift = fmean(v for c in clusters for v in c)
    n = len(clusters)
    draws = []
    for _ in range(resamples):
        sample = [clusters[rng.randrange(n)] for _ in range(n)]
        draws.append(fmean(v for c in sample for v in c))
    return shift, variance(draws)


def main() -> None:
    args = parser.parse_args()
    results, seeds = merge(args.runs)
    if len(seeds) < 2:
        raise SystemExit("a seed effect needs at least two seeds")
    orgs = sorted({arm.split("|")[1] for arm in results if arm.startswith("base|")})
    rng = random.Random(args.seed)
    report: dict = {"runs": [str(p) for p in args.runs], "seeds": seeds, "organizations": {}}
    between, noise = [], []
    for org in orgs:
        other = next(o for o in orgs if o != org)
        contrasts = {s: per_example_contrast(results, org, other, s) for s in seeds}
        estimates = {s: fmean(v for _, v in contrasts[s].values()) for s in seeds}
        pairs = []
        for a, b in itertools.combinations(seeds, 2):
            shift, var = pair_noise_variance(
                contrasts[a], contrasts[b], resamples=args.resamples, rng=rng
            )
            pairs.append(
                {
                    "seeds": [a, b],
                    "shift": shift,
                    "noise_sd": var**0.5,
                    "z": shift / var**0.5 if var > 0 else None,
                }
            )
            noise.append(var)
        between.append(variance(list(estimates.values())))
        report["organizations"][org] = {"contrast_by_seed": estimates, "pairs": pairs}
        print(f"{org}: " + ", ".join(f"s{s} {e:+.4f}" for s, e in estimates.items()))
        for p in pairs:
            print(
                f"  s{p['seeds'][0]} vs s{p['seeds'][1]}: shift {p['shift']:+.4f}, "
                f"noise SD {p['noise_sd']:.4f}, z {p['z']:+.2f}"
            )
    moment = fmean(between) - fmean(noise) / 2
    report["between_seed_variance"] = fmean(between)
    report["pair_noise_variance"] = fmean(noise)
    report["sigma_b_squared"] = moment
    report["sigma_b"] = max(0.0, moment) ** 0.5
    print(f"between-seed variance {fmean(between):.6f}, half the pair noise {fmean(noise) / 2:.6f}")
    print(f"sigma_b^2 = {moment:+.6f}  ->  sigma_b = {report['sigma_b']:.4f}")
    args.out.write_text(json.dumps(report, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
