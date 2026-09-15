"""Pilot-scale RQ1: does the matched adapter beat the mismatched one, per organization?

The 2 by 2 grid plus the base arm, on one month per organization, through the same `walk`
and `gate` the confirmatory run will use. It proves the grid executes end to end on real
weights, which the Stage 1 checklist asks for, and gives a first look at the contrast. It
is not the confirmatory result: one month, held out by change rather than by time, and the
test windows stay sealed.
"""

import argparse
import gc
import json
from pathlib import Path

import torch

from sphragis.corpus.cli import WINDOWS
from sphragis.corpus.pipeline import run_dedup, run_split
from sphragis.experiment.holdout import (
    equalize_training,
    holdout_by_change,
    split_by_window,
    verbatim_overlap,
)
from sphragis.experiment.model import (
    MAX_NEW_TOKENS,
    MODEL_ID,
    TRAINING,
    HFGenerator,
    attach_adapter,
    train_adapter,
)
from sphragis.experiment.neutral import (
    apparatus_holds,
    leakage_check,
    manipulation_check,
    non_degeneracy,
    positive_control,
)
from sphragis.experiment.runner import build_prompt
from sphragis.experiment.training import build_supervised
from sphragis.experiment.walk import gate, walk

parser = argparse.ArgumentParser(description=__doc__)
source = parser.add_mutually_exclusive_group(required=True)
source.add_argument(
    "--corpus",
    action="append",
    metavar="ORG=PATH",
    help="one month per organization, split by change holdout: the pilot's stand-in",
)
source.add_argument(
    "--root",
    type=Path,
    help="built corpus root; splits by TIME WINDOW, which is the study's own split",
)
parser.add_argument("--org", action="append", default=[], help="with --root; repeatable")
parser.add_argument("--train-window", default="train")
parser.add_argument("--eval-window", default="dev", help="never the sealed test window")
parser.add_argument("--seeds", default="1", help="comma-separated; an odd count")
parser.add_argument("--split-seed", type=int, default=0)
parser.add_argument("--bootstrap-seed", type=int, default=7)
parser.add_argument("--adapters", type=Path, default=Path("adapters"))
parser.add_argument("--out", type=Path, default=Path("rq1-pilot.json"))
parser.add_argument(
    "--leakage-max-rate",
    type=float,
    default=0.01,
    help="outcome-neutral test 4 threshold; PROVISIONAL until fixed in the Stage 1 report",
)
parser.add_argument(
    "--equalize-train",
    action="store_true",
    help="subsample every organization's training set to the smallest one's size",
)
parser.add_argument("--max-new-tokens", type=int, default=MAX_NEW_TOKENS)
parser.add_argument(
    "--dry-run",
    action="store_true",
    help="report the split and exit, before any model is loaded: runs on a login node",
)
args = parser.parse_args()

seeds = tuple(int(s) for s in args.seeds.split(","))
orgs = tuple(sorted(dict(e.split("=", 1) for e in args.corpus) if args.corpus else args.org))
if not orgs:
    raise SystemExit("--root needs at least one --org")

train_rows: dict[str, list[dict]] = {}
held_out: dict[str, list[dict]] = {}
summary: dict[str, dict] = {}
for org in orgs:
    if args.corpus:
        # Pilot shape: one month, held out by change. No time separation, so it measures
        # whether the apparatus runs, not the contrast the report claims.
        path = dict(e.split("=", 1) for e in args.corpus)[org]
        rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line]
        kept, removed = run_dedup(rows)
        train, evaluate_on = holdout_by_change(kept, seed=args.split_seed)
        source_note = f"holdout seed {args.split_seed} over {path}"
    else:
        # Study shape: train and evaluate on separate time windows.
        directory = args.root / org / "examples"
        rows = [
            json.loads(line)
            for month in sorted(directory.glob("*.jsonl"))
            for line in month.read_text().splitlines()
            if line
        ]
        if not rows:
            raise SystemExit(f"no examples under {directory}; build first")
        kept, removed = run_dedup(rows)
        windows, straddling, unassigned = run_split(kept, WINDOWS)
        if straddling or unassigned:
            raise SystemExit(
                f"{org}: {len(straddling)} straddling and {len(unassigned)} unassigned changes"
            )
        train, evaluate_on = split_by_window(
            windows, train_window=args.train_window, eval_window=args.eval_window
        )
        source_note = f"{args.train_window} -> {args.eval_window} windows under {directory}"
    leaked = verbatim_overlap(train, evaluate_on)
    assert not leaked, f"{org}: {len(leaked)} held-out examples repeat a training pair"
    train_rows[org], held_out[org] = train, evaluate_on
    summary[org] = {
        "source": source_note,
        "examples": len(rows),
        "dedup_removed": removed,
        "train_examples": len(train),
        "held_out_examples": len(evaluate_on),
        "held_out_changes": len({r["change_id"] for r in evaluate_on}),
    }
    print(f"{org}: {summary[org]}", flush=True)

if args.equalize_train:
    train_rows = equalize_training(train_rows, seed=args.split_seed)
    for org in orgs:
        summary[org]["train_examples_equalized"] = len(train_rows[org])
    print(f"equalized training sets: { {o: len(train_rows[o]) for o in orgs} }", flush=True)


if args.dry_run:
    # Everything above is CPU: loading, dedup, the split and the leakage assertion. Stopping
    # here is what lets a login node check the data the job will train on, before the queue.
    print("DRY RUN: split only, no model loaded", flush=True)
    raise SystemExit(0)


class InProcessTrainer:
    """Trains one adapter, saves it, and releases the GPU before returning its path."""

    def __init__(self) -> None:
        self.reports: dict[str, dict] = {}
        self.losses: dict[str, list[float]] = {}

    def train(self, org: str, seed: int) -> str:
        model, tok = attach_adapter(MODEL_ID, seed)
        if tok.pad_token_id is None:
            tok.pad_token = tok.eos_token
        items, refused = [], 0
        for r in train_rows[org]:
            try:
                items.append(
                    build_supervised(
                        tok, r, prompt_builder=build_prompt, max_length=TRAINING["max_seq_length"]
                    )
                )
            except ValueError:
                refused += 1
        report = train_adapter(model, items, pad_token_id=tok.pad_token_id, seed=seed)
        epochs = int(TRAINING["epochs"])
        assert report.skipped_steps == 0, f"{org} s{seed}: {report.skipped_steps} steps skipped"
        target = args.adapters / f"{org}-s{seed}"
        self.losses[f"{org}-s{seed}"] = list(report.losses)
        model.save_pretrained(target)
        self.reports[f"{org}-s{seed}"] = {
            "items": len(items),
            "refused": refused,
            "steps": report.steps,
            "applied_steps": report.applied_steps,
            "first_loss": report.losses[0],
            "last_loss": report.losses[-1],
            # The last step holds only the examples left over after full batches (1 of 145,
            # 6 of 422), so its loss is noise. The final epoch's mean is the readable figure.
            "adapter_weight_norm": report.adapter_weight_norm,
            "final_epoch_mean_loss": sum(report.losses[-report.steps // epochs :])
            / max(1, len(report.losses[-report.steps // epochs :])),
        }
        print(f"trained {target}: {self.reports[f'{org}-s{seed}']}", flush=True)
        del model
        gc.collect()
        torch.cuda.empty_cache()
        return str(target)


def generator_for(adapter: str | None) -> HFGenerator:
    gc.collect()
    torch.cuda.empty_cache()
    print(f"evaluating with {adapter or 'base'}", flush=True)
    return HFGenerator(adapter_path=adapter, max_new_tokens=args.max_new_tokens)


trainer = InProcessTrainer()
results = walk(
    orgs=orgs, seeds=seeds, windows=held_out, trainer=trainer, generator_for=generator_for
)
for run, rows in results.items():
    n = len(rows)
    print(
        f"{run:<28} EM={sum(r['exact_match'] for r in rows) / n:.3f} "
        f"normEM={sum(r['normalized_exact_match'] for r in rows) / n:.3f} "
        f"sim={sum(r['edit_similarity'] for r in rows) / n:.3f}",
        flush=True,
    )

verdict = gate(results, orgs=orgs, seeds=seeds, bootstrap_seed=args.bootstrap_seed)
for org, interval in verdict["binding"].items():
    print(
        f"RQ1 {org}: matched - mismatched EM {interval['estimate']:+.3f} "
        f"[{interval['low']:+.3f}, {interval['high']:+.3f}] "
        f"over {interval['clusters']:.0f} changes",
        flush=True,
    )
print(f"GATE (pilot scale, not confirmatory): {verdict['verdict']}", flush=True)

# Outcome-neutral tests. At confirmatory scale a failure here halts the study before the gate
# is read; at pilot scale they are reported beside it.
checks = []
for org in orgs:
    for seed in seeds:
        checks.append(
            positive_control(
                results[f"adapter:{org}|{org}|s{seed}"],
                results[f"base|{org}"],
                label=f"{org}|s{seed}",
                bootstrap_seed=args.bootstrap_seed,
            )
        )
        checks.append(
            manipulation_check(
                trainer.losses[f"{org}-s{seed}"],
                epochs=int(TRAINING["epochs"]),
                adapter_weight_norm=trainer.reports[f"{org}-s{seed}"]["adapter_weight_norm"],
                label=f"{org}|s{seed}",
            )
        )
    checks.append(
        leakage_check(train_rows[org], held_out[org], max_rate=args.leakage_max_rate, label=org)
    )
checks.append(non_degeneracy(results))
for check in checks:
    print(f"outcome-neutral {check.name:<32} {'pass' if check.passed else 'FAIL'}", flush=True)
holds = apparatus_holds(checks)
print(f"APPARATUS {'holds' if holds else 'FAILS: H1 would not be read'}", flush=True)

args.out.write_text(
    json.dumps(
        {
            "model_id": MODEL_ID,
            "seeds": seeds,
            "split_seed": args.split_seed,
            "equalize_train": args.equalize_train,
            "bootstrap_seed": args.bootstrap_seed,
            "max_new_tokens": args.max_new_tokens,
            "corpora": summary,
            "training": trainer.reports,
            "verdict": verdict,
            "outcome_neutral": {
                "apparatus_holds": holds,
                "leakage_max_rate_provisional": args.leakage_max_rate,
                "checks": [
                    {"name": c.name, "passed": c.passed, "evidence": c.evidence} for c in checks
                ],
            },
            "results": results,
        },
        indent=2,
        default=str,
    )
)
print(f"wrote {args.out}")
print("RQ1_PILOT_OK")
