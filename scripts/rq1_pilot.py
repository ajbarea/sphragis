"""Pilot-scale RQ1: does the matched adapter beat the mismatched one, per organization?

The 2 by 2 grid plus the base arm, on one month per organization, through the same `walk`
and `gate` the confirmatory run will use. It proves the grid executes end to end on real
weights, which the Stage 1 checklist asks for, and gives a first look at the contrast. It
is not the confirmatory result: one month, held out by change rather than by time, and the
test windows stay sealed.
"""

import argparse
import gc
import hashlib
import json
from pathlib import Path

import torch

from sphragis.corpus.load import derived_file_rows
from sphragis.corpus.pipeline import run_dedup
from sphragis.experiment.decomposition import (
    FOREIGN_POOL_SHA256,
    FOREIGN_SOURCES,
    FOREIGN_SPANS,
    halves,
    is_cpp,
    matched_foreign_arms,
    require_registered_source,
)
from sphragis.experiment.holdout import (
    cleared_pool,
    equalize_training,
    holdout_by_change,
    pool_digest,
    verbatim_overlap,
    window_split,
)
from sphragis.experiment.model import (
    MAX_NEW_TOKENS,
    MODEL_ID,
    REGISTERED_RANK,
    REPETITION_PENALTY,
    TRAINING,
    HFGenerator,
    attach_adapter,
    run_provenance,
    train_adapter,
)
from sphragis.experiment.neutral import (
    LEAKAGE_MAX_RATE,
    LEAKAGE_THRESHOLD,
    Check,
    apparatus_holds,
    leakage_check,
    manipulation_check,
    non_degeneracy,
    positive_control,
)
from sphragis.experiment.runner import build_prompt, require_unique_ids
from sphragis.experiment.runs import reused_adapter, stored_foreign_pool, trained_keys
from sphragis.experiment.training import supervised
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
parser.add_argument(
    "--legacy-corpus",
    action="store_true",
    help="with --corpus: read files not cut under the current label rules, to reproduce an "
    "earlier result; recorded in the output",
)
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
    default=LEAKAGE_MAX_RATE,
    help="outcome-neutral test 4: the registered ceiling on the near-duplicate share",
)
parser.add_argument(
    "--leakage-threshold",
    type=float,
    default=LEAKAGE_THRESHOLD,
    help="outcome-neutral test 4: the registered Jaccard at which a pair is a near-duplicate",
)
parser.add_argument(
    "--equalize-train",
    action="store_true",
    help="subsample every organization's training set to the smallest one's size",
)
parser.add_argument(
    "--train-size",
    type=int,
    help="with --equalize-train: cut every training set to this size, below the smallest",
)
parser.add_argument(
    "--lora-rank",
    type=int,
    default=REGISTERED_RANK,
    help="the registered rank by default; the conditional branch reruns an arm at 256",
)
parser.add_argument("--max-new-tokens", type=int, default=MAX_NEW_TOKENS)
parser.add_argument(
    "--repetition-penalty",
    type=float,
    default=REPETITION_PENALTY,
    help="the registered 1.0 by default; 1.1 rescores a run from before the registration",
)
parser.add_argument(
    "--reuse-from",
    type=Path,
    help="the stored result whose adapters, saved under --adapters, are evaluated again untrained",
)
parser.add_argument(
    "--foreign-pool",
    type=Path,
    help="H2: --foreign-source's cleared pool (scripts/clear_foreign_pool.py's file), which each "
    "half's matched foreign arm is drawn from (holdout.matched_foreign_draw)",
)
parser.add_argument("--foreign-source", choices=FOREIGN_SOURCES, help="with --foreign-pool")
parser.add_argument(
    "--dry-run",
    action="store_true",
    help="report the split and exit, before any model is loaded: runs on a login node",
)
args = parser.parse_args()

seeds = tuple(int(s) for s in args.seeds.split(","))
if args.train_size is not None and not args.equalize_train:
    raise SystemExit("--train-size needs --equalize-train")
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
        rows = derived_file_rows(Path(path), legacy=args.legacy_corpus)
        kept, removed = run_dedup(rows)
        train, evaluate_on = holdout_by_change(kept, seed=args.split_seed)
        source_note, examples = f"holdout seed {args.split_seed} over {path}", len(rows)
    else:
        # Study shape: train and evaluate on separate time windows.
        try:
            train, evaluate_on, read = window_split(
                args.root, org, train_window=args.train_window, eval_window=args.eval_window
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error
        source_note, examples, removed = read["source"], read["examples"], read["dedup_removed"]
    leaked = verbatim_overlap(train, evaluate_on)
    assert not leaked, f"{org}: {len(leaked)} held-out examples repeat a training pair"
    train_rows[org], held_out[org] = train, evaluate_on
    summary[org] = {
        "source": source_note,
        "legacy_corpus": bool(args.corpus) and args.legacy_corpus,
        "examples": examples,
        "dedup_removed": removed,
        "train_examples": len(train),
        "held_out_examples": len(evaluate_on),
        "held_out_changes": len({r["change_id"] for r in evaluate_on}),
    }
    print(f"{org}: {summary[org]}", flush=True)

if args.equalize_train:
    train_rows = equalize_training(train_rows, seed=args.split_seed, size=args.train_size)
    for org in orgs:
        summary[org]["train_examples_equalized"] = len(train_rows[org])
    print(f"equalized training sets: { {o: len(train_rows[o]) for o in orgs} }", flush=True)

# H2's matched foreign arms: per evaluated half, an adapter trained on the source's pool, as many
# C++ examples as the sibling half's draw and the rest of N from its other files, drawn with the
# run's training seed (research log, 2026-10-14). Scored on that half alone.
foreign: dict[str, str] = {}
foreign_record: dict | None = None
if bool(args.foreign_pool) != bool(args.foreign_source):
    raise SystemExit("--foreign-pool and --foreign-source come together")
if args.foreign_pool:
    if args.corpus or len(orgs) != 2 or orgs != halves(orgs[0].removesuffix("-a")):
        raise SystemExit("foreign arms are drawn for one organization's two halves under --root")
    if args.train_size is None or len(seeds) != 1:
        raise SystemExit("foreign arms need --train-size and one seed, the partition run's own")
    # The halves' adapters are the H1 run's, read again: trained afresh here, they would replace
    # the adapters H1 read and every later reuse of them would be refused.
    if not args.reuse_from:
        raise SystemExit("foreign arms are added to a stored run: pass its --reuse-from")
    try:
        require_registered_source(args.foreign_source)
        pool = cleared_pool(
            args.foreign_pool, args.foreign_source, FOREIGN_SPANS[args.foreign_source]
        )
        # The pool the stop rule covered, cleared of Qt's copies, and no other.
        if pool_digest(pool) != FOREIGN_POOL_SHA256:
            raise ValueError(
                f"{args.foreign_pool} is not the registered pool (sha256 {FOREIGN_POOL_SHA256})"
            )
        drawn, foreign = matched_foreign_arms(
            train_rows,
            pool,
            org=orgs[0].removesuffix("-a"),
            source=args.foreign_source,
            seed=seeds[0],
        )
    except ValueError as error:
        raise SystemExit(f"foreign arms: {error}") from error
    train_rows.update(drawn)
    # A draw that copies one of its half's held-out pairs is refused, as an own half's is above.
    for arm, evaluated in foreign.items():
        copied = verbatim_overlap(train_rows[arm], held_out[evaluated])
        if copied:
            raise SystemExit(f"{arm}: {len(copied)} of {evaluated}'s held-out pairs in its draw")
    foreign_record = {
        "source": args.foreign_source,
        "pool": str(args.foreign_pool),
        "pool_sha256": pool_digest(pool),
        "pool_examples": len(pool),
        "arms": {
            arm: {
                "evaluated": foreign[arm],
                "in_language": sum(1 for row in rows if is_cpp(row)),
                "other": sum(1 for row in rows if not is_cpp(row)),
            }
            for arm, rows in drawn.items()
        },
    }
    print(f"foreign arms: {foreign_record['arms']}", flush=True)
    # An arm reused from a stored run was trained on that run's draw, so the pool must be its.
    if args.reuse_from and set(foreign) & {
        k.rsplit("-s", 1)[0] for k in trained_keys(args.reuse_from)
    }:
        stored_pool = stored_foreign_pool(args.reuse_from)
        if stored_pool is None or stored_pool["pool_sha256"] != foreign_record["pool_sha256"]:
            raise SystemExit(f"{args.reuse_from} drew its foreign arms from another pool")


# Before any model loads, and so inside the dry run: the arms are paired by example id, so a
# repeated id in an evaluation set is fatal, and checked only at scoring it cost job 148093
# four GPU-hours for no result.
for org in orgs:
    require_unique_ids(held_out[org], label=f"{org} evaluation set")

# Test 4 needs only the data, so it runs before any model loads: on the corpus's own training
# rows and, since a contrast credits whatever a sibling's adapter gains, on every other corpus's
# training rows into this held-out set. A failure halts here, recorded, before any GPU time.
leakage = [
    leakage_check(
        train_rows[source],
        held_out[org],
        threshold=args.leakage_threshold,
        max_rate=args.leakage_max_rate,
        label=org if source == org else f"{source}->{org}",
    )
    for org in orgs
    for source in orgs
]
# A foreign arm is credited only on its own half, so its draw is checked against that half.
leakage += [
    leakage_check(
        train_rows[arm],
        held_out[evaluated],
        threshold=args.leakage_threshold,
        max_rate=args.leakage_max_rate,
        label=f"{arm}->{evaluated}",
    )
    for arm, evaluated in foreign.items()
]
for check in leakage:
    print(f"outcome-neutral {check.name:<32} {'pass' if check.passed else 'FAIL'}", flush=True)
if not all(check.passed for check in leakage):
    args.out.write_text(
        json.dumps(
            {
                "provenance": run_provenance(),
                "corpora": summary,
                "foreign": foreign,
                "foreign_pool": foreign_record,
                "halted": "leakage",
                "outcome_neutral": {
                    "apparatus_holds": False,
                    "leakage_max_rate": args.leakage_max_rate,
                    "leakage_threshold": args.leakage_threshold,
                    "checks": [
                        {"name": c.name, "passed": c.passed, "evidence": c.evidence}
                        for c in leakage
                    ],
                },
            },
            indent=2,
        )
        + "\n"
    )
    # A foreign arm's draw failing alone leaves H1 as it was; H2 is what is not read then.
    failed = [c for c in leakage if not c.passed]
    unread = "H2" if all(c.name.startswith("leakage:foreign-") for c in failed) else "H1"
    raise SystemExit(f"APPARATUS FAILS at test 4, before training: {unread} would not be read")

if args.dry_run:
    # Everything above is CPU: loading, dedup, the split and the leakage check. Stopping here
    # is what lets a login node check the data the job will train on, before the queue.
    print("DRY RUN: split only, no model loaded", flush=True)
    raise SystemExit(0)


stored_training = trained_keys(args.reuse_from) if args.reuse_from else set()


class InProcessTrainer:
    """Trains one adapter, saves it, and releases the GPU before returning its path."""

    def __init__(self) -> None:
        self.reports: dict[str, dict] = {}
        self.losses: dict[str, list[float]] = {}
        self.reused_checks: dict[str, Check] = {}

    def train(self, org: str, seed: int) -> str:
        target = args.adapters / f"{org}-s{seed}"
        # Under --reuse-from every adapter is the stored run's, except a foreign arm it never
        # trained: an H2 run reuses its H1 run's halves and trains only their matched arms.
        if args.reuse_from and (org not in foreign or f"{org}-s{seed}" in stored_training):
            return self.reuse(org, seed, target)
        if org in foreign and target.exists():
            # Another result may have scored the adapter there; retraining would change it.
            raise SystemExit(f"{target} already holds a foreign arm; reuse it or remove it first")
        model, tok = attach_adapter(MODEL_ID, seed, rank=args.lora_rank)
        if tok.pad_token_id is None:
            tok.pad_token = tok.eos_token
        items, refused = supervised(
            tok, train_rows[org], prompt_builder=build_prompt, max_length=TRAINING["max_seq_length"]
        )
        report = train_adapter(model, items, pad_token_id=tok.pad_token_id, seed=seed)
        epochs = int(TRAINING["epochs"])
        assert report.skipped_steps == 0, f"{org} s{seed}: {report.skipped_steps} steps skipped"
        self.losses[f"{org}-s{seed}"] = list(report.losses)
        model.save_pretrained(target)
        weights = hashlib.sha256((target / "adapter_model.safetensors").read_bytes()).hexdigest()
        self.reports[f"{org}-s{seed}"] = {
            # Recorded so a later rescoring can check it reads these weights (`adapter_checked`).
            "adapter_sha256": weights,
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

    def reuse(self, org: str, seed: int, target: Path) -> str:
        """The adapter a stored run trained, with that run's training report and test 3."""
        key = f"{org}-s{seed}"
        try:
            report, check = reused_adapter(
                args.reuse_from, key, target, train_size=args.train_size, rank=args.lora_rank
            )
        except ValueError as error:
            raise SystemExit(f"--reuse-from: {error}") from error
        self.reports[key] = report
        self.reused_checks[key] = Check(check["name"], check["passed"], check["evidence"])
        print(f"reusing {target} from {args.reuse_from}", flush=True)
        return str(target)


inference_dtypes: set[str] = set()
repetition_penalties: set[float] = set()


def generator_for(adapter: str | None) -> HFGenerator:
    gc.collect()
    torch.cuda.empty_cache()
    print(f"evaluating with {adapter or 'base'}", flush=True)
    generator = HFGenerator(
        adapter_path=adapter,
        max_new_tokens=args.max_new_tokens,
        repetition_penalty=args.repetition_penalty,
    )
    inference_dtypes.add(generator.computed_dtype)
    repetition_penalties.add(generator.effective_repetition_penalty)
    return generator


trainer = InProcessTrainer()
results = walk(
    orgs=orgs,
    seeds=seeds,
    windows=held_out,
    trainer=trainer,
    generator_for=generator_for,
    foreign=foreign,
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
        # A reused adapter's test 3 is the stored run's, which trained it.
        if f"{org}-s{seed}" in trainer.reused_checks:
            checks.append(trainer.reused_checks[f"{org}-s{seed}"])
            continue
        checks.append(
            manipulation_check(
                trainer.losses[f"{org}-s{seed}"],
                epochs=int(TRAINING["epochs"]),
                adapter_weight_norm=trainer.reports[f"{org}-s{seed}"]["adapter_weight_norm"],
                label=f"{org}|s{seed}",
            )
        )
# A foreign arm is not its half's own adapter, so test 2 does not apply to it; test 3 does.
for arm in foreign:
    for seed in seeds:
        if f"{arm}-s{seed}" in trainer.reused_checks:
            checks.append(trainer.reused_checks[f"{arm}-s{seed}"])
            continue
        checks.append(
            manipulation_check(
                trainer.losses[f"{arm}-s{seed}"],
                epochs=int(TRAINING["epochs"]),
                adapter_weight_norm=trainer.reports[f"{arm}-s{seed}"]["adapter_weight_norm"],
                label=f"{arm}|s{seed}",
            )
        )
checks += leakage
checks.append(non_degeneracy(results))
for check in checks:
    print(f"outcome-neutral {check.name:<32} {'pass' if check.passed else 'FAIL'}", flush=True)
holds = apparatus_holds(checks)
failed = [c for c in checks if not c.passed]
unread = "H2" if failed and all("foreign-" in c.name for c in failed) else "H1"
print(f"APPARATUS {'holds' if holds else f'FAILS: {unread} would not be read'}", flush=True)

args.out.write_text(
    json.dumps(
        {
            "provenance": run_provenance(),
            "model_id": MODEL_ID,
            # Recorded, not assumed: a rank-256 rerun and the registered run must never be
            # told apart by their filenames alone.
            "lora_rank": args.lora_rank,
            "seeds": seeds,
            "split_seed": args.split_seed,
            "equalize_train": args.equalize_train,
            # Read back from the loaded models, not from what was asked for.
            "inference_dtype": sorted(inference_dtypes),
            "train_size": args.train_size,
            "bootstrap_seed": args.bootstrap_seed,
            "max_new_tokens": args.max_new_tokens,
            "repetition_penalty": sorted(repetition_penalties),
            "corpora": summary,
            # Each matched foreign arm and the half it is scored on, and its pool; empty without H2.
            "foreign": foreign,
            "foreign_pool": foreign_record,
            "training": trainer.reports,
            "verdict": verdict,
            "outcome_neutral": {
                "apparatus_holds": holds,
                "leakage_max_rate": args.leakage_max_rate,
                "leakage_threshold": args.leakage_threshold,
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
