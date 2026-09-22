"""Re-read single-seed runs as one multi-seed study, under both gates.

Each extra seed runs as its own single-seed job (`SEEDS=2`, `SEEDS=3`), since three seeds in
one job would triple a walltime already near the limit. This merges their result files and
reads the registered gate (median seed) beside `crossed_gate` (seeds and changes resampled
together), under both estimands.

Runs are merged only if they are seeds of one study: the same model, corpora, split and
training size, and the same held-out examples per arm. `crossed_bootstrap` refuses runs over
different examples in any case; checking here names the file that differs.

Submit extra seeds with the same RUN_TAG as seed 1 plus SEEDS=, and the result name gains the
seed: RUN_TAG=qtfull SEEDS=2 writes `rq1-windows-qtfull-s2.json`.

    uv run --no-sync --no-active python scripts/crossed_reread.py \
        datasets/results/rq1-windows-qtfull.json datasets/results/rq1-windows-qtfull-s2.json \
        datasets/results/rq1-windows-qtfull-s3.json --out datasets/results/rq1-qtfull-seeds.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import pstdev
from typing import Any

from sphragis.corpus.redact import redact_text
from sphragis.experiment.grid import EvalRun, run_id
from sphragis.experiment.model import run_provenance
from sphragis.experiment.runner import to_clusters
from sphragis.experiment.walk import crossed_gate, gate
from sphragis.measure.stats import ESTIMATORS, paired_difference

parser = argparse.ArgumentParser()
parser.add_argument("runs", type=Path, nargs="+", help="single-seed result files")
parser.add_argument("--bootstrap-seed", type=int, default=7)
parser.add_argument("--out", type=Path, required=True)


def configuration(run: dict[str, Any]) -> dict[str, Any]:
    """Everything that must match for two runs to be seeds of one study.

    The held-out examples are checked separately, but they fix only the evaluation: a run
    trained at 788 examples scores the same held-out set as one trained at 1,517, and merging
    the two would average different studies as if they were seeds of one.

    Training size is read from what was trained on, not from `--train-size`: a size equal to
    the smallest set trains on exactly the rows an unsized run does. Steps per organization
    catch a change to epochs or batch size between the runs.
    """
    return {
        "model_id": run["model_id"],
        "split_seed": run["split_seed"],
        "equalize_train": run["equalize_train"],
        "max_new_tokens": run["max_new_tokens"],
        "corpora": {
            org: (c["source"], c.get("train_examples_equalized"), c["held_out_examples"])
            for org, c in run["corpora"].items()
        },
        "steps": {
            name.rsplit("-s", 1)[0]: (t["items"], t["steps"]) for name, t in run["training"].items()
        },
    }


def merge(paths: list[Path]) -> tuple[dict[str, list[dict[str, Any]]], tuple[int, ...]]:
    """Every run's adapter arms under one results map, and the seeds they carry."""
    merged: dict[str, list[dict[str, Any]]] = {}
    seeds: list[int] = []
    first: dict[str, Any] | None = None
    for path in paths:
        run = json.loads(path.read_text())
        if first is None:
            first = run
        elif (theirs := configuration(run)) != (ours := configuration(first)):
            changed = sorted(k for k in ours if ours[k] != theirs.get(k))
            raise SystemExit(f"{path} was not the same study as {paths[0]}: differs on {changed}")
        seeds.extend(run["seeds"])
        for arm, rows in run["results"].items():
            if arm.startswith("base|"):
                # The base model has no seed; keep the first run's evaluation of it.
                merged.setdefault(arm, rows)
                continue
            if arm in merged:
                raise SystemExit(f"{path} repeats arm {arm}: two runs used the same seed")
            reference = first["results"].get(f"{arm.rsplit('|', 1)[0]}|s{first['seeds'][0]}")
            # Compared through the redaction, because a stored run whose ids were redacted
            # and a fresh one whose ids were not are the same examples under different
            # spellings, and this guard exists to catch different examples.
            if reference is not None and {redact_text(r["id"]) for r in rows} != {
                redact_text(r["id"]) for r in reference
            }:
                raise SystemExit(f"{path} scored different examples on {arm}")
            merged[arm] = rows
    return merged, tuple(seeds)


def main() -> None:
    args = parser.parse_args()
    results, seeds = merge(args.runs)
    orgs = tuple(sorted({arm.split("|")[1] for arm in results if arm.startswith("base|")}))
    per_seed = {
        org: {
            seed: paired_difference(
                to_clusters(
                    results[run_id(EvalRun(f"adapter:{org}", org, seed))],
                    results[run_id(EvalRun(f"adapter:{other}", org, seed))],
                )
            )
            for seed in seeds
        }
        for org, other in ((orgs[0], orgs[1]), (orgs[1], orgs[0]))
    }
    readings: dict[str, dict[str, Any]] = {}
    for name, estimator in ESTIMATORS.items():
        registered = gate(
            results,
            orgs=orgs,
            seeds=seeds,
            bootstrap_seed=args.bootstrap_seed,
            estimator=estimator,
        )
        crossed = crossed_gate(
            results,
            orgs=orgs,
            seeds=seeds,
            bootstrap_seed=args.bootstrap_seed,
            estimator=estimator,
        )
        readings[name] = {
            "median_seed": {"verdict": registered["verdict"], "per_org": registered["binding"]},
            "crossed": crossed,
        }
        for rule, outcome in readings[name].items():
            print(f"{name:16} {rule:12} {outcome['verdict']}")
            for org, interval in outcome["per_org"].items():
                print(
                    f"    {org:10} {interval['estimate']:+.4f} "
                    f"[{interval['low']:+.4f}, {interval['high']:+.4f}]"
                )
    for org, estimates in per_seed.items():
        values = ", ".join(f"s{s} {v:+.4f}" for s, v in estimates.items())
        print(f"per-seed pooled contrast on {org}: {values}  (SD {pstdev(estimates.values()):.4f})")
    args.out.write_text(
        json.dumps(
            {
                "runs": [str(p) for p in args.runs],
                "seeds": seeds,
                "orgs": orgs,
                "bootstrap_seed": args.bootstrap_seed,
                "per_seed_pooled_contrast": per_seed,
                "readings": readings,
                "provenance": run_provenance(),
            },
            indent=2,
        )
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
