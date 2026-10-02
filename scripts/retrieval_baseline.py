"""Few-shot retrieval as a comparator to the adapters: the same contrasts, no training.

For each development-window example of an evaluated half, the base model is prompted four ways:
alone (the registered base arm), and with the `k` most similar training examples by BM25 from its
own half, from its sibling half, or from the other organization. Own minus sibling is the
retrieval counterpart of H1's half-split contrast, sibling minus foreign of the organization
contrast. If retrieval shows the same own-organization advantage the adapters do, the conventions
are recoverable from the data at inference time, which the adapters' result has to be read
against. Exploratory, outside the registered cells. Halves are the registered largest-first
assignment and windows the corpus's own; the test window is sealed and never read.

    uv run --no-sync python scripts/retrieval_baseline.py --root <corpus root> \\
        --orgs openstack wikimedia --out retrieval-baseline.json [--dry-run]

Run on the cluster: see scripts/retrieval_baseline.sbatch.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from statistics import fmean, median
from typing import Any

from sphragis.corpus.cli import WINDOWS
from sphragis.corpus.halves import assign, project_counts
from sphragis.corpus.load import refined_examples
from sphragis.corpus.pipeline import run_dedup, run_split
from sphragis.experiment.retrieval import BM25, few_shot_prompt
from sphragis.experiment.runner import build_prompt, require_unique_ids, to_clusters
from sphragis.measure.score import score
from sphragis.measure.stats import cluster_bootstrap
from sphragis.provenance import provenance_header

ARMS = ("none", "own", "sibling", "foreign")
# The contrasts read, treatment first: the retrieval counterparts of the half-split contrast
# and of the organization contrast, and how much retrieval adds over the base model at all.
CONTRASTS = (("own", "sibling"), ("sibling", "foreign"), ("own", "none"))
# The training budget every adapter was fitted under (model.TRAINING), in tokens. A pool holds
# only examples an adapter could have trained on: the same refusal `build_supervised` applies.
MAX_SEQ_LENGTH = 2048
# A dry run loads no tokenizer, so it stands in characters at four a token, and says so.
CHARS_PER_TOKEN = 4

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True, help="corpus root holding <org>/refined")
parser.add_argument("--orgs", nargs=2, default=["openstack", "wikimedia"])
parser.add_argument("--k", type=int, default=3)
parser.add_argument("--window", default="dev", choices=["pilot", "dev"])
parser.add_argument("--limit", type=int, help="first N targets per organization, for a smoke run")
parser.add_argument("--bootstrap-seed", type=int, default=7)
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


def corpus(root: Path, org: str) -> tuple[dict[str, list[dict]], dict[str, int]]:
    """An organization's windows as the study loads them, and its registered halves."""
    rows = refined_examples(root, org)
    if not rows:
        raise SystemExit(f"{org}: no refined examples under {root / org}")
    side = assign(dict(project_counts(rows, WINDOWS["train"])))
    kept, _ = run_dedup(rows)
    windows, _, _ = run_split(kept, WINDOWS)
    if windows.get("test"):
        raise SystemExit(f"{org}: the corpus holds test-window examples; it is sealed")
    return windows, side


def arm_prompts(
    targets: Sequence[Mapping[str, Any]],
    side: Mapping[str, int],
    pools: Mapping[str, BM25],
    k: int,
) -> dict[str, list[str]]:
    """Each target's prompt under each arm; `pools` holds the half pools and the foreign one."""
    prompts: dict[str, list[str]] = {arm: [] for arm in ARMS}
    for target in targets:
        half = side[target["project"]]
        sources = {"own": pools[f"half{half}"], "sibling": pools[f"half{1 - half}"]}
        sources["foreign"] = pools["foreign"]
        prompts["none"].append(few_shot_prompt(target, []))
        for arm, pool in sources.items():
            prompts[arm].append(few_shot_prompt(target, pool.top(target, k)))
    return prompts


def summarize(
    targets: Sequence[Mapping[str, Any]], scored: Mapping[str, list[dict]], seed: int
) -> dict[str, Any]:
    """Exact match per arm and each contrast's change-clustered 95% interval."""
    out: dict[str, Any] = {
        "targets": len(targets),
        "changes": len({t["change_id"] for t in targets}),
    }
    out["exact_match"] = {
        arm: fmean(r["exact_match"] for r in rows) for arm, rows in scored.items()
    }
    out["contrasts"] = {
        f"{a}-{b}": cluster_bootstrap(to_clusters(scored[a], scored[b]), seed=seed)
        for a, b in CONTRASTS
    }
    return out


def main() -> None:
    args = parser.parse_args()
    loaded = {org: corpus(args.root, org) for org in args.orgs}
    generator = None
    if args.dry_run:
        fits = trainable_by_chars
    else:
        from sphragis.experiment.model import HFGenerator

        generator = HFGenerator()
        fits = trainable_by_tokens(generator.tokenizer)
    plan: dict[str, tuple[list[dict], dict[str, list[str]]]] = {}
    pool_sizes: dict[str, dict[str, int]] = {}
    for org in args.orgs:
        windows, side = loaded[org]
        other = next(o for o in args.orgs if o != org)
        targets = [r for r in windows[args.window] if r["project"] in side][: args.limit]
        require_unique_ids(targets, label=f"{org} targets")
        train = [r for r in windows["train"] if fits(r)]
        pools = {
            f"half{h}": BM25([r for r in train if side.get(r["project"]) == h]) for h in (0, 1)
        }
        pools["foreign"] = BM25([r for r in loaded[other][0]["train"] if fits(r)])
        pool_sizes[org] = {name: len(index.pool) for name, index in pools.items()}
        plan[org] = (targets, arm_prompts(targets, side, pools, args.k))

    report: dict[str, Any] = {
        "k": args.k,
        "window": args.window,
        "arms": list(ARMS),
        "pool_filter": "chars/4 proxy"
        if args.dry_run
        else f"build_supervised, {MAX_SEQ_LENGTH} tokens",
        "pool_sizes": pool_sizes,
        "orgs": {},
    }
    if args.dry_run:
        for org, (targets, prompts) in plan.items():
            report["orgs"][org] = {
                "targets": len(targets),
                "prompt_chars_median": {a: median(len(p) for p in ps) for a, ps in prompts.items()},
                "prompt_chars_max": {a: max(len(p) for p in ps) for a, ps in prompts.items()},
            }
        report["provenance"] = provenance_header()
    else:
        from sphragis.experiment.model import run_provenance

        assert generator is not None
        for org, (targets, prompts) in plan.items():
            scored = {
                arm: [
                    {
                        "id": t["id"],
                        "change_id": t["change_id"],
                        **score(generator.generate(p), str(t["after"])),
                    }
                    for t, p in zip(targets, prompts[arm], strict=True)
                ]
                for arm in ARMS
            }
            report["orgs"][org] = summarize(targets, scored, args.bootstrap_seed)
            print(org, report["orgs"][org]["exact_match"], flush=True)
        report["provenance"] = run_provenance()
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
