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
- own minus none, what the own half's pool adds over the base model.

With `--arms rules` the pools are the rules files `rules_distil.py` distilled from them, the
jobs `retrieval_comparator.py --arms rules` wrote, and the base arm comes from the retrieval
foreign job (`--base-job`); beside them, the written guides' contrasts: written own minus written
foreign, written own minus none, and distilled own minus written own. The adapters' own minus
sibling is read beside every family over the same partitions and examples.

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
    paired_clusters,
    project_clusters,
    within_sesoi,
)
from sphragis.experiment.grid import EvalRun, conditioned, run_id
from sphragis.experiment.neutral import LEAKAGE_THRESHOLD
from sphragis.experiment.partitions import on_common_examples
from sphragis.experiment.retrieval import KS, adapter_run, arm_key, condition, first_partitions
from sphragis.experiment.rules_arms import DISTILLED, WRITTEN
from sphragis.measure.stats import equal_halves, partitioned_crossed_draws, percentile_interval
from sphragis.provenance import provenance_header
from sphragis.refusal import refusals

CONFIDENCE = 0.95

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--org", required=True)
parser.add_argument("--foreign", required=True)
parser.add_argument("--admissible", type=Path, required=True)
parser.add_argument("--foreign-job", type=Path, required=True)
parser.add_argument("--halves", type=Path, nargs="+", required=True, help="admissible order")
parser.add_argument("--results", type=Path, required=True, help="the adapters' partition runs")
parser.add_argument("--arms", choices=["retrieval", "rules"], default="retrieval")
parser.add_argument("--base-job", type=Path, help="with --arms rules: the retrieval foreign job")
parser.add_argument("--bootstrap-seed", type=int, default=7)
parser.add_argument("--resamples", type=int, default=10_000)
parser.add_argument("--out", type=Path, required=True)

Rows = list[dict[str, Any]]


def load(path: Path, **expected: Any) -> dict[str, Any]:
    """A generation job's report, refused unless it is the job named and complete."""
    job = json.loads(path.read_text())
    # Jobs written before rules arms existed (d077d54) record no `arms`: they are retrieval jobs.
    found = {name: job.get(name, "retrieval" if name == "arms" else None) for name in expected}
    if found != expected:
        raise SystemExit(f"{path}: {found}, not {expected}")
    if "results" not in job:
        raise SystemExit(f"{path} is a dry run: no results")
    return job


def needed_once(arms: str, *, org: str, foreign: str) -> list[str]:
    """The arms a reading takes from the organization-wide jobs, each evaluated on `org`: base,
    and the foreign halves' arms of the family read; for rules, both written files' too, which
    every rules foreign job holds."""
    keys = [run_id(EvalRun("base", org, None))]
    if arms == "retrieval":
        keys += [arm_key(k, pool, org) for k in KS for pool in halves(foreign)]
    else:
        keys += [conditioned(DISTILLED, pool, org) for pool in halves(foreign)]
        keys += [conditioned(WRITTEN, owner, org) for owner in (org, foreign)]
    return keys


def run_results(
    own: Mapping[str, Rows], once: Mapping[str, Rows], *, org: str, keys: Sequence[str]
) -> dict[str, Rows]:
    """One partition's arms, with the arms computed once per organization (`keys`, each ending
    `|<org>`) cut to each of its halves' examples and keyed to the half."""
    out = dict(own)
    for half in halves(org):
        ids = {row["id"] for key, rows in own.items() if key.endswith(f"|{half}") for row in rows}
        for source in keys:
            if not source.endswith(f"|{org}"):
                raise SystemExit(f"{source} is not evaluated on {org}'s examples")
            rows = [row for row in once[source] if row["id"] in ids]
            missing = ids - {row["id"] for row in rows}
            if missing:
                raise SystemExit(f"{source} lacks {len(missing)} of {half}'s examples")
            out[source.removesuffix(f"|{org}") + f"|{half}"] = rows
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
        with refusals():
            path, run, _, _ = adapter_run(
                results, org=org, partition=partition, order=order, size=size
            )
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


#: What every rules job records of the distillation and the arm prompts it read, held equal.
RULES_RECORD = ("rules_suffix", "rules_pipeline", "rules_arms")


def families(arms: str) -> dict[str, str]:
    """The arm families a reading compares, by name, each its condition: the retrieval ones by k,
    as the committed readings key them under `ks`."""
    if arms == "retrieval":
        return {str(k): condition(k) for k in KS}
    return {"distilled": DISTILLED}


def label(name: str) -> str:
    """A family as its reading line names it: `k=1` for a retrieval one."""
    return f"k={name}" if name.isdigit() else name


def read_family(
    cut: Sequence[Mapping[str, Rows]],
    *,
    org: str,
    foreign: str,
    name: str,
    condition_name: str,
    seed: int,
    resamples: int,
    shots: bool,
) -> dict[str, Any]:
    """One family's contrasts over the runs, the rule applied to own minus sibling; with `shots`,
    the reading again without targets whose shot is a near-duplicate of them."""
    first, second = halves(org)
    read = {"org": org, "seed": None, "condition": condition_name}

    def key(owner: str, evaluated: str) -> str:
        return conditioned(condition_name, owner, evaluated)

    arms = {
        "none": {h: run_id(EvalRun("base", h, None)) for h in (first, second)},
        "own": {h: key(h, h) for h in (first, second)},
        "sibling": {first: key(second, first), second: key(first, second)},
    }
    out: dict[str, Any] = {
        # Each run's arm pooled over its two halves' examples, then averaged over runs.
        "exact_match": {
            arm: fmean(
                fmean(row["exact_match"] for k in keys.values() for row in r[k]) for r in cut
            )
            for arm, keys in arms.items()
        },
        "own_minus_sibling": reading(
            contrast([project_clusters(r, **read) for r in cut], seed, resamples)
        ),
        "sibling_minus_foreign": contrast(
            [organization_clusters(r, foreign=foreign, **read) for r in cut], seed, resamples
        ),
        "own_minus_none": contrast([base_clusters(r, **read) for r in cut], seed, resamples),
    }
    # Beside it, the same reading without any target whose own or sibling shot is a
    # near-duplicate of it at test 4's threshold: projects split whole, so such a shot sits in
    # the own half's pool, and copying it would read as a half-split contrast.
    if not shots:
        # A rules arm has no shots to be near-duplicates: not applicable, not zero.
        out["without_near_duplicate_shots"] = None
        return report_family(out, name=name, org=org)
    retrieved = [k for h in (first, second) for k in (arms["own"][h], arms["sibling"][h])]
    flagged = near_duplicate_targets(cut, retrieved)
    clean = [
        {k: [row for row in rows if row["id"] not in flagged] for k, rows in r.items()} for r in cut
    ]
    try:
        without = reading(contrast([project_clusters(r, **read) for r in clean], seed, resamples))
    except ValueError as error:
        # Too few examples left in some half to read: reported, not a crash.
        without = {"unreadable": str(error)}
    out["without_near_duplicate_shots"] = {
        "excluded": len(flagged),
        "threshold": LEAKAGE_THRESHOLD,
        "own_minus_sibling": without,
    }
    return report_family(out, name=name, org=org)


def report_family(out: dict[str, Any], *, name: str, org: str) -> dict[str, Any]:
    """Print one family's reading line and return it."""
    cell = out["own_minus_sibling"]
    print(
        f"{label(name)} {org}: own-sibling {cell['estimate']:+.4f} "
        f"[{cell['low']:+.4f}, {cell['high']:+.4f}] -> {cell['reading']}; "
        f"sibling-foreign {out['sibling_minus_foreign']['estimate']:+.4f}; "
        f"own-none {out['own_minus_none']['estimate']:+.4f}"
    )
    return out


def read_written(
    cut: Sequence[Mapping[str, Rows]], *, org: str, foreign: str, seed: int, resamples: int
) -> dict[str, Any]:
    """The written guides' contrasts: own against foreign, against none, and against the own
    half's distilled file, the last comparing the two sources of a rules file."""
    pairs = {
        "written_own_minus_written_foreign": (
            lambda h: conditioned(WRITTEN, org, h),
            lambda h: conditioned(WRITTEN, foreign, h),
        ),
        "written_own_minus_none": (
            lambda h: conditioned(WRITTEN, org, h),
            lambda h: run_id(EvalRun("base", h, None)),
        ),
        "distilled_own_minus_written_own": (
            lambda h: conditioned(DISTILLED, h, h),
            lambda h: conditioned(WRITTEN, org, h),
        ),
    }
    return {
        name: contrast(
            [paired_clusters(r, org=org, treatment=t, control=c) for r in cut], seed, resamples
        )
        for name, (t, c) in pairs.items()
    }


def main() -> None:
    args = parser.parse_args()
    with refusals(f"{args.admissible}: "):
        order, size = first_partitions(json.loads(args.admissible.read_text()), org=args.org)
    if len(args.halves) != len(order):
        raise SystemExit(f"{len(args.halves)} --halves files, not the fixed {len(order)}")
    if (args.arms == "rules") != (args.base_job is not None):
        raise SystemExit(
            "--base-job is for --arms rules, which needs it: the retrieval foreign job's base arm"
        )
    fixed = {"org": args.org, "train_size": size, "ks": list(KS), "limit": None}
    if args.arms == "rules":
        # Every rules job read one distillation: one suffix, one pipeline.
        head = json.loads(args.foreign_job.read_text())
        fixed |= {key: head.get(key) for key in RULES_RECORD}
        if fixed["rules_pipeline"] is None:
            raise SystemExit(f"{args.foreign_job} does not record the rules files it read")
    foreign_job = load(
        args.foreign_job, pools="foreign", foreign=args.foreign, arms=args.arms, **fixed
    )
    # Every arm is compared with arms from other jobs, so all of them ran one generator.
    generator = {
        key: foreign_job.get(key)
        for key in ("generator", "inference_dtype", "model_id", "max_new_tokens")
    }
    if None in generator.values():
        raise SystemExit(f"{args.foreign_job} does not record its generator")
    fixed |= generator
    once = dict(foreign_job["results"])
    if args.base_job:
        base_fixed = {k: v for k, v in fixed.items() if k not in RULES_RECORD}
        base = load(
            args.base_job, pools="foreign", foreign=args.foreign, arms="retrieval", **base_fixed
        )
        once[run_id(EvalRun("base", args.org, None))] = base["results"][
            run_id(EvalRun("base", args.org, None))
        ]
    needed = needed_once(args.arms, org=args.org, foreign=args.foreign)
    lacking = [key for key in needed if key not in once]
    if lacking:
        raise SystemExit(f"{args.foreign_job} (with any --base-job) lacks the arms {lacking}")
    runs = [
        run_results(
            load(path, pools="halves", partition=partition, arms=args.arms, **fixed)["results"],
            once,
            org=args.org,
            keys=sorted(once),
        )
        for path, partition in zip(args.halves, order, strict=True)
    ]
    seed, resamples = args.bootstrap_seed, args.resamples
    per_family: dict[str, Any] = {}
    adapters = None
    for name, condition_name in families(args.arms).items():
        with refusals():
            cut, examples = on_common_examples(runs, condition=condition_name)
        per_family[name] = {
            **examples,
            **read_family(
                cut,
                org=args.org,
                foreign=args.foreign,
                name=name,
                condition_name=condition_name,
                seed=seed,
                resamples=resamples,
                shots=args.arms == "retrieval",
            ),
        }
        if args.arms == "rules":
            per_family[name] |= read_written(
                cut, org=args.org, foreign=args.foreign, seed=seed, resamples=resamples
            )
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
    assert adapters is not None
    cell = adapters["own_minus_sibling"]
    print(
        f"adapters {args.org}: own-sibling {cell['estimate']:+.4f} "
        f"[{cell['low']:+.4f}, {cell['high']:+.4f}] -> {cell['reading']}"
    )
    report = {
        "org": args.org,
        "foreign": args.foreign,
        **({"arms": args.arms} if args.arms != "retrieval" else {}),
        "partitions": order,
        "halves_jobs": [str(p) for p in args.halves],
        "foreign_job": str(args.foreign_job),
        **({"base_job": str(args.base_job)} if args.base_job else {}),
        "generator": fixed["generator"],
        "bootstrap_seed": seed,
        "resamples": resamples,
        # The retrieval readings keep the layout the committed ones were written in.
        ("ks" if args.arms == "retrieval" else "families"): per_family,
        "adapters": adapters,
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
