"""Federated client updates for RQ2: many small adapters, one initialization, several sources.

Every client of every source starts from the identical LoRA state, as clients do from a round's
global adapter, trains the same number of steps on its own disjoint examples, and is saved. No
evaluation: the question is what the updates themselves reveal, read afterwards on CPU by
scripts/adapter_geometry.py and scripts/client_attribution.py.

The initial state is captured once and restored before each client rather than re-seeded, so
"same initialization" holds by construction, and the restore is checked. The base model loads
once. LoRA's B starts at zero, so a saved adapter's B A is the client's update itself.

    uv run --no-sync python scripts/client_updates.py \
        --source openstack:python=nova.jsonl --source qt:docs=qtdoc.jsonl ... \
        --adapters ~/scratch/sphragis-adapters-clients --out ~/client-updates.json

A source is named `<organization>:<content>` so the attribution can be read at either altitude.

Run on the cluster: see scripts/client_updates.sbatch.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from peft import get_peft_model_state_dict, set_peft_model_state_dict

from sphragis.experiment.clients import partition
from sphragis.experiment.model import (
    MODEL_ID,
    TRAINING,
    _require_tokenizer,
    attach_adapter,
    run_provenance,
    train_adapter,
)
from sphragis.experiment.runner import build_prompt
from sphragis.experiment.training import build_supervised

parser = argparse.ArgumentParser()
parser.add_argument("--source", action="append", required=True, metavar="ORG:CONTENT/PROJECT=PATH")
parser.add_argument("--client-size", type=int, default=64)
parser.add_argument("--clients", type=int, default=8, help="at most this many per source")
parser.add_argument(
    "--max-per-change",
    type=int,
    help="examples one change may give a client; default a quarter of the client",
)
parser.add_argument("--seed", type=int, default=1, help="the shared initialization")
parser.add_argument("--adapters", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--dry-run", action="store_true", help="partition only, no model")
parser.add_argument(
    "--model-id", default=MODEL_ID, help="a smaller model of the same family for a smoke run"
)


def main() -> None:
    args = parser.parse_args()
    # Examples the trainer would refuse are dropped before partitioning, or a refusal inside a
    # client would leave it short and break the equal-size design. Needs only the tokenizer.
    tok = _require_tokenizer(args.model_id)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    plan: dict[str, list[list[dict]]] = {}
    items: dict[str, dict] = {}
    for spec in args.source:
        name, _, path = spec.partition("=")
        rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line]
        usable = []
        for row in rows:
            try:
                items[row["id"]] = build_supervised(
                    tok, row, prompt_builder=build_prompt, max_length=TRAINING["max_seq_length"]
                )
            except ValueError:
                continue
            usable.append(row)
        plan[name] = partition(
            usable,
            size=args.client_size,
            seed=args.seed,
            limit=args.clients,
            max_per_change=args.max_per_change,
        )
        print(
            f"{name}: {len(rows)} examples, {len(rows) - len(usable)} refused -> "
            f"{len(plan[name])} clients",
            flush=True,
        )
    if any(not clients for clients in plan.values()):
        raise SystemExit("a source yields no full client at this size")
    if args.dry_run:
        print("DRY RUN: partition only, no model loaded")
        return

    model, _ = attach_adapter(args.model_id, args.seed)
    initial = {k: v.detach().clone() for k, v in get_peft_model_state_dict(model).items()}
    model.save_pretrained(args.adapters / "init")
    reports: dict[str, dict] = {}
    for name, clients in plan.items():
        for index, client in enumerate(clients):
            set_peft_model_state_dict(model, initial)
            restored = get_peft_model_state_dict(model)
            if not all(torch.equal(restored[k], v) for k, v in initial.items()):
                raise SystemExit("the initial adapter state did not restore exactly")
            batch = [items[r["id"]] for r in client]
            report = train_adapter(model, batch, pad_token_id=tok.pad_token_id, seed=args.seed)
            # A skipped micro-batch drops one example from the step, leaving the client short of
            # the size every client is held to, so it stops the run as a skipped step does.
            if report.skipped_steps or report.skipped_micro_batches:
                raise SystemExit(
                    f"{name} client {index}: {report.skipped_steps} steps and "
                    f"{report.skipped_micro_batches} micro-batches skipped"
                )
            label = f"{name.replace(':', '-').replace('/', '_')}-c{index}"
            model.save_pretrained(args.adapters / label)
            reports[label] = {
                "source": name,
                "examples": len(batch),
                "changes": len({r["change_id"] for r in client}),
                "ids": [r["id"] for r in client],
                "steps": report.steps,
                "applied_steps": report.applied_steps,
                "skipped_steps": report.skipped_steps,
                "skipped_micro_batches": report.skipped_micro_batches,
                "first_loss": report.losses[0],
                "last_loss": report.losses[-1],
                "adapter_weight_norm": report.adapter_weight_norm,
            }
            print(
                f"{label}: {reports[label]['steps']} steps, loss "
                f"{report.losses[0]:.3f} -> {report.losses[-1]:.3f}",
                flush=True,
            )
    args.out.write_text(
        json.dumps(
            {
                "model_id": args.model_id,
                "client_size": args.client_size,
                "max_per_change": args.max_per_change or max(1, args.client_size // 4),
                "seed": args.seed,
                "clients": reports,
                "provenance": run_provenance(),
            },
            indent=2,
        )
    )
    print(f"wrote {args.out}")
    print("CLIENT_UPDATES_OK")


if __name__ == "__main__":
    main()
