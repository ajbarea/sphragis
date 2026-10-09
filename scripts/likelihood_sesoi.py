"""The SESOI in log-probability per token, carried from exact match by the positive control.

No published threshold exists for a likelihood gain, so the SESOI keeps its cost-benefit meaning,
one more exact refinement per hundred review comments (`decomposition.SESOI`), converted by the
exchange rate between the two scores on the positive control: every adapter arm against the base
model on the adapter's own half, over the unplanted development runs. The positive control is
not H1's contrast, so the rate is fixed without reading own minus sibling (research log,
2026-10-09, "Planned before any likelihood result is read"). CPU only.

    uv run --no-sync python scripts/likelihood_sesoi.py \\
        --out datasets/results/likelihood-sesoi.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import fmean

from sphragis.experiment.decomposition import SESOI
from sphragis.provenance import provenance_header

RESULTS = Path("datasets/results")


def own_half_gains(path: Path) -> list[tuple[float, float]]:
    """Each own-half adapter arm's mean gain over base: per-token log-prob, exact match."""
    results = json.loads(path.read_text())["results"]
    gains = []
    for arm, rows in sorted(results.items()):
        if not arm.startswith("adapter:"):
            continue
        trained, evaluated = arm.removeprefix("adapter:").split("|")[:2]
        if trained != evaluated:
            continue
        base = {row["id"]: row for row in results[f"base|{evaluated}"]}
        gains.append(
            (
                fmean(r["logprob_per_token"] - base[r["id"]]["logprob_per_token"] for r in rows),
                fmean(r["exact_match"] - base[r["id"]]["exact_match"] for r in rows),
            )
        )
    return gains


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    files = sorted(
        p for p in RESULTS.glob("likelihood-partition-*-rp1.0.json") if "-plant" not in p.name
    )
    gains = [gain for path in files for gain in own_half_gains(path)]
    logprob, exact = fmean(g[0] for g in gains), fmean(g[1] for g in gains)
    report = {
        "sesoi": SESOI * logprob / exact,
        "exact_match_sesoi": SESOI,
        "adapter_minus_base": {"logprob_per_token": logprob, "exact_match": exact},
        "arms": len(gains),
        "files": [str(p) for p in files],
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"SESOI {report['sesoi']:.6f} nats per token over {len(gains)} arms in {len(files)} runs")


if __name__ == "__main__":
    main()
