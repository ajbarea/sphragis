"""An organization's K under the coverage rule, as the K source a test read takes (`--sizing`).

The reproducibility K the pilot sized (`partition_pilot.py`, `runs_needed` at the metric's
SESOI) sizes agreement between two aggregations, not the crossed interval's coverage. The rule
fixed in the research log on 2026-10-09, before the grid ran, takes the larger of it and the first
K in `partitions.K_GRID` at which a simulated null, at the 90% sizing bound, passes no more often
than nominal at every Holm level (`partitions.coverage_runs`). The grid is the
`partition_sensitivity.py` simulations of that pilot at those K (`RUNS=<K> NULL_ONLY=1`, and the
full simulation at the pilot's own K); every K up to the first that holds must be among them.

    R=datasets/results S=$R/partition-sensitivity-wikimedia-rp1.0-logprob_per_token
    uv run --no-sync --no-active python scripts/k_coverage.py \\
        --pilot $R/partition-pilot-wikimedia-likelihood.json --org wikimedia \\
        --grid $S.json $S-k*-null.json --out $R/k-coverage-wikimedia-likelihood.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sphragis.experiment.cells import K_COVERAGE, REGISTERED_SPREAD_TARGET
from sphragis.experiment.partitions import COVERAGE_NULL_TRIALS, coverage_runs
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--pilot", type=Path, required=True, help="the development pilot K came from")
parser.add_argument("--org", required=True)
parser.add_argument("--grid", type=Path, nargs="+", required=True, help="its simulations by K")
parser.add_argument("--out", type=Path, required=True)


def main() -> None:
    args = parser.parse_args()
    pilot = json.loads(args.pilot.read_text())
    grid = [(str(path), json.loads(path.read_text())) for path in args.grid]
    try:
        k = coverage_runs(pilot, grid, org=args.org, name=str(args.pilot))
    except (KeyError, TypeError, ValueError) as error:
        raise SystemExit(f"not sized: {error}") from error
    out = {
        "kind": K_COVERAGE,
        "org": args.org,
        "metric": pilot.get("metric", "exact_match"),
        "pilot": str(args.pilot),
        "spread_target": REGISTERED_SPREAD_TARGET,
        "null_trials": COVERAGE_NULL_TRIALS,
        **k,
        "provenance": provenance_header(),
    }
    for row in k["grid"]:
        print(
            f"K = {row['runs']}: null false positive {row['null_false_positive']}"
            f"{' within nominal' if row['within_nominal'] else ''}"
        )
    print(
        f"{args.org}: reproducibility K {k['reproducibility_runs']}, coverage K "
        f"{k['coverage_runs']}, K = {k['runs']}"
    )
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
