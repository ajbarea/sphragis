"""Does the registered 7B fit one GPU, training and inference, at the longest admissible item?

Every batch setting and `--time` in `scripts/` was sized on a GH200, whose unified memory would
not have surfaced an overflow, and SPORC's GPUs are 40 GB A100s. Training runs one item per
micro-batch (`train_adapter`), so its peak is set by the longest item, and the item builder
refuses any item over `TRAINING["max_seq_length"]` tokens: an item of exactly that length bounds
every item the study can train on. Token content does not move memory, so the items are synthetic.

Measured, shortest first so an overflow at the bound still leaves the lengths below it:

  training   `train_adapter` on items of each length, at the registered rank and at 256, the
             rank the conditional branch reruns at; peak memory and seconds per optimiser step
  inference  the registered fp32 generator over a prompt one token under the training bound,
             forced to `MAX_NEW_TOKENS` new tokens, bare and with the largest-rank adapter
             attached. Evaluation prompts are not capped, so this is a reference point, not a
             bound; in fp32 the cache grows by about 115 KB a token past it.

An overflow is recorded as `oom` and the probe moves on, since which cell overflows is the
measurement. An overflowed cell has no peak: what it allocated before failing is recorded as
`allocated_at_oom_gb`, a floor on what it needed. `outside_torch_gb` is device memory PyTorch's
allocator does not hold (the CUDA context and libraries), so a cell's headroom is the device's
total less its reserved peak and that. Each cell starts from an emptied cache, where a real run
mixes lengths over hundreds of steps and fragments more, so a thin headroom does not carry over.
A cell with `applied_steps` short of `steps` skipped an update and may understate its peak.
The result file is rewritten after every cell, so a failure later in the job keeps the cells
before it. Submit with `make submit-pinned JOB=memory_probe CLUSTER=sporc`.
"""

from __future__ import annotations

import argparse
import json
import random
import tempfile
import time
from pathlib import Path
from typing import Any

parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--ranks", default="32,256", help="adapter ranks to train at")
parser.add_argument("--lengths", default="512,1024,2048", help="item lengths in tokens")
parser.add_argument("--steps", type=int, default=2, help="optimiser steps per cell")
parser.add_argument(
    "--target-tokens", type=int, default=256, help="supervised tail of each synthetic item"
)

# Clear of the special and control tokens at both ends of Qwen2.5's vocabulary.
TOKEN_RANGE = (1_000, 100_000)


def synthetic_items(n: int, length: int, target_tokens: int, seed: int) -> list[dict[str, Any]]:
    """`n` items of exactly `length` tokens, the last `target_tokens` supervised.

    The layout `sphragis.experiment.training` builds: prompt labels masked, target labels
    equal to the inputs, attention everywhere.
    """
    if not 0 < target_tokens < length:
        raise ValueError(f"target_tokens must be in (0, {length}), got {target_tokens}")
    rng = random.Random(seed)
    items = []
    for _ in range(n):
        ids = [rng.randrange(*TOKEN_RANGE) for _ in range(length)]
        prompt = length - target_tokens
        items.append(
            {
                "input_ids": ids,
                "labels": [-100] * prompt + ids[prompt:],
                "attention_mask": [1] * length,
            }
        )
    return items


def _free() -> None:
    import gc

    import torch

    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()


def _memory(oom: bool) -> dict[str, float | None]:
    """The cell's peaks, or None for each when it overflowed. Call before `_free`."""
    import torch

    free, total = torch.cuda.mem_get_info()
    allocated = round(torch.cuda.max_memory_allocated() / 1e9, 2)
    return {
        "peak_allocated_gb": None if oom else allocated,
        "peak_reserved_gb": None if oom else round(torch.cuda.max_memory_reserved() / 1e9, 2),
        "allocated_at_oom_gb": allocated if oom else None,
        "outside_torch_gb": round((total - free - torch.cuda.memory_reserved()) / 1e9, 2),
        "total_gb": round(total / 1e9, 2),
    }


def _write(path: Path, report: dict[str, Any]) -> None:
    from sphragis.experiment.model import run_provenance

    provenance = run_provenance()
    # The probe resets the peak between cells, so the process-wide peak that `gpu_record` reports
    # would be the last cell's alone. The cells carry the peaks.
    if provenance.get("gpu"):
        provenance["gpu"] |= {"peak_allocated_gb": None, "peak_reserved_gb": None}
    path.write_text(json.dumps({**report, "provenance": provenance}, indent=2) + "\n")


def train_cells(
    args: argparse.Namespace,
    ranks: list[int],
    lengths: list[int],
    adapter_dir: Path,
    report: dict[str, Any],
) -> None:
    import torch

    from sphragis.experiment.model import MODEL_ID, TRAINING, attach_adapter, train_adapter

    grad_accum = int(TRAINING["batch_size"])
    # One epoch over steps x batch items: exactly `steps` optimiser steps of the registered size.
    budget = {**TRAINING, "epochs": 1}
    cells = report["training"]
    for rank in ranks:
        _free()
        model, tokenizer = attach_adapter(MODEL_ID, seed=1, rank=rank)
        pad_token_id = int(tokenizer.pad_token_id or tokenizer.eos_token_id)
        if rank == ranks[0]:
            # Untimed and unrecorded: the first steps of a process pay CUDA and cuBLAS start-up,
            # which would otherwise land in the first cell's seconds per step.
            warm = synthetic_items(grad_accum, lengths[0], args.target_tokens, seed=0)
            train_adapter(model, warm, pad_token_id=pad_token_id, seed=1, budget=budget)
            _free()
        for length in lengths:
            items = synthetic_items(
                grad_accum * args.steps, length, args.target_tokens, seed=length
            )
            torch.cuda.reset_peak_memory_stats()
            cell: dict[str, Any] = {"rank": rank, "length": length, "steps": args.steps}
            start = time.time()
            oom = False
            try:
                trained = train_adapter(
                    model,
                    items,
                    pad_token_id=pad_token_id,
                    seed=1,
                    budget=budget,
                    grad_accum=grad_accum,
                )
                torch.cuda.synchronize()
                cell |= {
                    "seconds_per_step": round((time.time() - start) / trained.steps, 2),
                    "applied_steps": trained.applied_steps,
                }
            except torch.cuda.OutOfMemoryError:
                oom = True
            cell |= {"oom": oom, **_memory(oom)}
            print(f"train {json.dumps(cell)}", flush=True)
            cells.append(cell)
            _write(args.out, report)
            _free()
        if rank == max(ranks):
            model.save_pretrained(adapter_dir)
        del model


def inference_cells(
    args: argparse.Namespace, max_length: int, adapter_dir: Path | None, report: dict[str, Any]
) -> None:
    import torch

    from sphragis.experiment.model import MAX_NEW_TOKENS, MODEL_ID, HFGenerator

    cells = report["inference"]
    for adapter in (None, adapter_dir):
        _free()
        cell: dict[str, Any] = {
            "adapter_rank": None if adapter is None else max(_ints(args.ranks)),
            "prompt_tokens": max_length - 1,
            "new_tokens": MAX_NEW_TOKENS,
        }
        generator = None
        oom = False
        try:
            generator = HFGenerator(
                model_id=MODEL_ID, adapter_path=None if adapter is None else str(adapter)
            )
            cell["dtype"] = generator.computed_dtype
            ids = synthetic_items(1, max_length, 1, seed=0)[0]["input_ids"][:-1]
            inputs = torch.tensor([ids], device=generator.device)
            start = time.time()
            with torch.inference_mode():
                out = generator.model.generate(  # ty: ignore[invalid-argument-type]
                    input_ids=inputs,
                    attention_mask=torch.ones_like(inputs),
                    max_new_tokens=MAX_NEW_TOKENS,
                    min_new_tokens=MAX_NEW_TOKENS,
                    do_sample=False,
                    pad_token_id=generator.tokenizer.eos_token_id,
                )
            torch.cuda.synchronize()
            cell |= {
                "seconds": round(time.time() - start, 2),
                "generated": int(out.shape[-1] - inputs.shape[-1]),
            }
        except torch.cuda.OutOfMemoryError:
            oom = True
        del generator
        cell |= {"oom": oom, **_memory(oom)}
        print(f"infer {json.dumps(cell)}", flush=True)
        cells.append(cell)
        _write(args.out, report)


def check_lengths(lengths: list[int], target_tokens: int, max_length: int) -> str | None:
    """Why these cells cannot run, or None. Checked before any model loads."""
    if lengths[-1] > max_length:
        return f"lengths above the registered bound {max_length} are inadmissible"
    if lengths[0] <= target_tokens:
        return f"every length must exceed --target-tokens {target_tokens}, got {lengths[0]}"
    return None


def _ints(text: str) -> list[int]:
    return sorted(int(part) for part in text.split(","))


def main() -> None:
    args = parser.parse_args()
    from sphragis.experiment.model import TRAINING

    max_length = int(TRAINING["max_seq_length"])
    lengths = _ints(args.lengths)
    if problem := check_lengths(lengths, args.target_tokens, max_length):
        parser.error(problem)
    report: dict[str, Any] = {
        "max_seq_length": max_length,
        "target_tokens": args.target_tokens,
        "complete": False,
        "training": [],
        "inference": [],
    }
    with tempfile.TemporaryDirectory() as tmp:
        adapter_dir = Path(tmp) / "adapter"
        train_cells(args, _ints(args.ranks), lengths, adapter_dir, report)
        saved = adapter_dir if (adapter_dir / "adapter_config.json").exists() else None
        inference_cells(args, max_length, saved, report)
    report["complete"] = True
    _write(args.out, report)
    print("PROBE_OK")


if __name__ == "__main__":
    main()
