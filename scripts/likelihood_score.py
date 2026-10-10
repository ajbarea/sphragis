"""Score a stored H1 partition run by the log-probability of each held-out reference.

Every arm of the stored run, base and adapters, is scored again on exactly its examples, with no
generation: the reference's summed log-probability under the arm's model, its token count, and
their mean per token. The rows keep the stored run's exact match beside them, so a reader can
take either metric, and the verdict is recomputed by the registered gate on the mean per token.
The result has the stored run's shape, so `partition_pilot.py --metric` and check 5 read it as
they read the run itself.

Held-out rows are rebuilt from the run's own corpus root and windows (`window_split`) and must
be the stored arm's examples, keyed through the redaction the stored ids carry.

Run on the cluster: see scripts/likelihood_score.sbatch.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path

import torch

from sphragis.corpus.redact import redact_text
from sphragis.experiment.grid import EvalRun, run_id
from sphragis.experiment.holdout import window_split
from sphragis.experiment.model import (
    HFGenerator,
    load_tokenizer,
    reference_logprob,
    run_provenance,
    scorable,
)
from sphragis.experiment.neutral import source_root, source_windows
from sphragis.experiment.runner import build_prompt
from sphragis.experiment.walk import gate

#: The per-example field the verdict is read on: equal weight per example, as exact match has.
METRIC = "logprob_per_token"

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run", type=Path, required=True, help="the stored partition run")
parser.add_argument("--adapters", type=Path, required=True, help="where its adapters are saved")
parser.add_argument("--seeds", required=True, help="the stored run's, checked against it")
parser.add_argument("--train-size", type=int, help="the stored run's, checked; none for a placebo")
parser.add_argument("--out", type=Path, required=True)


def held_out_rows(run: dict, org: str) -> dict[str, dict]:
    """The held-out rows of `org`, keyed by their redacted id, refused unless they are the run's."""
    source = run["corpora"][org]["source"]
    train_window, eval_window = source_windows(source).split(" -> ")
    _, rows, _ = window_split(
        source_root(source), org, train_window=train_window, eval_window=eval_window
    )
    by_id = {redact_text(row["id"]): row for row in rows}
    stored = {redact_text(r["id"]) for r in run["results"][run_id(EvalRun("base", org, None))]}
    if set(by_id) != stored:
        raise SystemExit(
            f"{org}: the corpus holds {len(by_id)} held-out rows, the run {len(stored)}"
        )
    return by_id


def adapter_checked(run: dict, adapters: Path, org: str, seed: int) -> str:
    """The saved adapter the stored run scored, refused if its rank or recorded digest differs."""
    path = adapters / f"{org}-s{seed}"
    if json.loads((path / "adapter_config.json").read_text())["r"] != run["lora_rank"]:
        raise SystemExit(f"{path} is not rank {run['lora_rank']}")
    recorded = run["training"][f"{org}-s{seed}"].get("adapter_sha256")
    weights = hashlib.sha256((path / "adapter_model.safetensors").read_bytes()).hexdigest()
    if recorded is not None and weights != recorded:
        raise SystemExit(f"{path} is not the adapter the stored run scored")
    return str(path)


def score(generator: HFGenerator, rows: dict[str, dict], stored: list[dict]) -> list[dict]:
    scored = []
    for row in stored:
        total, tokens = reference_logprob(
            generator.model,
            generator.tokenizer,
            rows[redact_text(row["id"])],
            prompt_builder=build_prompt,
            device=generator.device,
        )
        scored.append(
            {
                "id": row["id"],
                "change_id": row["change_id"],
                "exact_match": row["exact_match"],
                "logprob": total,
                "tokens": tokens,
                METRIC: total / tokens,
            }
        )
    return scored


def main() -> None:
    args = parser.parse_args()
    run = json.loads(args.run.read_text())
    seeds = [int(s) for s in args.seeds.split(",")]
    if (run["seeds"], run["train_size"]) != (seeds, args.train_size):
        raise SystemExit(f"{args.run} has seeds {run['seeds']} at {run['train_size']}, not these")
    orgs = sorted(run["corpora"])
    rows = {org: held_out_rows(run, org) for org in orgs}
    # Every row is tokenized before any model loads, so one that cannot be scored costs no GPU.
    tokenizer = load_tokenizer(run["model_id"])
    for org in orgs:
        for row in rows[org].values():
            scorable(tokenizer, row, prompt_builder=build_prompt)
    adapters = {
        (org, seed): adapter_checked(run, args.adapters, org, seed)
        for org in orgs
        for seed in run["seeds"]
    }
    # One model load an arm's model: the base, then each adapter, each scored on every half.
    models: list[tuple[str | None, str | None, int | None]] = [(None, None, None)]
    models += [(org, adapters[org, seed], seed) for org in orgs for seed in run["seeds"]]
    results: dict[str, list[dict]] = {}
    dtypes: set[str] = set()
    for trained, adapter, seed in models:
        generator = HFGenerator(model_id=run["model_id"], adapter_path=adapter)
        dtypes.add(generator.computed_dtype)
        for evaluated in orgs:
            arm = run_id(
                EvalRun("base" if trained is None else f"adapter:{trained}", evaluated, seed)
            )
            results[arm] = score(generator, rows[evaluated], run["results"][arm])
            mean = sum(r[METRIC] for r in results[arm]) / len(results[arm])
            print(f"{arm:<44} {METRIC} {mean:.4f}", flush=True)
        del generator
        gc.collect()
        torch.cuda.empty_cache()
    if set(results) != set(run["results"]):
        raise SystemExit(f"scored {sorted(results)}, the run holds {sorted(run['results'])}")
    # The stored run's bootstrap seed, so the two verdicts differ only by the metric.
    verdict = gate(
        results, orgs=orgs, seeds=run["seeds"], metric=METRIC, bootstrap_seed=run["bootstrap_seed"]
    )
    copied = ("model_id", "seeds", "split_seed", "equalize_train", "train_size", "lora_rank")
    copied += ("bootstrap_seed",)
    args.out.write_text(
        json.dumps(
            {
                "provenance": run_provenance(),
                "scored_from": str(args.run),
                "metric": METRIC,
                **{key: run.get(key) for key in copied},
                "inference_dtype": sorted(dtypes),
                # Likelihood reads no decoder; these are the stored run's, for the exact match
                # its rows carry.
                "max_new_tokens": run["max_new_tokens"],
                "repetition_penalty": run.get("repetition_penalty"),
                "corpora": run["corpora"],
                "training": run["training"],
                "verdict": verdict,
                "results": results,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"verdict on {METRIC}: {verdict['verdict']}")
    print(f"wrote {args.out}")
    print("LIKELIHOOD_SCORE_OK")


if __name__ == "__main__":
    main()
