"""Few-shot retrieval as the adapters' comparator: the generations, one job per pool set.

The base model is prompted with the k training examples nearest a held-out example by BM25,
solved, from a pool that stands where an adapter would: the rows that adapter trains on. No
weights are trained. `scripts/retrieval_read.py` reads the contrasts. Exploratory, outside the
registered cells; the test window is sealed and never read.

Every corpus is the one an adapter partition run read, found through that run's committed
result under `--results` (`retrieval.adapter_run`), so nothing is rebuilt.

`--pools halves`, once per admissible partition of an organization: each half read as the
partition run reads it (`window_split`) and cut as its adapter's training set
(`retrieval.pools`). Every held-out example of either half is prompted from each half's pool at
each k.

`--pools foreign`, once per organization: every example any of its first partitions scores,
prompted alone (the base arm) and from each half of the foreign organization's first admissible
partition, cut the same way. These arms do not depend on the organization's own partition, so
one job serves every run.

    uv run --no-sync python scripts/retrieval_comparator.py --pools halves \\
        --org openstack --partition 2 --results datasets/results \\
        --admissible datasets/results/admissible-partitions-openstack.json \\
        --out retrieval-halves-openstack-p2.json [--dry-run]

Run on the cluster: scripts/retrieval_comparator.sbatch.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from statistics import median
from typing import Any

from sphragis.experiment.decomposition import halves
from sphragis.experiment.grid import EvalRun, run_id
from sphragis.experiment.holdout import verbatim_overlap, window_split
from sphragis.experiment.neutral import LEAKAGE_MAX_RATE, LEAKAGE_THRESHOLD, leakage_check
from sphragis.experiment.retrieval import (
    EQUALIZE_SEED,
    KS,
    adapter_run,
    arm_prompts,
    few_shot_prompt,
    fingerprint,
    first_partitions,
    pools,
    resumable,
)
from sphragis.experiment.runner import build_prompt, require_unique_ids, scored_row
from sphragis.experiment.training import MAX_SEQ_LENGTH
from sphragis.provenance import provenance_header

# A dry run loads no tokenizer, so it stands in characters at four a token, and says so.
CHARS_PER_TOKEN = 4

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--pools", choices=["halves", "foreign"], required=True)
parser.add_argument("--results", type=Path, required=True, help="the adapters' partition runs")
parser.add_argument("--org", required=True)
parser.add_argument("--admissible", type=Path, required=True, help="the org's admissible list")
parser.add_argument("--partition", type=int, help="with --pools halves: the partition seed")
parser.add_argument("--foreign", help="with --pools foreign: the foreign organization")
parser.add_argument("--foreign-admissible", type=Path, help="with --pools foreign: its list")
parser.add_argument("--limit", type=int, help="first N targets per evaluated set, a smoke run")
parser.add_argument("--dry-run", action="store_true", help="build every prompt, load no model")
parser.add_argument("--out", type=Path, required=True)


def trainable_by_chars(row: Mapping[str, Any]) -> bool:
    """Whether an example plausibly fits the training budget, for a dry run without a tokenizer."""
    return len(build_prompt(row)) + len(str(row["after"])) <= MAX_SEQ_LENGTH * CHARS_PER_TOKEN


def trainable_by_tokens(tokenizer: Any) -> Callable[[Mapping[str, Any]], bool]:
    """Whether `build_supervised` would accept an example as a training item."""
    from sphragis.experiment.training import build_supervised

    def fits(row: Mapping[str, Any]) -> bool:
        try:
            build_supervised(tokenizer, row, prompt_builder=build_prompt, max_length=MAX_SEQ_LENGTH)
        except ValueError:
            return False
        return True

    return fits


def partition_root(root: Path, org: str, partition: int) -> dict[str, Any]:
    """The manifest of a partition corpus, refused unless it is `org`'s `partition`, unplanted."""
    path = root / "placebo.json"
    if not path.is_file():
        raise SystemExit(f"{root}: no placebo.json; build it with placebo_corpus.py")
    manifest = json.loads(path.read_text())
    found = {
        "source_org": manifest.get("source_org"),
        "partition_seed": manifest.get("partition_seed"),
        "deduplicated once": manifest.get("dedup_org") is not None,
        "planted": manifest.get("plant") is not None,
        "names": manifest.get("names"),
    }
    wanted = {
        "source_org": org,
        "partition_seed": partition,
        "deduplicated once": True,
        "planted": False,
        "names": list(halves(org)),
    }
    if found != wanted:
        raise SystemExit(f"{path}: {found}, not {wanted}")
    return manifest


def admissible(path: Path, org: str) -> tuple[list[int], int]:
    """The organization's first admissible partitions and its training size."""
    try:
        return first_partitions(json.loads(path.read_text()), org=org)
    except ValueError as error:
        raise SystemExit(f"{path}: {error}") from error


def corpus_of(
    results: Path, org: str, partition: int, order: list[int], size: int
) -> tuple[Path, dict[str, int]]:
    """The corpus the adapters' run on `partition` read, and how many rows each half trained on.

    Refused unless the corpus is that partition's and the run cut its training sets as the pools
    are cut: equalized, at `EQUALIZE_SEED`.
    """
    try:
        path, run, root = adapter_run(results, org=org, partition=partition, order=order, size=size)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if (run.get("equalize_train"), run.get("split_seed")) != (True, EQUALIZE_SEED):
        raise SystemExit(
            f"{path}: equalize_train {run.get('equalize_train')}, split_seed "
            f"{run.get('split_seed')}; the pools are cut equalized at {EQUALIZE_SEED}"
        )
    partition_root(root, org, partition)
    position = order.index(partition) + 1
    return root, {half: run["training"][f"{half}-s{position}"]["items"] for half in halves(org)}


def half_pools(
    root: Path, org: str, size: int, fits: Callable[[Mapping[str, Any]], bool]
) -> tuple[dict, dict[str, list[dict]], dict[str, dict]]:
    """A partition's two half pools, each half's held-out rows, and where each came from."""
    train, held_out, summary = {}, {}, {}
    for half in halves(org):
        try:
            train[half], held_out[half], summary[half] = window_split(root, half)
        except ValueError as error:
            raise SystemExit(str(error)) from error
    return pools(train, size=size, fits=fits), held_out, summary


def scored(results: Path, org: str, order: list[int], size: int) -> list[dict]:
    """Every held-out example any of the organization's first partitions scores, once each."""
    rows: dict[str, dict] = {}
    for partition in order:
        root, _ = corpus_of(results, org, partition, order, size)
        for half in halves(org):
            try:
                _, held_out, _ = window_split(root, half)
            except ValueError as error:
                raise SystemExit(str(error)) from error
            for row in held_out:
                rows.setdefault(row["id"], row)
    return sorted(rows.values(), key=lambda row: str(row["id"]))


def main() -> None:
    args = parser.parse_args()
    order, size = admissible(args.admissible, args.org)
    foreign_order: list[int] | None = None
    if args.pools == "halves":
        if args.partition is None:
            raise SystemExit("--pools halves needs --partition")
        root, trained = corpus_of(args.results, args.org, args.partition, order, size)
    else:
        if not (args.foreign and args.foreign_admissible):
            raise SystemExit("--pools foreign needs --foreign and --foreign-admissible")
        if args.foreign == args.org:
            raise SystemExit("the foreign organization must not be the one evaluated")
        foreign_order, foreign_size = admissible(args.foreign_admissible, args.foreign)
        # A larger pool holds closer neighbours, so the arms compare at one size or not at all.
        if foreign_size != size:
            raise SystemExit(f"{args.foreign} trains at {foreign_size}, {args.org} at {size}")
        root, trained = corpus_of(args.results, args.foreign, foreign_order[0], foreign_order, size)

    # Only the tokenizer before test 4: the model loads once the data has passed it.
    if args.dry_run:
        fits = trainable_by_chars
    else:
        from sphragis.experiment.model import MODEL_ID, _require_tokenizer

        fits = trainable_by_tokens(_require_tokenizer(MODEL_ID))

    # Each evaluated set's targets, and every prompt for them, keyed as the reader reads them.
    targets: dict[str, list[dict]] = {}
    prompts: dict[str, list[str]] = {}
    evaluated_by: dict[str, str] = {}
    if args.pools == "halves":
        indexes, held_out, corpora = half_pools(root, args.org, size, fits)
        for half in halves(args.org):
            targets[half] = held_out[half][: args.limit]
    else:
        indexes, _, corpora = half_pools(root, args.foreign, size, fits)
        targets[args.org] = scored(args.results, args.org, order, size)[: args.limit]
        base = run_id(EvalRun("base", args.org, None))
        prompts[base] = [few_shot_prompt(t, []) for t in targets[args.org]]
        evaluated_by[base] = args.org
    # Each pool is its adapter's training set: as many rows as the run trained that adapter on.
    # The proxy cannot say so, so a dry run records both.
    pooled = {pool: len(index.pool) for pool, index in indexes.items()}
    if not args.dry_run and pooled != trained:
        raise SystemExit(f"pools hold {pooled} rows, the adapters trained on {trained}")
    for evaluated, rows in targets.items():
        require_unique_ids(rows, label=f"{evaluated} targets")
        for key, texts in arm_prompts(rows, indexes, evaluated=evaluated).items():
            prompts[key], evaluated_by[key] = texts, evaluated

    # A pool row repeated verbatim in a target is the answer in the prompt; near-duplicates are
    # held to the registered test 4, as the adapters' training rows are.
    leakage = []
    for evaluated, rows in targets.items():
        for pool, index in indexes.items():
            repeated = verbatim_overlap(index.pool, rows)
            if repeated:
                raise SystemExit(f"{len(repeated)} {evaluated} targets repeat a {pool} pool row")
            check = leakage_check(
                index.pool,
                rows,
                threshold=LEAKAGE_THRESHOLD,
                max_rate=LEAKAGE_MAX_RATE,
                label=f"{pool}->{evaluated}",
            )
            leakage.append({"name": check.name, "passed": check.passed, **check.evidence})
    if not all(check["passed"] for check in leakage):
        raise SystemExit(f"outcome-neutral test 4 fails on a pool: {leakage}")

    report: dict[str, Any] = {
        "pools": args.pools,
        "org": args.org,
        "partition": args.partition,
        "foreign": args.foreign,
        "foreign_partition": foreign_order[0] if foreign_order else None,
        "train_size": size,
        "ks": list(KS),
        "limit": args.limit,
        "pool_filter": "chars/4 proxy" if args.dry_run else f"build_supervised, {MAX_SEQ_LENGTH}",
        "pool_sizes": pooled,
        "adapter_items": trained,
        # Which training rows each pool held, so a reading names the examples it retrieved from.
        "pool_ids": {pool: [row["id"] for row in index.pool] for pool, index in indexes.items()},
        "targets": {evaluated: len(rows) for evaluated, rows in targets.items()},
        "corpora": corpora,
        "leakage": leakage,
    }
    if args.dry_run:
        report["prompt_chars"] = {
            key: {"median": median(map(len, texts)), "max": max(map(len, texts))}
            for key, texts in prompts.items()
        }
        report["provenance"] = provenance_header()
        args.out.write_text(json.dumps(report, indent=2) + "\n")
        print(f"DRY RUN: {sum(map(len, prompts.values()))} prompts; wrote {args.out}")
        return

    from sphragis.experiment.model import HFGenerator, revision, run_provenance

    generator = HFGenerator()
    signature = (
        f"{generator.model_id}@{revision(generator.model_id)}|{generator.computed_dtype}|"
        f"max_new_tokens={generator.max_new_tokens}|temperature={generator.temperature}"
    )
    marks = {key: [fingerprint(signature, p) for p in texts] for key, texts in prompts.items()}
    expected = {
        key: list(zip((t["id"] for t in targets[evaluated_by[key]]), marks[key], strict=True))
        for key in prompts
    }
    # Each arm's rows are appended as it finishes. A rerun keeps only the arms it would generate
    # identically, rewritten in place first, so a wall clock or a kill loses at most the arm it
    # interrupted, and a changed prompt, pool or generator is regenerated.
    rows_path = args.out.with_suffix(".rows.jsonl")
    results: dict[str, list[dict]] = {}
    if rows_path.is_file():
        results, dropped = resumable(rows_path.read_text().splitlines(), expected)
        if dropped:
            print(f"{rows_path}: regenerating {dropped}", flush=True)
        # A kept generation is rescored against the current reference and metric, which the
        # fingerprint does not cover.
        for key, rows in results.items():
            results[key] = [
                {**scored_row(target, row["prediction"]), "fingerprint": row["fingerprint"]}
                for target, row in zip(targets[evaluated_by[key]], rows, strict=True)
            ]
        partial = rows_path.with_suffix(".partial")
        partial.write_text(
            "".join(
                json.dumps({"arm": key, **r}) + "\n" for key, rows in results.items() for r in rows
            )
        )
        os.replace(partial, rows_path)
    with rows_path.open("a") as rows_out:
        for key, texts in prompts.items():
            if key in results:
                continue
            results[key] = []
            for target, prompt, mark in zip(
                targets[evaluated_by[key]], texts, marks[key], strict=True
            ):
                prediction = generator.generate(prompt)
                results[key].append({**scored_row(target, prediction), "fingerprint": mark})
            rows_out.writelines(json.dumps({"arm": key, **r}) + "\n" for r in results[key])
            rows_out.flush()
            em = sum(r["exact_match"] for r in results[key]) / max(1, len(results[key]))
            print(f"{key:<44} EM={em:.3f}", flush=True)
    report["generator"] = signature
    report["inference_dtype"] = generator.computed_dtype
    report["results"] = results
    report["provenance"] = run_provenance()
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
