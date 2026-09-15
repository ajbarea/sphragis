"""Minimum detectable matched-minus-mismatched exact-match difference, from an RQ1 pilot.

Uses the pilot's real per-change outcomes as the variance model, simulates studies of the
given sizes under a sign-flip null (`sphragis.experiment.power`), and reports the smallest
difference the one-sided gate detects at 80% power. The input to Stage 1 section 5.

    python scripts/power_rq1.py datasets/results/rq1-pilot-equalized.json \
        --size openstack=880 --size qt=2400
"""

import argparse
import json
import time
from pathlib import Path

from sphragis.experiment.power import minimum_detectable_effect
from sphragis.experiment.runner import to_clusters

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("pilot", type=Path, help="rq1_pilot.py output JSON")
parser.add_argument(
    "--size", action="append", default=[], metavar="ORG=N", help="planned test-window changes"
)
parser.add_argument("--seed", type=int, default=11)
parser.add_argument("--trials", type=int, default=100)
parser.add_argument("--resamples", type=int, default=100)
parser.add_argument("--tolerance", type=float, default=0.02)
args = parser.parse_args()

pilot = json.loads(args.pilot.read_text())
results = pilot["results"]
seed = pilot["seeds"][0]
planned = {org: int(n) for org, n in (entry.split("=", 1) for entry in args.size)}
orgs = sorted(pilot["corpora"])
if len(orgs) != 2:
    raise SystemExit(f"expected two organizations, found {orgs}")

for org in orgs:
    other = next(o for o in orgs if o != org)
    clusters = to_clusters(
        results[f"adapter:{org}|{org}|s{seed}"], results[f"adapter:{other}|{org}|s{seed}"]
    )
    for n in sorted({len(clusters), 200, planned.get(org, len(clusters))}):
        started = time.time()
        mde = minimum_detectable_effect(
            clusters,
            seed=args.seed,
            n_changes=n,
            trials=args.trials,
            resamples=args.resamples,
            tolerance=args.tolerance,
        )
        print(
            f"{org}: pilot changes {len(clusters)}, simulated {n}: detectable difference "
            f"{mde.difference:+.4f} (lift {mde.lift:.3f}, reached {mde.reached_target}) "
            f"[{time.time() - started:.0f}s]",
            flush=True,
        )
print(
    f"settings: trials={args.trials} resamples={args.resamples} tolerance={args.tolerance} "
    f"seed={args.seed}; lifts below the tolerance are at the bisection's resolution"
)
