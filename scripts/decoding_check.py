"""Is greedy decoding what amplifies a planted convention?

At a quarter planted, the adapter emitted the annotation on about 64% of its greedy outputs. If
greedy is the amplifier, sampling from the same adapter at temperature 1 should bring the rate
back toward the 25% it was trained on. If sampling also gives something near 64%, the adapter
itself learned the amplified rate and decoding is not the cause.

No retraining: this reloads the adapter the calibration run saved and evaluates it on exactly
the held-out examples that run used, read from its own result file, so the only thing that
differs between the two emission rates is the decoder.

Run on the cluster: see scripts/decoding_check.sbatch.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import fmean

from sphragis.experiment.model import HFGenerator, run_provenance
from sphragis.experiment.planted import MARKER
from sphragis.experiment.runner import build_prompt

parser = argparse.ArgumentParser()
parser.add_argument("--condition", default="marker-0.25")
parser.add_argument("--results", type=Path, required=True, help="the calibration run's JSON")
parser.add_argument("--corpus-dir", type=Path, required=True, help="where the planted halves are")
parser.add_argument("--adapter", type=Path, required=True, help="the planted half's saved adapter")
parser.add_argument("--temperature", type=float, default=1.0)
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--out", type=Path, required=True)


def emits(text: str) -> bool:
    return MARKER.strip() in text


def main() -> None:
    args = parser.parse_args()
    run = json.loads(args.results.read_text())["results"]
    examples = {}
    for side in ("a", "b"):
        path = args.corpus_dir / f"{args.condition}-{side}.jsonl"
        for line in path.open():
            if line.strip():
                row = json.loads(line)
                examples[row["id"]] = row

    generator = HFGenerator(
        adapter_path=str(args.adapter), temperature=args.temperature, seed=args.seed
    )
    halves: dict[str, dict[str, float]] = {}
    # The planted half is b, so b's adapter is the one whose emission is in question. Evaluate
    # it on both halves, on the same examples the greedy run scored.
    for half, arm in (("a", "adapter:b|a|s1"), ("b", "adapter:b|b|s1")):
        greedy_rows = run[arm]
        greedy = fmean(1.0 if emits(str(r.get("prediction", ""))) else 0.0 for r in greedy_rows)
        sampled = [emits(generator.generate(build_prompt(examples[r["id"]]))) for r in greedy_rows]
        rate = fmean(1.0 if s else 0.0 for s in sampled)
        halves[half] = {
            "examples": len(greedy_rows),
            "greedy_emission": greedy,
            "sampled_emission": rate,
        }
        print(
            f"half {half}: {len(greedy_rows)} examples  greedy {greedy:.3f}  "
            f"sampled at T={args.temperature:g} {rate:.3f}",
            flush=True,
        )

    args.out.write_text(
        json.dumps(
            {
                "condition": args.condition,
                "adapter": str(args.adapter),
                "temperature": args.temperature,
                "seed": args.seed,
                "halves": halves,
                "provenance": run_provenance(),
            },
            indent=2,
        )
    )
    print(f"wrote {args.out}")
    print("DECODING_CHECK_OK")


if __name__ == "__main__":
    main()
