"""Does the registered 7B fit one GPU, training and inference, at the longest admissible item?

Every batch setting and `--time` in `scripts/` was sized on a GH200, whose unified memory would
not have surfaced an overflow, and SPORC's GPUs are 40 GB A100s. Training runs one item per
micro-batch (`train_adapter`), so its peak is set by the longest item, and the item builder
refuses any item over `TRAINING["max_seq_length"]` tokens: an item of exactly that length bounds
every item the study can train on. Token content does not move memory, so the items are synthetic.

Measured, shortest first so an overflow at the bound still leaves the lengths below it:

  training   `train_adapter` on items of each length, at the registered rank and at 256, the
             rank the conditional branch reruns at; peak memory and seconds per optimiser step
  inference  the registered fp32 generator over a prompt one token under the bound, forced to
             `MAX_NEW_TOKENS` new tokens, bare and with the largest-rank adapter attached

An overflow is recorded as `oom` and the probe moves on, since which cell overflows is the
measurement. Submit with `make submit-pinned JOB=memory_probe CLUSTER=sporc`.
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
parser.add_argument("--lengths", default="256,1024,2048", help="item lengths in tokens")
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


def _peak() -> dict[str, float]:
    import torch

    return {
        "peak_allocated_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2),
        "peak_reserved_gb": round(torch.cuda.max_memory_reserved() / 1e9, 2),
    }


def train_cells(
    args: argparse.Namespace, ranks: list[int], lengths: list[int], adapter_dir: Path
) -> list[dict[str, Any]]:
    import torch

    from sphragis.experiment.model import MODEL_ID, TRAINING, attach_adapter, train_adapter

    grad_accum = int(TRAINING["batch_size"])
    # One epoch over steps x batch items: exactly `steps` optimiser steps of the registered size.
    budget = {**TRAINING, "epochs": 1}
    cells = []
    for rank in ranks:
        _free()
        model, tokenizer = attach_adapter(MODEL_ID, seed=1, rank=rank)
        for length in lengths:
            items = synthetic_items(
                grad_accum * args.steps, length, args.target_tokens, seed=length
            )
            torch.cuda.reset_peak_memory_stats()
            cell: dict[str, Any] = {"rank": rank, "length": length, "steps": args.steps}
            start = time.time()
            try:
                report = train_adapter(
                    model,
                    items,
                    pad_token_id=int(tokenizer.pad_token_id or tokenizer.eos_token_id),
                    seed=1,
                    budget=budget,
                    grad_accum=grad_accum,
                )
                torch.cuda.synchronize()
                cell |= {
                    "oom": False,
                    "seconds_per_step": round((time.time() - start) / report.steps, 2),
                    "applied_steps": report.applied_steps,
                }
            except torch.cuda.OutOfMemoryError:
                cell["oom"] = True
            cell |= _peak()
            print(f"train {json.dumps(cell)}", flush=True)
            cells.append(cell)
            _free()
        if rank == max(ranks):
            model.save_pretrained(adapter_dir)
        del model
    return cells


def inference_cells(
    args: argparse.Namespace, max_length: int, adapter_dir: Path | None
) -> list[dict[str, Any]]:
    import torch

    from sphragis.experiment.model import MAX_NEW_TOKENS, MODEL_ID, HFGenerator

    cells = []
    for adapter in (None, adapter_dir):
        _free()
        cell: dict[str, Any] = {
            "adapter_rank": None if adapter is None else max(_ints(args.ranks)),
            "prompt_tokens": max_length - 1,
            "new_tokens": MAX_NEW_TOKENS,
        }
        generator = None
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
                "oom": False,
                "seconds": round(time.time() - start, 2),
                "generated": int(out.shape[-1] - inputs.shape[-1]),
            }
        except torch.cuda.OutOfMemoryError:
            cell["oom"] = True
        del generator
        cell |= _peak()
        print(f"infer {json.dumps(cell)}", flush=True)
        cells.append(cell)
    return cells


def _ints(text: str) -> list[int]:
    return sorted(int(part) for part in text.split(","))


def main() -> None:
    args = parser.parse_args()
    from sphragis.experiment.model import TRAINING, run_provenance

    max_length = int(TRAINING["max_seq_length"])
    lengths = _ints(args.lengths)
    if lengths[-1] > max_length:
        parser.error(f"lengths above the registered bound {max_length} are inadmissible")
    with tempfile.TemporaryDirectory() as tmp:
        adapter_dir = Path(tmp) / "adapter"
        training = train_cells(args, _ints(args.ranks), lengths, adapter_dir)
        saved = adapter_dir if (adapter_dir / "adapter_config.json").exists() else None
        inference = inference_cells(args, max_length, saved)
    args.out.write_text(
        json.dumps(
            {
                "max_seq_length": max_length,
                "target_tokens": args.target_tokens,
                "training": training,
                "inference": inference,
                "provenance": run_provenance(),
            },
            indent=2,
        )
        + "\n"
    )
    print("PROBE_OK")


if __name__ == "__main__":
    main()
