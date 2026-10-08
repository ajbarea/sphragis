"""Read the repetition-penalty check: what the checkpoint's penalty changed in stored results.

Qwen2.5-Coder-7B-Instruct's pinned generation config sets `repetition_penalty: 1.1`, and
transformers applies it under greedy decoding as well as sampling, over the prompt's tokens as well
as the output's, so every result written before the penalty was recorded decoded with it. Each
`--pair` is a stored H1 partition run and its rescoring at an explicit penalty with the same
adapters (`decoder_check.sbatch`); a pair rescored at 1.1 is the control and must reproduce the
stored predictions exactly. Each `--decoding` cell is the planted calibration adapter rerun at one
temperature and penalty (`decoding_check.sbatch`). CPU only.

    uv run --no-sync python scripts/decoder_read.py \\
        --pair rq1-partition-openstack-p2-n1850.json:rq1-partition-openstack-p2-n1850-rp1.1.json \\
        --pair rq1-partition-openstack-p2-n1850.json:rq1-partition-openstack-p2-n1850-rp1.0.json \\
        --decoding decoding-marker-0.25-fp32-t0.0-rp1.1.json ... \\
        --out datasets/results/decoder-penalty.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import fmean
from typing import Any

from sphragis.experiment.runs import decoder
from sphragis.provenance import provenance_header

RESULTS = Path("datasets/results")


def _load(name: str) -> dict[str, Any]:
    return json.loads((RESULTS / name).read_text())


def pair(stored_name: str, rerun_name: str) -> dict[str, Any]:
    """Per arm: exact match under each decoder and how many predictions the rerun kept."""
    stored, rerun = _load(stored_name), _load(rerun_name)
    if set(stored["results"]) != set(rerun["results"]):
        raise SystemExit(f"{rerun_name} scored other arms than {stored_name}")
    arms = {}
    for arm, rows in sorted(stored["results"].items()):
        again = rerun["results"][arm]
        if [r["id"] for r in rows] != [r["id"] for r in again]:
            raise SystemExit(f"{rerun_name}: {arm} scored other examples")
        arms[arm] = {
            "examples": len(rows),
            "stored_exact_match": fmean(r["exact_match"] for r in rows),
            "rerun_exact_match": fmean(r["exact_match"] for r in again),
            "predictions_kept": sum(
                a["prediction"] == b["prediction"] for a, b in zip(rows, again, strict=True)
            ),
        }
    contrast = {
        name: {
            org: {key: interval[key] for key in ("estimate", "low", "high")}
            for org, interval in run["verdict"]["binding"].items()
        }
        for name, run in (("stored", stored), ("rerun", rerun))
    }
    return {
        "stored": stored_name,
        "rerun": rerun_name,
        "stored_decoder": decoder(stored),
        "rerun_decoder": decoder(rerun),
        "reproduces_stored": all(a["predictions_kept"] == a["examples"] for a in arms.values()),
        "arms": arms,
        "binding": contrast,
        "verdict": {"stored": stored["verdict"]["verdict"], "rerun": rerun["verdict"]["verdict"]},
    }


def decoding(name: str) -> dict[str, Any]:
    cell = _load(name)
    return {
        "file": name,
        "temperature": cell["temperature"],
        "repetition_penalty": cell["repetition_penalty"],
        "dtype": cell["dtype"],
        "halves": cell["halves"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", action="append", default=[], help="stored.json:rerun.json")
    parser.add_argument("--decoding", action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    pairs = [pair(*spec.split(":", 1)) for spec in args.pair]
    report = {
        "pairs": pairs,
        "decoding": [decoding(name) for name in args.decoding],
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    for p in pairs:
        print(f"{p['rerun']}: reproduces stored {p['reproduces_stored']}, {p['verdict']}")
        for arm, a in p["arms"].items():
            print(
                f"  {arm:<40} {a['stored_exact_match']:.4f} -> {a['rerun_exact_match']:.4f}"
                f"  kept {a['predictions_kept']}/{a['examples']}"
            )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
