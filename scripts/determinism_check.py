"""Where does greedy decoding's run-to-run variation come from?

Two byte-identical null runs, job 148088 on node gh-a-081 and job 148199 on gh-a-103,
disagreed on 23 to 28 of about 440 base-model predictions. Batch size is one throughout, and
a forward pass over identical inputs at a fixed batch shape is bitwise reproducible on one GPU
(He, "Defeating Nondeterminism in LLM Inference", Thinking Machines, 2025), so floating-point
non-associativity alone does not explain it. What differed was the node and the job.

Each job runs the base model over the same prompts four times:

  bf16         as the study runs it
  bf16_repeat  the same generator again: is one process reproducible?
  bf16_fresh   a reloaded model: does loading it again change anything?
  fp32         the same weights upcast exactly, computed in fp32 (LayerCast's idea)

The question is between jobs, so submit it twice, pinned to the two nodes the stored runs used
(scripts/determinism_check.sbatch), then compare locally:

    uv run --no-sync --no-active python scripts/determinism_check.py --compare \
        datasets/results/determinism-sym-0-gh-a-081.json \
        datasets/results/determinism-sym-0-gh-a-103.json \
        --stored datasets/results/calibration-marker-0.json datasets/results/calibration-sym-0.json

If the job on gh-a-081 reproduces 148088's predictions and the one on gh-a-103 reproduces
148199's, the node is the variable; if fp32 agrees across nodes where bf16 does not, computing
in fp32 is the fix for the confirmatory run.

(PyTorch's allocator aligns every block to 512 bytes, so allocation state cannot change the
alignment a GEMM kernel is chosen on, and is not tested.)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--condition", default="sym-0")
parser.add_argument("--half", default="a")
parser.add_argument("--corpus-dir", type=Path, help="where the condition's corpus halves are")
parser.add_argument(
    "--stored", type=Path, nargs="+", required=True, help="runs whose base predictions to compare"
)
parser.add_argument("--examples", type=int, default=150)
parser.add_argument("--out", type=Path, help="this job's passes")
parser.add_argument("--compare", type=Path, nargs="*", help="job outputs to compare, no GPU")


def stored_predictions(paths: list[Path], arm: str) -> dict[str, dict[str, str]]:
    return {
        path.name: {r["id"]: r["prediction"] for r in json.loads(path.read_text())["results"][arm]}
        for path in paths
    }


def generate(args: argparse.Namespace) -> None:
    import torch

    from sphragis.experiment.model import HFGenerator, run_provenance
    from sphragis.experiment.runner import build_prompt

    stored = stored_predictions(args.stored, f"base|{args.half}")
    ids = sorted(next(iter(stored.values())))[: args.examples]
    rows = {}
    for line in (args.corpus_dir / f"{args.condition}-{args.half}.jsonl").open():
        if line.strip():
            row = json.loads(line)
            rows[row["id"]] = row
    missing = [i for i in ids if i not in rows]
    if missing:
        raise SystemExit(
            f"{len(missing)} stored ids are not in {args.condition}-{args.half}: the stored runs "
            "and the corpus are not the same held-out examples"
        )
    prompts = {i: build_prompt(rows[i]) for i in ids}

    def run(generator: HFGenerator, label: str) -> dict[str, str]:
        out = {i: generator.generate(p) for i, p in prompts.items()}
        print(f"{label}: done", flush=True)
        return out

    passes: dict[str, dict[str, str]] = {}
    generator = HFGenerator()
    passes["bf16"] = run(generator, "bf16")
    passes["bf16_repeat"] = run(generator, "bf16 repeat")
    del generator
    torch.cuda.empty_cache()
    generator = HFGenerator()
    passes["bf16_fresh"] = run(generator, "bf16 fresh")
    # bf16 -> fp32 is exact, so this is the same weights computed in fp32.
    generator.model.to(torch.float32)
    passes["fp32"] = run(generator, "fp32")
    args.out.write_text(
        json.dumps(
            {
                "condition": args.condition,
                "half": args.half,
                "ids": ids,
                "passes": passes,
                "provenance": run_provenance(),
            },
            indent=2,
        )
    )
    print(f"wrote {args.out}")
    print("DETERMINISM_CHECK_OK")


def compare(args: argparse.Namespace) -> None:
    jobs = [json.loads(path.read_text()) for path in args.compare]
    ids = jobs[0]["ids"]
    if any(job["ids"] != ids for job in jobs):
        raise SystemExit("the jobs generated for different prompts")
    columns: dict[str, dict[str, str]] = {}
    for job in jobs:
        node = job["provenance"]["slurm"]["node"]
        for name, predictions in job["passes"].items():
            columns[f"{node}:{name}"] = predictions
    for name, predictions in stored_predictions(args.stored, f"base|{jobs[0]['half']}").items():
        columns[f"stored:{name}"] = {i: predictions[i] for i in ids}
    names = list(columns)
    print(f"predictions differing, of {len(ids)}")
    for a in names:
        row = " ".join(f"{sum(columns[a][i] != columns[b][i] for i in ids):4d}" for b in names)
        print(f"{a:40} {row}")


def main() -> None:
    args = parser.parse_args()
    if args.compare:
        compare(args)
    elif args.corpus_dir and args.out:
        generate(args)
    else:
        parser.error("either --compare, or --corpus-dir and --out to generate")


if __name__ == "__main__":
    main()
