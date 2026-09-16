"""What the estimand choice costs, on a run that has already happened.

`gate_under_each_estimand` computes both at run time. This applies the same thing to a
results file already on disk, so the Stage 1 decision can be made against real numbers
from the pilots rather than against the synthetic case that separates them in the tests.

Run: uv run --no-active python scripts/estimands.py datasets/results/rq1-pilot-equalized.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sphragis.experiment.walk import gate_under_each_estimand

parser = argparse.ArgumentParser()
parser.add_argument("results", type=Path, nargs="+")
parser.add_argument("--metric", default="exact_match")
parser.add_argument("--bootstrap-seed", type=int, default=0)


def main() -> None:
    args = parser.parse_args()
    for path in args.results:
        payload: dict[str, Any] = json.loads(path.read_text())
        results = payload["results"]
        seeds = [int(s) for s in payload["seeds"]]
        orgs = sorted({key.split("|")[1] for key in results if key.startswith("base|")})

        outcome = gate_under_each_estimand(
            results,
            orgs=tuple(orgs),
            seeds=tuple(seeds),
            metric=args.metric,
            bootstrap_seed=args.bootstrap_seed,
        )
        print(f"\n=== {path.name}  metric={args.metric}  seeds={seeds} ===")
        print(f"{'org':<12}{'estimand':<18}{'estimate':>10}{'low':>10}{'high':>10}{'changes':>9}")
        for name, verdict in outcome["by_estimand"].items():
            for org, interval in verdict["binding"].items():
                print(
                    f"{org:<12}{name:<18}{interval['estimate']:>+10.4f}"
                    f"{interval['low']:>+10.4f}{interval['high']:>+10.4f}"
                    f"{int(interval['clusters']):>9d}"
                )
        print(f"verdicts: {outcome['verdicts']}  agree: {outcome['agree']}")

        for org in orgs:
            pooled = outcome["by_estimand"]["pooled"]["binding"][org]["estimate"]
            averaged = outcome["by_estimand"]["change_averaged"]["binding"][org]["estimate"]
            print(f"  {org}: the choice moves the point estimate by {abs(pooled - averaged):.4f}")


if __name__ == "__main__":
    main()
