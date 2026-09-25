"""The adapter-instance cut: two adapters per client, and only one of them leaves.

FedDPA (Yang et al., NeurIPS 2024) and FDLoRA (Lu et al., arXiv:2406.07925) split an update
by *instance* rather than by factor, module or subspace: each client holds a global adapter
that is communicated and a local adapter that never is. Neither paper claims the transmitted
half hides its source, and neither runs an attack; FedDPA states the assumption instead, that
"all clients are trusted ... and the whole process does not suffer from any attacks". This
script trains clients under that split so the same instruments the rest of RQ2 uses can read
the half that leaves.

Two schedules, both from FedDPA's own algorithm rather than invented here:

  feddpa-f  the sequential variant. The global adapter trains on the client's data; the local
            adapter is then initialized from it and fine-tuned. One phase each, no rounds.
  feddpa-t  the iterative variant. Per round, the global adapter trains alone, then the local
            adapter trains *alongside the frozen global one*, which is why both adapters are
            active for that phase and only the local one carries gradient.

What is saved per client is the global adapter, because that is what the server receives. The
local adapter is saved beside it under `-local` so a later reading can ask what was withheld,
which is the comparison the papers never make.

Run: uv run --no-sync python scripts/dual_adapter_updates.py \
       --sources-file scripts/clients-cpp-rebuilt.txt --corpus-root ~/corpus/clients3 \
       --client-size 128 --schedule feddpa-t --rounds 2 \
       --adapters ~/scratch/sphragis-adapters-dual --out ~/client-updates-dual.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from peft import get_peft_model_state_dict, set_peft_model_state_dict

from sphragis.corpus.load import derived_file_rows
from sphragis.experiment.clients import partition
from sphragis.experiment.dual_adapter import (
    GLOBAL,
    LOCAL,
    check_label_collisions,
    client_label,
    freeze,
    save_adapter,
    sources,
    unfreeze,
)
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
parser.add_argument(
    "--legacy-corpus",
    action="store_true",
    help="read corpus files not cut under the current label rules, to reproduce an earlier "
    "result; recorded in the output",
)
parser.add_argument(
    "--schedule",
    default="feddpa-t",
    choices=("feddpa-f", "feddpa-t"),
    help="which of FedDPA's two published variants to run",
)
parser.add_argument(
    "--rounds",
    type=int,
    default=2,
    help="feddpa-t only: how many times the pair alternates. One round is feddpa-f with the "
    "local adapter starting from the global one rather than from its own previous state",
)
parser.add_argument("--adapters", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--model-id", default=MODEL_ID)
parser.add_argument("--device", default="cuda:0", help="cpu exercises the schedule without a GPU")
parser.add_argument("--dry-run", action="store_true", help="partition only, no model")


def main() -> None:
    args = parser.parse_args()
    tok = _require_tokenizer(args.model_id)

    plan: dict[str, list[list[dict]]] = {}
    items: dict[str, dict] = {}
    for spec in sources(args):
        name, _, path = spec.partition("=")
        rows = derived_file_rows(args.corpus_root / path, legacy=args.legacy_corpus)
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
    check_label_collisions(plan)
    if args.dry_run:
        print("DRY RUN: partition only, no model loaded")
        return

    model, _ = attach_adapter(args.model_id, args.seed, device=args.device)
    # `attach_adapter` names its adapter "default"; this study needs the pair to be named for
    # what they are, so the global one is added under its own name and the default is left
    # unused rather than silently reinterpreted.
    model.add_adapter(GLOBAL, LORA)
    model.add_adapter(LOCAL, LORA)
    initial = {
        name: {
            k: v.detach().clone()
            for k, v in get_peft_model_state_dict(model, adapter_name=name).items()
        }
        for name in (GLOBAL, LOCAL)
    }

    reports: dict[str, dict] = {}
    for source, clients in plan.items():
        for index, client in enumerate(clients):
            for name in (GLOBAL, LOCAL):
                set_peft_model_state_dict(model, initial[name], adapter_name=name)
            batch = [items[r["id"]] for r in client]
            label = client_label(source, index)
            losses: dict[str, list[float]] = {GLOBAL: [], LOCAL: []}

            rounds = 1 if args.schedule == "feddpa-f" else args.rounds
            for round_index in range(rounds):
                model.set_adapter(GLOBAL)
                report = train_adapter(model, batch, pad_token_id=tok.pad_token_id, seed=args.seed)
                if report.skipped_steps or report.skipped_micro_batches:
                    raise SystemExit(f"{label}: the global phase skipped a step")
                losses[GLOBAL].extend(report.losses)

                if args.schedule == "feddpa-f":
                    # "the local adapter is initialized by the learned global adapter and
                    # directly fine-tuned" (FedDPA, Algorithm 1).
                    set_peft_model_state_dict(
                        model,
                        get_peft_model_state_dict(model, adapter_name=GLOBAL),
                        adapter_name=LOCAL,
                    )
                    model.set_adapter(LOCAL)
                else:
                    # "fine-tuned alongside the frozen global adapter": both apply, one trains.
                    model.base_model.set_adapter([GLOBAL, LOCAL])
                    freeze(model, GLOBAL)
                report = train_adapter(model, batch, pad_token_id=tok.pad_token_id, seed=args.seed)
                if report.skipped_steps or report.skipped_micro_batches:
                    raise SystemExit(
                        f"{label}: the local phase skipped a step in round {round_index}"
                    )
                losses[LOCAL].extend(report.losses)
                # The global phase of the next round needs its parameters back.
                unfreeze(model, GLOBAL)

            save_adapter(model, GLOBAL, args.adapters / label)
            save_adapter(model, LOCAL, args.adapters / f"{label}-local")
            reports[label] = {
                "source": source,
                "examples": len(batch),
                "changes": len({r["change_id"] for r in client}),
                "ids": [r["id"] for r in client],
                "global_first_loss": losses[GLOBAL][0],
                "global_last_loss": losses[GLOBAL][-1],
                "local_first_loss": losses[LOCAL][0],
                "local_last_loss": losses[LOCAL][-1],
            }
            print(
                f"{label}: global {losses[GLOBAL][0]:.3f} -> {losses[GLOBAL][-1]:.3f}, "
                f"local {losses[LOCAL][0]:.3f} -> {losses[LOCAL][-1]:.3f}",
                flush=True,
            )

    args.out.write_text(
        json.dumps(
            {
                "model_id": args.model_id,
                "schedule": args.schedule,
                "rounds": 1 if args.schedule == "feddpa-f" else args.rounds,
                "transmitted": GLOBAL,
                "withheld": LOCAL,
                "corpus_root": str(args.corpus_root),
                "legacy_corpus": args.legacy_corpus,
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
    print("DUAL_ADAPTER_UPDATES_OK")


if __name__ == "__main__":
    main()
