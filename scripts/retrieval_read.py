"""Read the retrieval comparator: its contrasts over partitions, beside the adapters' H1.

Takes an organization's `retrieval_comparator.py --pools foreign` job and one `--pools halves`
job per partition, in admissible order (the first `retrieval.PARTITIONS`). Each partition is a
run as an adapter partition run is, its two pools in place of its two adapters, and every
contrast is read with the registered crossed runs-by-changes estimator
(`partitioned_crossed_draws`) on the examples every run scored:

- own minus sibling, the counterpart of H1's half-split contrast;
- sibling minus foreign, of the organization contrast (the foreign organization's two half
  pools averaged per example, as its two adapters are);
- own minus none, what retrieval from the own half adds over the base model.

Reading rule, fixed before any generation (research log, 2026-10-05): own minus sibling at each
k, on its 95% interval, reads "carries a half-split contrast" when the lower bound clears the
SESOI, "carries none as large as the SESOI" when the interval sits inside the SESOI band, and
"inconclusive" otherwise; the adapters' development-window H1 (`--adapters`) is reported
beside it. Exploratory: no reading here binds a verdict.

    uv run --no-sync python scripts/retrieval_read.py --org openstack --foreign wikimedia \\
        --admissible datasets/results/admissible-partitions-openstack.json \\
        --foreign-job retrieval-foreign-openstack.json \\
        --adapters datasets/results/partition-pilot-openstack.json \\
        retrieval-halves-openstack-p2.json ... --out retrieval-openstack.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import fmean
from typing import Any

from sphragis.experiment.decomposition import (
    halves,
    meaningful,
    organization_clusters,
    project_clusters,
    within_sesoi,
)
from sphragis.experiment.grid import EvalRun, run_id
from sphragis.experiment.partitions import MAX_DROPPED_SHARE, eval_ids
from sphragis.experiment.retrieval import KS, PARTITIONS, arm_key, condition
from sphragis.experiment.runner import to_clusters
from sphragis.measure.stats import equal_halves, partitioned_crossed_draws, percentile_interval
from sphragis.provenance import provenance_header

CONFIDENCE = 0.95

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("halves_jobs", type=Path, nargs="+", help="--pools halves, admissible order")
parser.add_argument("--org", required=True)
parser.add_argument("--foreign", required=True)
parser.add_argument("--admissible", type=Path, required=True)
parser.add_argument("--foreign-job", type=Path, required=True)
parser.add_argument("--adapters", type=Path, help="the org's partition pilot, read beside")
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


def common(runs: Sequence[Mapping[str, Rows]], k: int) -> tuple[list[dict[str, Rows]], dict]:
    """Every run cut to the examples every run scored, held to the partition design's ceiling."""
    per_run = [eval_ids(results, condition(k)) for results in runs]
    union, shared = set().union(*per_run), set.intersection(*per_run)
    dropped = len(union) - len(shared)
    if union and dropped / len(union) > MAX_DROPPED_SHARE:
        raise SystemExit(f"{dropped} of {len(union)} examples missing from some run")
    cut = [
        {key: [row for row in rows if row["id"] in shared] for key, rows in results.items()}
        for results in runs
    ]
    return cut, {"examples": len(shared), "dropped": dropped}


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


def reading(low: float, high: float) -> str:
    """The rule fixed before generation, on own minus sibling's interval."""
    if meaningful(low):
        return "carries a half-split contrast"
    if within_sesoi(low, high):
        return "carries none as large as the SESOI"
    return "inconclusive"


def main() -> None:
    args = parser.parse_args()
    listing = json.loads(args.admissible.read_text())
    order, size = listing["admissible"][:PARTITIONS], listing["size_floor"]
    if len(args.halves_jobs) != PARTITIONS:
        raise SystemExit(f"{len(args.halves_jobs)} partition jobs, not the fixed {PARTITIONS}")
    fixed = {"org": args.org, "train_size": size, "ks": list(KS), "limit": None}
    foreign = load(args.foreign_job, pools="foreign", foreign=args.foreign, **fixed)["results"]
    runs = [
        run_results(
            load(path, pools="halves", partition=partition, **fixed)["results"],
            foreign,
            org=args.org,
            foreign_org=args.foreign,
        )
        for path, partition in zip(args.halves_jobs, order, strict=True)
    ]
    seed, resamples = args.bootstrap_seed, args.resamples
    per_k: dict[str, Any] = {}
    for k in KS:
        cut, examples = common(runs, k)
        own_sibling = contrast(
            [project_clusters(r, org=args.org, seed=None, condition=condition(k)) for r in cut],
            seed,
            resamples,
        )
        sibling_foreign = contrast(
            [
                organization_clusters(
                    r, org=args.org, foreign=args.foreign, seed=None, condition=condition(k)
                )
                for r in cut
            ],
            seed,
            resamples,
        )
        own_none = contrast(
            [
                [
                    to_clusters(r[arm_key(k, half, half)], r[run_id(EvalRun("base", half, None))])
                    for half in halves(args.org)
                ]
                for r in cut
            ],
            seed,
            resamples,
        )
        first, second = halves(args.org)
        arms = {
            "none": {h: run_id(EvalRun("base", h, None)) for h in (first, second)},
            "own": {h: arm_key(k, h, h) for h in (first, second)},
            "sibling": {first: arm_key(k, second, first), second: arm_key(k, first, second)},
        }
        # Each run's arm pooled over its two halves' examples, then averaged over runs.
        exact_match = {
            arm: fmean(
                fmean(row["exact_match"] for half, key in keys.items() for row in r[key])
                for r in cut
            )
            for arm, keys in arms.items()
        }
        per_k[str(k)] = {
            **examples,
            "exact_match": exact_match,
            "own_minus_sibling": own_sibling,
            "sibling_minus_foreign": sibling_foreign,
            "own_minus_none": own_none,
            "reading": reading(own_sibling["low"], own_sibling["high"]),
        }
        print(
            f"k={k} {args.org}: own-sibling {own_sibling['estimate']:+.4f} "
            f"[{own_sibling['low']:+.4f}, {own_sibling['high']:+.4f}] -> "
            f"{per_k[str(k)]['reading']}; sibling-foreign {sibling_foreign['estimate']:+.4f}; "
            f"own-none {own_none['estimate']:+.4f}"
        )
    adapters = None
    if args.adapters:
        pilot = json.loads(args.adapters.read_text())
        if pilot.get("org", args.org) != args.org or pilot.get("window") != "development":
            raise SystemExit(f"{args.adapters} is not {args.org}'s development pilot")
        interval = pilot["intervals"][str(CONFIDENCE)]
        adapters = {"file": str(args.adapters), "estimate": pilot["estimate"], **interval}
    report = {
        "org": args.org,
        "foreign": args.foreign,
        "partitions": order,
        "halves_jobs": [str(p) for p in args.halves_jobs],
        "foreign_job": str(args.foreign_job),
        "bootstrap_seed": seed,
        "resamples": resamples,
        "ks": per_k,
        "adapters_h1": adapters,
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
