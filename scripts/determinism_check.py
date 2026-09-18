"""Where does greedy decoding's run-to-run variation come from?

Two byte-identical null runs (jobs 148088, 148199) disagreed on 23 to 28 of about 440 base-model
predictions. Batch size is one throughout, and a forward pass over identical inputs at a fixed
batch shape is bitwise reproducible on one GPU (He, "Defeating Nondeterminism in LLM
Inference", Thinking Machines, 2025), so floating-point non-associativity alone does not
explain it. The two runs differed in node and in what the job had allocated before evaluating.

One job, one node, the same prompts, several passes:

  repeat      the same generator twice: is one process reproducible at all?
  perturbed   after shifting the allocator: does allocation state reach the output?
  fresh       a new generator: does reloading the model?
  fp32        weights upcast exactly from bf16, compute in fp32 (LayerCast's idea)

each compared with the others and with the predictions both stored runs made. Setting
DETERMINISTIC=1 in the job adds torch's deterministic algorithms to every pass.

Run on the cluster: see scripts/determinism_check.sbatch.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import torch

from sphragis.experiment.model import HFGenerator, run_provenance
from sphragis.experiment.runner import build_prompt

parser = argparse.ArgumentParser()
parser.add_argument("--condition", default="sym-0")
parser.add_argument("--half", default="a")
parser.add_argument("--corpus-dir", type=Path, required=True)
parser.add_argument(
    "--stored",
    type=Path,
    nargs="+",
    required=True,
    help="result files whose base-model predictions to compare against",
)
parser.add_argument("--examples", type=int, default=150)
parser.add_argument("--out", type=Path, required=True)


def differing(first: dict[str, str], second: dict[str, str]) -> int:
    return sum(first[i] != second[i] for i in first)


def main() -> None:
    args = parser.parse_args()
    if os.environ.get("DETERMINISTIC") == "1":
        torch.use_deterministic_algorithms(True)
    arm = f"base|{args.half}"
    stored = {
        path.name: {r["id"]: r["prediction"] for r in json.loads(path.read_text())["results"][arm]}
        for path in args.stored
    }
    ids = sorted(next(iter(stored.values())))[: args.examples]
    rows = {}
    for line in (args.corpus_dir / f"{args.condition}-{args.half}.jsonl").open():
        if line.strip():
            row = json.loads(line)
            rows[row["id"]] = row
    prompts = {i: build_prompt(rows[i]) for i in ids}

    def run(generator: HFGenerator, label: str) -> dict[str, str]:
        out = {i: generator.generate(p) for i, p in prompts.items()}
        print(f"{label}: done", flush=True)
        return out

    passes: dict[str, dict[str, str]] = {}
    generator = HFGenerator()
    passes["first"] = run(generator, "first")
    passes["repeat"] = run(generator, "repeat")
    # Odd-sized live allocations move where the next activations land.
    ballast = [torch.empty(n * 1_000_003 + 7, dtype=torch.uint8, device="cuda") for n in (1, 3)]
    passes["perturbed"] = run(generator, "perturbed")
    del ballast, generator
    torch.cuda.empty_cache()
    generator = HFGenerator()
    passes["fresh"] = run(generator, "fresh")
    # bf16 -> fp32 is exact, so this is the same weights computed in fp32.
    generator.model.to(torch.float32)
    passes["fp32"] = run(generator, "fp32")
    passes["fp32_repeat"] = run(generator, "fp32 repeat")

    stored_subset = {name: {i: preds[i] for i in ids} for name, preds in stored.items()}
    everything = {**passes, **stored_subset}
    names = list(everything)
    matrix = {a: {b: differing(everything[a], everything[b]) for b in names} for a in names}
    for a in names:
        print(f"{a:40} " + " ".join(f"{matrix[a][b]:4d}" for b in names))
    args.out.write_text(
        json.dumps(
            {
                "condition": args.condition,
                "half": args.half,
                "examples": len(ids),
                "deterministic_algorithms": os.environ.get("DETERMINISTIC") == "1",
                "differing_predictions": matrix,
                "passes": passes,
                "provenance": run_provenance(),
            },
            indent=2,
        )
    )
    print(f"wrote {args.out}")
    print("DETERMINISM_CHECK_OK")


if __name__ == "__main__":
    main()
