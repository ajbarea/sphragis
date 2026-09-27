"""The registered decomposition gate, read on development-window runs as a pilot.

Merges single-seed placebo runs (own half against sibling half, per organization) and runs
`decomposition_gate` over the admitted organizations, with the detectable effects read from a
`decomposition_sensitivity.py` artifact. A pilot reading: the development window is censored
and never stands for the test-window effect.

    PYTHONPATH=$PWD .venv/bin/python scripts/decomposition_pilot.py \
        datasets/results/rq1-placebo-openstack-v2.json \
        datasets/results/rq1-placebo-openstack-v2-s{2,3,4,5}.json \
        --admitted openstack --corpus-digest f49b745cfbde62c0 \
        --sensitivity datasets/results/decomposition-sensitivity-v2.json \
        --out datasets/results/decomposition-pilot-openstack-v2.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from sphragis.experiment.decomposition import decomposition_gate, detectable_effects
from sphragis.experiment.runs import merge
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("runs", type=Path, nargs="+", help="single-seed placebo result files")
parser.add_argument("--admitted", action="append", required=True)
parser.add_argument("--sensitivity", type=Path, required=True)
parser.add_argument("--corpus-digest", help="as in scripts/seed_effect.py")
parser.add_argument("--bootstrap-seed", type=int, default=7)
parser.add_argument("--out", type=Path, required=True)


def _digest(value: str | None) -> bool:
    if value is None:
        return False
    if not re.fullmatch(r"[0-9a-f]{16}", value):
        raise SystemExit(f"--corpus-digest takes 16 hex digits, got {value!r}")
    return True


def main() -> None:
    args = parser.parse_args()
    results, seeds = merge(args.runs, roots_verified=_digest(args.corpus_digest))
    detectable = detectable_effects(json.loads(args.sensitivity.read_text()))
    outcome = decomposition_gate(
        results,
        admitted=args.admitted,
        seeds=seeds,
        bootstrap_seed=args.bootstrap_seed,
        detectable=detectable,
    )
    for org, cell in outcome["per_org"]["H1"].items():
        for level, interval in cell["intervals"].items():
            print(
                f"H1 {org} at {level}: {cell['estimate']:+.4f} "
                f"[{interval['low']:+.4f}, {interval['high']:+.4f}] {cell['verdicts'][level]}"
            )
    print("verdicts", outcome["verdicts"], "reading:", outcome["reading"])
    report = {
        "runs": [str(p) for p in args.runs],
        "seeds": seeds,
        "sensitivity": str(args.sensitivity),
        "pilot": "development window",
        **outcome,
        "provenance": provenance_header(),
    }
    if args.corpus_digest:
        report["corpus_roots_digest"] = args.corpus_digest
    args.out.write_text(json.dumps(report, indent=2, default=str))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
