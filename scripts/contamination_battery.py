"""Outcome-neutral test 1 on real data: is the post-cutoff corpus less familiar to the model
than a pre-cutoff control from the same projects?

Two measured methods beside the time partition. Min-K%++ reads the base checkpoint's token
likelihoods; guided completion asks the registered Instruct model to continue a hunk from its
first half. Each is computed on both windows and reported as a gap. No verdict: with no
published content cutoff, a flat gap is either no exposure or an instrument blind to it, and
the Stage 1 report commits to that reading in advance.
"""

import argparse
import gc
import json
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from sphragis.corpus.pipeline import run_dedup
from sphragis.experiment.model import (
    MAX_NEW_TOKENS,
    MEMBERSHIP_MODEL_ID,
    MODEL_ID,
    HFGenerator,
    token_statistics,
)
from sphragis.measure.contamination import (
    battery_report,
    min_k_percent,
    min_k_plus_plus,
    min_k_plus_plus_scores,
)
from sphragis.measure.score import extract_code

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--post", type=Path, required=True, help="post-cutoff examples (JSONL)")
parser.add_argument("--pre", type=Path, required=True, help="pre-cutoff control examples (JSONL)")
parser.add_argument("--corpus-starts", default="2024-10-01")
parser.add_argument("--model-published", default="2024-09-17")
parser.add_argument("--k", type=float, default=20.0)
parser.add_argument("--min-tokens", type=int, default=32)
parser.add_argument("--out", type=Path, default=Path("contamination.json"))
args = parser.parse_args()

# The guided instruction names the provenance, as guided completion does: the probe is whether
# the model can reproduce a specific file's content when told where it came from.
GUIDED = (
    "Below is the first part of a code hunk from the file {path} in the {project} repository.\n"
    "Continue it exactly as it appears in that repository. Reply with the continuation only.\n\n"
    "First part:\n{prefix}\n"
)


def load(path: Path) -> tuple[list[dict], dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    kept, removed = run_dedup(rows)
    return kept, {"examples": len(rows), "after_dedup": len(kept), "dedup_removed": removed}


post_rows, post_counts = load(args.post)
pre_rows, pre_counts = load(args.pre)

tok = AutoTokenizer.from_pretrained(MEMBERSHIP_MODEL_ID)


def eligible(rows: list[dict]) -> list[dict]:
    """Examples long enough for a membership score to mean anything.

    Hunks are short (median 16 tokens on OpenStack 2024-10) and Min-K%-family scores over a
    handful of tokens are dominated by noise; the method's own evaluation uses 32, 64 and 128.
    The same threshold applies to both windows, and how many qualify is reported.
    """
    return [
        r
        for r in rows
        if len(tok(str(r["after"]), add_special_tokens=False)["input_ids"]) >= args.min_tokens
    ]


post_e, pre_e = eligible(post_rows), eligible(pre_rows)
post_counts["eligible"], pre_counts["eligible"] = len(post_e), len(pre_e)
print(f"post {post_counts}\npre  {pre_counts}", flush=True)

# --- Min-K%++ on the base checkpoint ---------------------------------------------------
model = AutoModelForCausalLM.from_pretrained(
    MEMBERSHIP_MODEL_ID, dtype=torch.bfloat16, device_map="cuda:0"
)
model.eval()
post_tokens = [token_statistics(model, tok, str(r["after"])) for r in post_e]
pre_tokens = [token_statistics(model, tok, str(r["after"])) for r in pre_e]
del model
gc.collect()
torch.cuda.empty_cache()
print("membership statistics done", flush=True)


def per_example(rows: list[dict], tokens: list, window: str) -> list[dict]:
    return [
        {
            "id": r["id"],
            "window": window,
            "tokens": len(t),
            "undefined_positions": min_k_plus_plus_scores(t)[1],
            "min_k_plus_plus": min_k_plus_plus(t, k=args.k),
            "min_k_percent": min_k_percent([x[0] for x in t], k=args.k),
        }
        for r, t in zip(rows, tokens, strict=True)
    ]


scores = per_example(post_e, post_tokens, "post") + per_example(pre_e, pre_tokens, "pre")

# --- Guided completion on the registered model -------------------------------------------
generator = HFGenerator(model_id=MODEL_ID, max_new_tokens=MAX_NEW_TOKENS)


def completions(
    generator: HFGenerator, rows: list[dict], window: str, records: list[dict]
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for r in rows:
        lines = str(r["after"]).split("\n")
        if len(lines) < 2:
            continue
        cut = len(lines) // 2
        prefix, reference = "\n".join(lines[:cut]), "\n".join(lines[cut:])
        raw = generator.generate(
            GUIDED.format(path=r.get("path", ""), project=r.get("project", ""), prefix=prefix)
        )
        # Compared over the reference's own length: a model that continues past the hunk
        # has not failed to reproduce it.
        predicted = "\n".join(extract_code(raw).split("\n")[: len(lines) - cut])
        pairs.append((predicted, reference))
        records.append(
            {"id": r["id"], "window": window, "predicted": predicted, "reference": reference}
        )
    return pairs


guided_records: list[dict] = []
post_pairs = completions(generator, post_e, "post", guided_records)
pre_pairs = completions(generator, pre_e, "pre", guided_records)
del generator
gc.collect()
torch.cuda.empty_cache()

report = battery_report(
    post_tokens=post_tokens,
    pre_tokens=pre_tokens,
    post_completions=post_pairs,
    pre_completions=pre_pairs,
    corpus_starts=args.corpus_starts,
    model_published=args.model_published,
    k=args.k,
)
for method in ("min_k_plus_plus", "min_k_percent", "guided_completion"):
    r = report[method]
    print(
        f"{method:<18} post={r['post_cutoff']:+.4f} pre={r['pre_cutoff']:+.4f} gap={r['gap']:+.4f}",
        flush=True,
    )

args.out.write_text(
    json.dumps(
        {
            "membership_model": MEMBERSHIP_MODEL_ID,
            "guided_model": MODEL_ID,
            "k": args.k,
            "min_tokens": args.min_tokens,
            "post": {"path": str(args.post), **post_counts, "guided_pairs": len(post_pairs)},
            "pre": {"path": str(args.pre), **pre_counts, "guided_pairs": len(pre_pairs)},
            "report": report,
            "scores": scores,
            "guided": guided_records,
        },
        indent=2,
    )
)
print(f"wrote {args.out}")
print("BATTERY_OK")
