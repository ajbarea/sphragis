"""FDLoRA (Lu et al., arXiv:2406.07925): a global module seeded from the average of the
personalized ones and periodically overwriting them, the sibling of `dual_adapter_updates.py`
in the adapter-instance cut. Registered reading of Algorithm 1 is in `docs/research-log.md`,
2026-09-21 ("Registered before computing: how we read FDLoRA's algorithm, and the step it does
not account for").

Stage 1 (Algorithm 1, lines 1-6): every client trains its own personalized module from the shared
initialization, independently. Stage 2 (lines 7-19): the personalized modules average once into
the global module's round-0 seed -- the one round the paper's own prose never mentions leaves the
client -- and each client then runs `--inner-steps` (K) of InnerOpt on its own copy of the global
module every round, with the personalized module overwritten by that copy on a sync round
(`sphragis.experiment.fdlora.should_sync`, H = `--sync-period`).

What is saved per client: the global module (what the server receives every round, saved once
after the last), the personalized module beside it under `-local` (what stays withheld, mirroring
`dual_adapter_updates.py`), and the round-0 personalized module under `-p0` -- the transmission
this reading finds and the paper does not name.

This script does not simulate the server's own outer aggregation of the global module across
rounds (Algorithm 1, lines 17-18): after the round-0 average, each client continues training its
own copy independently, the same simplification `dual_adapter_updates.py` already makes for
FedDPA. Real cross-client averaging every round would wash out more of a client's own signal, not
less, so training independently is the conservative direction for a leakage measurement: it can
only overstate what a real, further-averaged deployment would leak.

Run: uv run --no-sync python scripts/fdlora_schedule.py \
       --sources-file scripts/clients-cpp-rebuilt.txt --corpus-root ~/corpus/clients3 \
       --client-size 128 --rounds 6 --inner-steps 3 --sync-period 3 \
       --adapters ~/scratch/sphragis-adapters-fdlora --out ~/client-updates-fdlora.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from peft import get_peft_model_state_dict, set_peft_model_state_dict

from sphragis.experiment.clients import partition
from sphragis.experiment.dual_adapter import GLOBAL, LOCAL, save_adapter, sources
from sphragis.experiment.fdlora import average_state_dicts, should_sync, with_inner_steps
from sphragis.experiment.model import (
    LORA,
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
parser.add_argument("--source", action="append", default=[], metavar="ORG:CONTENT/PROJECT=PATH")
parser.add_argument("--sources-file", type=Path)
parser.add_argument("--corpus-root", type=Path, default=Path())
parser.add_argument("--client-size", type=int, default=64)
parser.add_argument("--clients", type=int, default=8, help="at most this many per source")
parser.add_argument("--max-per-change", type=int)
parser.add_argument("--seed", type=int, default=1, help="the shared initialization")
parser.add_argument("--packing-seed", type=int)
parser.add_argument("--rounds", type=int, default=6, help="T: the outer communication rounds")
parser.add_argument(
    "--inner-steps",
    type=int,
    default=3,
    help="K: InnerOpt passes the global module takes a round (paper default 3)",
)
parser.add_argument(
    "--sync-period",
    type=int,
    default=3,
    help="H: the personalized module is overwritten every this many rounds",
)
parser.add_argument("--adapters", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--model-id", default=MODEL_ID)
parser.add_argument("--device", default="cuda:0", help="cpu exercises the schedule without a GPU")
parser.add_argument("--dry-run", action="store_true", help="partition only, no model")


def _clone(state: dict) -> dict:
    return {k: v.detach().clone() for k, v in state.items()}


def main() -> None:
    args = parser.parse_args()
    tok = _require_tokenizer(args.model_id)

    plan: dict[str, list[list[dict]]] = {}
    items: dict[str, dict] = {}
    for spec in sources(args):
        name, _, path = spec.partition("=")
        rows = [
            json.loads(line) for line in (args.corpus_root / path).read_text().splitlines() if line
        ]
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
            seed=args.seed if args.packing_seed is None else args.packing_seed,
            limit=args.clients,
            max_per_change=args.max_per_change,
        )
        print(f"{name}: {len(rows)} examples -> {len(plan[name])} clients", flush=True)
    if any(not clients for clients in plan.values()):
        raise SystemExit("a source yields no full client at this size")
    if args.dry_run:
        print("DRY RUN: partition only, no model loaded")
        return

    model, _ = attach_adapter(args.model_id, args.seed, device=args.device)
    model.add_adapter(GLOBAL, LORA)
    model.add_adapter(LOCAL, LORA)
    # Every client's Stage 1 starts from this same draw. GLOBAL has no initial of its own to
    # preserve: Stage 1 never trains it (no gradient reaches an inactive adapter, so the
    # optimiser step is a no-op on it regardless), and Stage 2 overwrites it with theta_s0 before
    # any client's first round.
    initial_local = _clone(get_peft_model_state_dict(model, adapter_name=LOCAL))

    reports: dict[str, dict] = {}
    for source, clients in plan.items():
        labels = [
            f"{source.replace(':', '-').replace('/', '_')}-c{index}"
            for index in range(len(clients))
        ]

        # Stage 1 (Algorithm 1, lines 1-6): every client's own personalized module, independently,
        # from the shared initialization.
        personalized: dict[str, dict] = {}
        stage1_losses: dict[str, list[float]] = {}
        for label, client in zip(labels, clients, strict=True):
            set_peft_model_state_dict(model, initial_local, adapter_name=LOCAL)
            model.set_adapter(LOCAL)
            batch = [items[r["id"]] for r in client]
            report = train_adapter(model, batch, pad_token_id=tok.pad_token_id, seed=args.seed)
            if report.skipped_steps or report.skipped_micro_batches:
                raise SystemExit(f"{label}: stage 1 skipped a step")
            personalized[label] = _clone(get_peft_model_state_dict(model, adapter_name=LOCAL))
            stage1_losses[label] = report.losses

        # Algorithm 1, line 7: theta_s(0) <- average of every client's personalized module. Our
        # reading: this is the one round that leaves every client, uncounted by the paper's own
        # "remains uninvolved in the federated learning process".
        theta_s0 = average_state_dicts(list(personalized.values()))
        for label in labels:
            set_peft_model_state_dict(model, personalized[label], adapter_name=LOCAL)
            save_adapter(model, LOCAL, args.adapters / f"{label}-p0")

        # Stage 2 (Algorithm 1, lines 8-19): each client's own copy of the global module, K inner
        # steps a round, synced into the personalized module on a sync round.
        for label, client in zip(labels, clients, strict=True):
            set_peft_model_state_dict(model, theta_s0, adapter_name=GLOBAL)
            set_peft_model_state_dict(model, personalized[label], adapter_name=LOCAL)
            model.set_adapter(GLOBAL)
            batch = [items[r["id"]] for r in client]
            losses: list[float] = []
            for round_index in range(args.rounds):
                report = train_adapter(
                    model,
                    batch,
                    pad_token_id=tok.pad_token_id,
                    seed=args.seed,
                    budget=with_inner_steps(TRAINING, args.inner_steps),
                )
                if report.skipped_steps or report.skipped_micro_batches:
                    raise SystemExit(f"{label}: round {round_index} skipped a step")
                losses.extend(report.losses)
                if should_sync(round_index, args.sync_period):
                    # Algorithm 1, line 14: theta_p(i) <- theta_s(i)(t).
                    set_peft_model_state_dict(
                        model,
                        get_peft_model_state_dict(model, adapter_name=GLOBAL),
                        adapter_name=LOCAL,
                    )

            save_adapter(model, GLOBAL, args.adapters / label)
            save_adapter(model, LOCAL, args.adapters / f"{label}-local")
            reports[label] = {
                "source": source,
                "examples": len(batch),
                "changes": len({r["change_id"] for r in client}),
                "ids": [r["id"] for r in client],
                "stage1_first_loss": stage1_losses[label][0],
                "stage1_last_loss": stage1_losses[label][-1],
                "global_first_loss": losses[0],
                "global_last_loss": losses[-1],
            }
            print(
                f"{label}: stage1 {stage1_losses[label][0]:.3f} -> {stage1_losses[label][-1]:.3f}, "
                f"global {losses[0]:.3f} -> {losses[-1]:.3f}",
                flush=True,
            )

    args.out.write_text(
        json.dumps(
            {
                "model_id": args.model_id,
                "rounds": args.rounds,
                "inner_steps": args.inner_steps,
                "sync_period": args.sync_period,
                "transmitted": GLOBAL,
                "withheld": LOCAL,
                "withheld_round0": "p0",
                "corpus_root": str(args.corpus_root),
                "sources": sorted(sources(args)),
                "client_size": args.client_size,
                "max_per_change": args.max_per_change or max(1, args.client_size // 4),
                "seed": args.seed,
                "packing_seed": args.seed if args.packing_seed is None else args.packing_seed,
                "device": args.device,
                "clients": reports,
                "provenance": run_provenance(),
            },
            indent=2,
        )
    )
    print(f"wrote {args.out}")
    print("FDLORA_SCHEDULE_OK")


if __name__ == "__main__":
    main()
