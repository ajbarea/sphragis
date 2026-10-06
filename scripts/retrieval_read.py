"""Read the retrieval comparator: its contrasts over partitions, beside the adapters' H1.

Takes an organization's `retrieval_comparator.py --pools foreign` job, one `--pools halves` job
per partition in admissible order (the first `retrieval.PARTITIONS`), and the adapters'
`partition_run` result on each partition from `--results` (`retrieval.adapter_run`). Each
partition is a run as an adapter partition run is, its two pools in place of its two adapters,
and every contrast is read with the registered crossed runs-by-changes estimator
(`partitioned_crossed_draws`) on the examples every run scored:

- own minus sibling, the counterpart of H1's half-split contrast;
- sibling minus foreign, of the organization contrast (the foreign organization's two half
  pools averaged per example, as its two adapters are);
- own minus none, what retrieval from the own half adds over the base model.

The adapters' own minus sibling is read beside them over the same partitions and examples.

Reading rule, fixed before any generation (research log, 2026-10-05): own minus sibling at each
k, on its 95% interval, reads "carries a half-split contrast" when the lower bound clears the
SESOI, "reversed" when the upper bound sits below minus the SESOI, "carries none as large as the
SESOI" when the interval sits inside the SESOI band, and "inconclusive" otherwise; the adapters'
interval is read by the same rule. The other two contrasts are reported, not read. Beside the
reading, own minus sibling is read again without every target whose own or sibling shot is a
near-duplicate of it at test 4's threshold. Exploratory: no
reading here binds a verdict.

    uv run --no-sync python scripts/retrieval_read.py --org openstack --foreign wikimedia \\
        --admissible datasets/results/admissible-partitions-openstack.json \\
        --foreign-job retrieval-foreign-openstack.json \\
        --halves retrieval-halves-openstack-p2.json ... \\
        --results datasets/results \\
        --out retrieval-openstack.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import fmean
from typing import Any

from sphragis.experiment.decomposition import (
    base_clusters,
    halves,
    meaningful,
    organization_clusters,
    project_clusters,
    within_sesoi,
)
from sphragis.experiment.grid import EvalRun, run_id
from sphragis.experiment.neutral import LEAKAGE_THRESHOLD
from sphragis.experiment.partitions import on_common_examples
from sphragis.experiment.retrieval import KS, adapter_run, arm_key, condition, first_partitions
from sphragis.measure.stats import equal_halves, partitioned_crossed_draws, percentile_interval
from sphragis.provenance import provenance_header

CONFIDENCE = 0.95

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--org", required=True)
parser.add_argument("--foreign", required=True)
parser.add_argument("--admissible", type=Path, required=True)
parser.add_argument("--foreign-job", type=Path, required=True)
parser.add_argument("--halves", type=Path, nargs="+", required=True, help="admissible order")
parser.add_argument("--results", type=Path, required=True, help="the adapters' partition runs")
parser.add_argument("--bootstrap-seed", type=int, default=7)
parser.add_argument("--resamples", type=int, default=10_000)
parser.add_argument("--out", type=Path, required=True)

Rows = list[dict[str, Any]]


def load(path: Path, **expected: Any) -> dict[str, Any]:
    """A generation job's report, refused unless it is the job named and complete."""
    job = json.loads(path.read_text())
    found = {name: job.get(name) for name in expected}
    if found != expected:
        raise SystemExit(f"{path}: {found}, not {expected}")
    if "results" not in job:
        raise SystemExit(f"{path} is a dry run: no results")
    return job


def run_results(
    own: Mapping[str, Rows], foreign: Mapping[str, Rows], *, org: str, foreign_org: str
) -> dict[str, Rows]:
    """One partition's arms, the foreign and base arms cut to each of its halves' examples."""
    out = dict(own)
    for half in halves(org):
        ids = {row["id"] for row in own[arm_key(KS[0], half, half)]}
        cut = [(run_id(EvalRun("base", half, None)), run_id(EvalRun("base", org, None)))]
        cut += [
            (arm_key(k, pool, half), arm_key(k, pool, org))
            for k in KS
            for pool in halves(foreign_org)
        ]
        for key, source in cut:
            rows = [row for row in foreign[source] if row["id"] in ids]
            missing = ids - {row["id"] for row in rows}
            if missing:
                raise SystemExit(f"{source} lacks {len(missing)} of {half}'s examples")
            out[key] = rows
    return out


def contrast(clusters: Sequence[Sequence[Sequence[Any]]], seed: int, resamples: int) -> dict:
    """A contrast over runs: the registered estimate, its interval, and each run's estimate."""
    estimate, draws = partitioned_crossed_draws(clusters, seed=seed, resamples=resamples)
    low, high = percentile_interval(draws, CONFIDENCE)
    return {
        "estimate": estimate,
        "confidence": CONFIDENCE,
        "low": low,
        "high": high,
        "per_run": [equal_halves(run) for run in clusters],
    }


def reading(cell: dict) -> dict:
    """An own-minus-sibling contrast with the rule fixed before generation applied to it."""
    low, high = cell["low"], cell["high"]
    if meaningful(low):
        verdict = "carries a half-split contrast"
    elif meaningful(-high):
        verdict = "reversed: the sibling half carries the contrast"
    elif within_sesoi(low, high):
        verdict = "carries none as large as the SESOI"
    else:
        verdict = "inconclusive"
    return {**cell, "reading": verdict}


def adapter_runs(
    results: Path,
    org: str,
    order: Sequence[int],
    size: int,
    ids: set[str],
    decoding: Mapping[str, Any],
) -> tuple[list[Path], list[dict[str, Rows]], Any]:
    """The adapters' development-window runs on the same partitions, cut to `ids`.

    Refused unless every run decoded as the retrieval generator did (`decoding`: model and
    output budget) and all were trained at one LoRA rank, which is returned.
    """
    paths, runs, ranks = [], [], set()
    for partition in order:
        try:
            path, run, _, _ = adapter_run(
                results, org=org, partition=partition, order=order, size=size
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error
        found = {key: run.get(key) for key in decoding}
        if found != dict(decoding):
            raise SystemExit(f"{path} decoded with {found}, the retrieval arms with {decoding}")
        ranks.add(run.get("lora_rank"))
        scored = {r["id"] for rows in run["results"].values() for r in rows}
        if ids - scored:
            raise SystemExit(f"{path} lacks {len(ids - scored)} of the retrieval examples")
        paths.append(path)
        runs.append(
            {arm: [r for r in rows if r["id"] in ids] for arm, rows in run["results"].items()}
        )
    if len(ranks) != 1:
        raise SystemExit(f"the adapter runs were trained at ranks {sorted(map(str, ranks))}")
    return paths, runs, ranks.pop()


def near_duplicate_targets(runs: Sequence[Mapping[str, Rows]], keys: Sequence[str]) -> set[str]:
    """Targets whose closest shot in any of `keys`, in any run, is a near-duplicate of them."""
    return {
        row["id"]
        for results in runs
        for key in keys
        for row in results[key]
        if row["shot_jaccard"] >= LEAKAGE_THRESHOLD
    }


def main() -> None:
    args = parser.parse_args()
    try:
        order, size = first_partitions(json.loads(args.admissible.read_text()), org=args.org)
    except ValueError as error:
        raise SystemExit(f"{args.admissible}: {error}") from error
    if len(args.halves) != len(order):
        raise SystemExit(f"{len(args.halves)} --halves files, not the fixed {len(order)}")
    fixed = {"org": args.org, "train_size": size, "ks": list(KS), "limit": None}
    foreign_job = load(args.foreign_job, pools="foreign", foreign=args.foreign, **fixed)
    # Every arm is compared with arms from other jobs, so all of them ran one generator.
    generator = {
        key: foreign_job.get(key)
        for key in ("generator", "inference_dtype", "model_id", "max_new_tokens")
    }
    if None in generator.values():
        raise SystemExit(f"{args.foreign_job} does not record its generator")
    fixed |= generator
    runs = [
        run_results(
            load(path, pools="halves", partition=partition, **fixed)["results"],
            foreign_job["results"],
            org=args.org,
            foreign_org=args.foreign,
        )
        for path, partition in zip(args.halves, order, strict=True)
    ]
    seed, resamples = args.bootstrap_seed, args.resamples
    first, second = halves(args.org)
    per_k: dict[str, Any] = {}
    adapters = None
    for k in KS:
        try:
            cut, examples = on_common_examples(runs, condition=condition(k))
        except ValueError as error:
            raise SystemExit(str(error)) from error
        read = {"org": args.org, "seed": None, "condition": condition(k)}
        arms = {
            "none": {h: run_id(EvalRun("base", h, None)) for h in (first, second)},
            "own": {h: arm_key(k, h, h) for h in (first, second)},
            "sibling": {first: arm_key(k, second, first), second: arm_key(k, first, second)},
        }
        per_k[str(k)] = {
            **examples,
            # Each run's arm pooled over its two halves' examples, then averaged over runs.
            "exact_match": {
                arm: fmean(
                    fmean(row["exact_match"] for key in keys.values() for row in r[key])
                    for r in cut
                )
                for arm, keys in arms.items()
            },
            "own_minus_sibling": reading(
                contrast([project_clusters(r, **read) for r in cut], seed, resamples)
            ),
            "sibling_minus_foreign": contrast(
                [organization_clusters(r, foreign=args.foreign, **read) for r in cut],
                seed,
                resamples,
            ),
            "own_minus_none": contrast([base_clusters(r, **read) for r in cut], seed, resamples),
        }
        # Beside it, the same reading without any target whose own or sibling shot is a
        # near-duplicate of it at test 4's threshold: projects split whole, so such a shot sits
        # in the own half's pool, and copying it would read as a half-split contrast.
        shots = [k_key for h in (first, second) for k_key in (arms["own"][h], arms["sibling"][h])]
        flagged = near_duplicate_targets(cut, shots)
        clean = [
            {key: [row for row in rows if row["id"] not in flagged] for key, rows in r.items()}
            for r in cut
        ]
        try:
            without = reading(
                contrast([project_clusters(r, **read) for r in clean], seed, resamples)
            )
        except ValueError as error:
            # Too few examples left in some half to read: reported, not a crash.
            without = {"unreadable": str(error)}
        per_k[str(k)]["without_near_duplicate_shots"] = {
            "excluded": len(flagged),
            "threshold": LEAKAGE_THRESHOLD,
            "own_minus_sibling": without,
        }
        if adapters is None:
            ids = {row["id"] for rows in cut[0].values() for row in rows}
            # The adapter runs record the dtypes they decoded in as a list.
            decoding = {key: generator[key] for key in ("model_id", "max_new_tokens")}
            decoding["inference_dtype"] = [generator["inference_dtype"]]
            paths, trained, rank = adapter_runs(args.results, args.org, order, size, ids, decoding)
            adapters = {
                "runs": [str(p) for p in paths],
                "lora_rank": rank,
                "examples": len(ids),
                "own_minus_sibling": reading(
                    contrast(
                        [
                            project_clusters(r, org=args.org, seed=position)
                            for position, r in enumerate(trained, start=1)
                        ],
                        seed,
                        resamples,
                    )
                ),
            }
        cell = per_k[str(k)]["own_minus_sibling"]
        print(
            f"k={k} {args.org}: own-sibling {cell['estimate']:+.4f} "
            f"[{cell['low']:+.4f}, {cell['high']:+.4f}] -> {cell['reading']}; "
            f"sibling-foreign {per_k[str(k)]['sibling_minus_foreign']['estimate']:+.4f}; "
            f"own-none {per_k[str(k)]['own_minus_none']['estimate']:+.4f}"
        )
    assert adapters is not None
    cell = adapters["own_minus_sibling"]
    print(
        f"adapters {args.org}: own-sibling {cell['estimate']:+.4f} "
        f"[{cell['low']:+.4f}, {cell['high']:+.4f}] -> {cell['reading']}"
    )
    report = {
        "org": args.org,
        "foreign": args.foreign,
        "partitions": order,
        "halves_jobs": [str(p) for p in args.halves],
        "foreign_job": str(args.foreign_job),
        "generator": fixed["generator"],
        "bootstrap_seed": seed,
        "resamples": resamples,
        "ks": per_k,
        "adapters": adapters,
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
