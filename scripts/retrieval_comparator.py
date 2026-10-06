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

`--arms rules`: the same jobs, each pool replaced by the rules file `rules_distil.py` distilled
from it (from `--rules`, under `--rules-suffix`, checked by pool ids, pipeline and generator), and
the foreign job's base arm replaced by both organizations' written guides' files (checked against
the snapshot in `--guides`). The base arm comes from the retrieval foreign job.

Run on the cluster: scripts/retrieval_comparator.sbatch (ARMS=rules for the rules arms).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from statistics import median
from typing import Any

from sphragis.experiment.decomposition import halves
from sphragis.experiment.grid import EvalRun, run_id
from sphragis.experiment.holdout import verbatim_overlap
from sphragis.experiment.neutral import LEAKAGE_MAX_RATE, LEAKAGE_THRESHOLD, leakage_check
from sphragis.experiment.retrieval import (
    KS,
    Prompt,
    arm_prompts,
    corpus_of,
    few_shot_prompt,
    fingerprint,
    first_partitions,
    pools,
    prompt_text,
    read_halves,
    resumable,
    trainable,
)
from sphragis.experiment.rules import (
    DISTILLED,
    WRITTEN,
    default_system_holds,
    pinned,
    pipeline,
    rules_arms,
)
from sphragis.experiment.runner import build_prompt, require_unique_ids, scored_row
from sphragis.experiment.training import MAX_SEQ_LENGTH
from sphragis.provenance import provenance_header

# A dry run loads no tokenizer, so it stands in characters at four a token, and says so.
CHARS_PER_TOKEN = 4

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--pools", choices=["halves", "foreign"], required=True)
parser.add_argument(
    "--arms",
    choices=["retrieval", "rules"],
    default="retrieval",
    help="BM25 shots from each pool, or the rules file distilled from it (rules_distil.py)",
)
parser.add_argument("--rules", type=Path, help="with --arms rules: the rules_distil.py outputs")
parser.add_argument(
    "--rules-suffix",
    default="",
    help="with --arms rules: the result suffix they were written under",
)
parser.add_argument(
    "--guides", type=Path, default=Path("datasets/rules"), help="the written guides' snapshot"
)
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


def admissible(path: Path, org: str) -> tuple[list[int], int]:
    """The organization's first admissible partitions and its training size."""
    try:
        return first_partitions(json.loads(path.read_text()), org=org)
    except ValueError as error:
        raise SystemExit(f"{path}: {error}") from error


def half_pools(
    root: Path,
    org: str,
    size: int,
    fits: Callable[[Mapping[str, Any]], bool],
    recorded: Mapping[str, Mapping[str, Any]],
) -> tuple[dict, dict[str, list[dict]], dict[str, dict]]:
    """A partition's two half pools, each half's held-out rows, and where each came from."""
    train, held_out, summary = read_halves(root, org, recorded)
    return pools(train, size=size, fits=fits), held_out, summary


def scored(results: Path, org: str, order: list[int], size: int) -> list[dict]:
    """Every held-out example any of the organization's first partitions scores, once each."""
    rows: dict[str, dict] = {}
    for partition in order:
        root, _, recorded = corpus_of(results, org, partition, order, size)
        for held_out in read_halves(root, org, recorded)[1].values():
            for row in held_out:
                first = rows.setdefault(row["id"], row)
                # The same example in every partition, or the arms are scored on two references.
                if any(
                    first.get(f) != row.get(f) for f in ("change_id", "comments", "before", "after")
                ):
                    raise SystemExit(f"example {row['id']} differs between partition corpora")
    return sorted(rows.values(), key=lambda row: str(row["id"]))


def rules_files(
    args: argparse.Namespace, indexes: Mapping[str, Any], foreign_order: list[int] | None
) -> tuple[dict[tuple[str, str], str], set[str]]:
    """The rules files a rules job prompts with, and the generators that made them. Each
    distilled file is checked to come from the pool this job rebuilt (by its rows' ids), so a file
    stands where its adapter would.

    A halves job's files are its partition's halves'; a foreign job's are the foreign
    organization's first partition's halves and both organizations' written guides.
    """
    if args.rules is None:
        raise SystemExit("--arms rules needs --rules")

    def load(name: str) -> dict[str, Any]:
        path = args.rules / f"{name}{args.rules_suffix}.json"
        if not path.is_file():
            raise SystemExit(f"no rules file {path}: run rules_distil.py first")
        # A claim a running distillation holds is an empty file.
        if path.stat().st_size == 0:
            raise SystemExit(f"{path} is still being written")
        return json.loads(path.read_text())

    owner, partition = (
        (args.org, args.partition) if args.pools == "halves" else (args.foreign, foreign_order[0])
    )
    distilled = load(f"rules-reviews-{owner}-p{partition}")
    files: dict[tuple[str, str], str] = {}
    made_by = {distilled["generator"]}
    # Made under the prompts and budgets the research log describes, not an earlier set.
    if distilled.get("pipeline") != pipeline():
        raise SystemExit(f"{owner} p{partition}'s rules files were distilled under other prompts")
    for half, index in indexes.items():
        if half not in distilled.get("files", {}):
            raise SystemExit(f"{owner} p{partition}'s rules files hold no file for {half}")
        made = distilled["files"][half]
        if not args.dry_run and made["pool_ids"] != [row["id"] for row in index.pool]:
            raise SystemExit(f"{half}'s rules file was distilled from another pool")
        files[(DISTILLED, half)] = made["file"]
    if args.pools == "foreign":
        for org in (args.org, args.foreign):
            written = load(f"rules-guide-{org}")
            # Distilled from the snapshot committed beside this code, page for page.
            snapshot = json.loads((args.guides / f"guide-{org}.json").read_text())
            if written.get("pipeline") != pipeline():
                raise SystemExit(f"{org}'s written rules file was distilled under other prompts")
            if written.get("guide") != pinned(snapshot):
                raise SystemExit(f"{org}'s written rules file was distilled from another snapshot")
            files[(WRITTEN, org)] = written["files"][org]["file"]
            made_by.add(written["generator"])
    return files, made_by


def main() -> None:
    """The job, with every library refusal (a `ValueError`) its exit, message unchanged."""
    try:
        run(parser.parse_args())
    except ValueError as error:
        raise SystemExit(str(error)) from error


def run(args: argparse.Namespace) -> None:
    if args.limit is not None and args.limit < 1:
        raise SystemExit(f"--limit must be at least 1, got {args.limit}")
    order, size = admissible(args.admissible, args.org)
    foreign_order: list[int] | None = None
    if args.pools == "halves":
        if args.partition is None:
            raise SystemExit("--pools halves needs --partition")
        root, trained, recorded = corpus_of(args.results, args.org, args.partition, order, size)
    else:
        if not (args.foreign and args.foreign_admissible):
            raise SystemExit("--pools foreign needs --foreign and --foreign-admissible")
        if args.foreign == args.org:
            raise SystemExit("the foreign organization must not be the one evaluated")
        foreign_order, foreign_size = admissible(args.foreign_admissible, args.foreign)
        # A larger pool holds closer neighbours, so the arms compare at one size or not at all.
        if foreign_size != size:
            raise SystemExit(f"{args.foreign} trains at {foreign_size}, {args.org} at {size}")
        root, trained, recorded = corpus_of(
            args.results, args.foreign, foreign_order[0], foreign_order, size
        )

    # Only the tokenizer before test 4: the model loads once the data has passed it.
    if args.dry_run:
        fits = trainable_by_chars
    else:
        from sphragis.experiment.model import MODEL_ID, _require_tokenizer

        tokenizer = _require_tokenizer(MODEL_ID)
        fits = trainable(tokenizer)
        if args.arms == "rules" and not default_system_holds(tokenizer):
            raise SystemExit("the chat template's default system turn is not rules.DEFAULT_SYSTEM")

    # Each evaluated set's targets, and every prompt for them, keyed as the reader reads them.
    targets: dict[str, list[dict]] = {}
    prompts: dict[str, list[Prompt]] = {}
    similarity: dict[str, list[float | None]] = {}
    evaluated_by: dict[str, str] = {}
    if args.pools == "halves":
        indexes, held_out, corpora = half_pools(root, args.org, size, fits, recorded)
        for half in halves(args.org):
            targets[half] = held_out[half][: args.limit]
    else:
        indexes, _, corpora = half_pools(root, args.foreign, size, fits, recorded)
        targets[args.org] = scored(args.results, args.org, order, size)[: args.limit]
        # The base arm is the retrieval foreign job's; a rules job reads it from there.
        if args.arms == "retrieval":
            base = run_id(EvalRun("base", args.org, None))
            prompts[base] = [few_shot_prompt(t, []) for t in targets[args.org]]
            similarity[base] = [0.0 for _ in targets[args.org]]
            evaluated_by[base] = args.org
    # Each pool is its adapter's training set: as many rows as the run trained that adapter on.
    # The proxy cannot say so, so a dry run records both.
    pooled = {pool: len(index.pool) for pool, index in indexes.items()}
    if not args.dry_run and pooled != trained:
        raise SystemExit(f"pools hold {pooled} rows, the adapters trained on {trained}")
    files: dict[tuple[str, str], str] = {}
    made_by: set[str] = set()
    if args.arms == "rules":
        files, made_by = rules_files(args, indexes, foreign_order)
    for evaluated, rows in targets.items():
        if not rows:
            raise SystemExit(f"{evaluated} has no held-out examples to prompt")
        require_unique_ids(rows, label=f"{evaluated} targets")
        if args.arms == "retrieval":
            arms, closest = arm_prompts(rows, indexes, evaluated=evaluated)
        else:
            arms = rules_arms(rows, files, evaluated=evaluated)
            # No shots, so no shot to be a near-duplicate: not measured, not zero.
            closest = {key: [None for _ in rows] for key in arms}
        for key, texts in arms.items():
            prompts[key], similarity[key], evaluated_by[key] = texts, closest[key], evaluated

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
        "arms": args.arms,
        # Which distillation the rules arms read, so a reading combines one set of them.
        "rules_suffix": args.rules_suffix if args.arms == "rules" else None,
        "rules_pipeline": pipeline() if args.arms == "rules" else None,
        "rules_files": {
            f"{condition}:{owner}": hashlib.sha256(text.encode()).hexdigest()
            for (condition, owner), text in files.items()
        },
        "org": args.org,
        "partition": args.partition,
        "foreign": args.foreign,
        "foreign_partition": foreign_order[0] if foreign_order else None,
        "train_size": size,
        "ks": list(KS),
        "limit": args.limit,
        "pool_filter": "chars/4 proxy" if args.dry_run else f"build_supervised, {MAX_SEQ_LENGTH}",
        "pool_sizes": pooled,
        # Prompts whose closest shot is a near-duplicate of the target at test 4's threshold:
        # the reader reads own minus sibling without them beside the full reading.
        "near_duplicate_shots": {
            key: None if None in values else sum(v >= LEAKAGE_THRESHOLD for v in values)
            for key, values in similarity.items()
        },
        "adapter_items": trained,
        # Which training rows each pool held, so a reading names the examples it retrieved from.
        "pool_ids": {pool: [row["id"] for row in index.pool] for pool, index in indexes.items()},
        "targets": {evaluated: len(rows) for evaluated, rows in targets.items()},
        "corpora": corpora,
        "leakage": leakage,
    }
    if args.dry_run:
        report["prompt_chars"] = {
            key: {
                "median": median(len(prompt_text(t)) for t in texts),
                "max": max(len(prompt_text(t)) for t in texts),
            }
            for key, texts in prompts.items()
        }
        report["provenance"] = provenance_header()
        args.out.write_text(json.dumps(report, indent=2) + "\n")
        print(f"DRY RUN: {sum(map(len, prompts.values()))} prompts; wrote {args.out}")
        return

    from sphragis.experiment.model import (
        INFERENCE_DTYPE,
        MODEL_ID,
        HFGenerator,
        revision,
        run_provenance,
    )

    # A rules file is part of its arm, so the model that scores the arm distilled it: checked on
    # the registered model and dtype before the model loads.
    for made in made_by:
        if made != f"{MODEL_ID}@{revision(MODEL_ID)}|{INFERENCE_DTYPE}":
            raise SystemExit(f"a rules file was distilled by {made}, not the registered model")
    generator = HFGenerator()
    signature = (
        f"{generator.model_id}@{revision(generator.model_id)}|{generator.computed_dtype}|"
        f"max_new_tokens={generator.max_new_tokens}|temperature={generator.temperature}"
    )
    if made_by and generator.computed_dtype != INFERENCE_DTYPE:
        raise SystemExit(f"the arms run in {generator.computed_dtype}, not {INFERENCE_DTYPE}")
    marks = {key: [fingerprint(signature, p) for p in texts] for key, texts in prompts.items()}
    expected = {
        key: list(zip((t["id"] for t in targets[evaluated_by[key]]), marks[key], strict=True))
        for key in prompts
    }
    # A rerun keeps only the rows it would generate identically, rewritten in place first: an arm
    # a kill cut short resumes from its kept prefix, and a changed prompt, pool or generator is
    # regenerated.
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
                {
                    **scored_row(target, row["prediction"]),
                    "fingerprint": row["fingerprint"],
                    "shot_jaccard": closest,
                }
                # A kept arm may be a prefix of its targets: the rows a kill left.
                for target, row, closest in zip(
                    targets[evaluated_by[key]], rows, similarity[key], strict=False
                )
            ]
        partial = rows_path.with_suffix(".partial")
        partial.write_text(
            "".join(
                json.dumps({"arm": key, **r}) + "\n" for key, rows in results.items() for r in rows
            )
        )
        os.replace(partial, rows_path)
    # Each row is written as it is generated, so a kill loses at most the one in progress.
    with rows_path.open("a") as rows_out:
        for key, texts in prompts.items():
            done = results.setdefault(key, [])
            todo = zip(targets[evaluated_by[key]], texts, marks[key], similarity[key], strict=True)
            for position, (target, prompt, mark, closest) in enumerate(todo):
                if position < len(done):
                    continue
                row = scored_row(target, generator.generate(prompt))
                done.append({**row, "fingerprint": mark, "shot_jaccard": closest})
                rows_out.write(json.dumps({"arm": key, **done[-1]}) + "\n")
                rows_out.flush()
            em = sum(r["exact_match"] for r in done) / max(1, len(done))
            print(f"{key:<44} EM={em:.3f}", flush=True)
    report["generator"] = signature
    report["model_id"], report["max_new_tokens"] = generator.model_id, generator.max_new_tokens
    report["inference_dtype"] = generator.computed_dtype
    report["results"] = results
    report["provenance"] = run_provenance()
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
