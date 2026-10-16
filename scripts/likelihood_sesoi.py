"""The SESOI in log-probability per token, carried from exact match by the positive control.

No published threshold exists for a likelihood gain, so the SESOI keeps its cost-benefit meaning,
one more exact refinement per hundred review comments (`decomposition.SESOI`), converted by the
exchange rate between the two scores on the positive control: every adapter arm against the base
model on the adapter's own half, over the unplanted development runs. The positive control is
not H1's contrast, so the rate is fixed without reading own minus sibling (research log,
2026-10-09, "Planned before any likelihood result is read").

The rule registered before any planted quote-style result was read (research log, 2026-10-15)
then decides between that rate and the one measured on a planted code-level convention
(`planted_exchange.py`, ten OpenStack runs at f = 1.0): if the planted rate's 95% interval
excludes the positive control's rate, δ is carried at the planted rate; if it includes it, or
the planted reading has no rate or no interval, the positive control's rate stands. The five
runs at f = 0.5 are reported beside it as the dose check. CPU only.

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
PLANTED = RESULTS / "planted-exchange-openstack-quotes1.0.json"
DOSE_CHECK = RESULTS / "planted-exchange-openstack-quotes0.5.json"


def planted_reading(path: Path) -> dict:
    """A planted exchange artifact's rate and interval, either None when it read none."""
    art = json.loads(path.read_text())
    return {
        "file": str(path),
        "fraction": art["fraction"],
        "rate": art.get("rate"),
        "rate_95": art.get("rate_95"),
        "undefined_resamples": art["undefined_resamples"],
    }


def chosen_rate(control: float, planted: dict) -> tuple[float, str]:
    """The registered rule: the planted rate when its interval excludes the control's rate."""
    interval = planted["rate_95"]
    if planted["rate"] is None or interval is None:
        return control, "positive_control"
    low, high = interval
    if low <= control <= high:
        return control, "positive_control"
    return planted["rate"], "planted"


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
    parser.add_argument("--planted", type=Path, default=PLANTED)
    parser.add_argument("--dose-check", type=Path, default=DOSE_CHECK)
    args = parser.parse_args()
    files = sorted(
        p for p in RESULTS.glob("likelihood-partition-*-rp1.0.json") if "-plant" not in p.name
    )
    gains = [gain for path in files for gain in own_half_gains(path)]
    logprob, exact = fmean(g[0] for g in gains), fmean(g[1] for g in gains)
    control = logprob / exact
    planted = planted_reading(args.planted)
    dose = planted_reading(args.dose_check)
    rate, source = chosen_rate(control, planted)
    dose_interval = dose["rate_95"]
    dose["holds_primary_rate"] = (
        None if dose_interval is None else dose_interval[0] <= planted["rate"] <= dose_interval[1]
    )
    report = {
        "sesoi": SESOI * rate,
        "exact_match_sesoi": SESOI,
        "rate": rate,
        "rate_source": source,
        "positive_control_rate": control,
        "planted": planted,
        "dose_check": dose,
        "adapter_minus_base": {"logprob_per_token": logprob, "exact_match": exact},
        "arms": len(gains),
        "files": [str(p) for p in files],
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"SESOI {report['sesoi']:.6f} nats per token at the {source} rate {rate:.4f} "
        f"(positive control {control:.4f} over {len(gains)} arms in {len(files)} runs; "
        f"planted {planted['rate']:.4f}, 95% {planted['rate_95']})"
    )


if __name__ == "__main__":
    main()
