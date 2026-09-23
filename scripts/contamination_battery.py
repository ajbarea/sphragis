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

from sphragis.corpus.load import derived_file_rows
from sphragis.corpus.pipeline import run_dedup
from sphragis.experiment.model import (
    MAX_NEW_TOKENS,
    MEMBERSHIP_MODEL_ID,
    MODEL_ID,
    HFGenerator,
    run_provenance,
    token_statistics,
)
from sphragis.measure.contamination import (
    battery_report,
    gap_k_percent,
    min_k_percent,
    min_k_plus_plus,
    min_k_plus_plus_scores,
)
from sphragis.measure.score import extract_code, score

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--post", type=Path, required=True, help="post-cutoff examples (JSONL)")
parser.add_argument("--pre", type=Path, required=True, help="pre-cutoff control examples (JSONL)")
parser.add_argument("--corpus-starts", default="2024-10-01")
parser.add_argument("--model-published", default="2024-09-17")
parser.add_argument("--k", type=float, default=20.0)
parser.add_argument(
    "--gap-window",
    type=int,
    default=3,
    help="Gap-K%'s smoothing window; 3 is the paper's default outside the LLaMA family",
)
parser.add_argument("--min-tokens", type=int, default=32)
parser.add_argument(
    "--scored-text",
    choices=("after", "with_context"),
    default="after",
    help="what Min-K%%++ scores: the bare revised hunk, or the hunk with its context lines",
)
parser.add_argument(
    "--legacy-corpus",
    action="store_true",
    help="read inputs not cut under the current label rules, to reproduce an earlier result; "
    "recorded in the output",
)
parser.add_argument("--out", type=Path, default=Path("contamination.json"))
args = parser.parse_args()

# The guided instruction names the provenance, as guided completion does: the probe is whether
# the model can reproduce a specific file's content when told where it came from.
GUIDED = (
    "Below is the first part of a code hunk from the file {path} in the {project} repository.\n"
    "Continue it exactly as it appears in that repository. Reply with the continuation only.\n\n"
    "First part:\n{prefix}\n"
)


def scored_text(row: dict) -> str:
    """The text a membership score is computed over, per --scored-text."""
    if args.scored_text == "after":
        return str(row["after"])
    if "context_before" not in row:
        raise SystemExit(
            f"--scored-text with_context needs a corpus built with context: {row['id']}"
        )
    parts = (row["context_before"], str(row["after"]), row["context_after"])
    return "\n".join(part for part in parts if part)


def load(path: Path) -> tuple[list[dict], dict]:
    rows = derived_file_rows(path, legacy=args.legacy_corpus)
    kept, removed = run_dedup(rows)
    counts = {"examples": len(rows), "after_dedup": len(kept), "dedup_removed": removed}
    return kept, {**counts, "legacy_corpus": args.legacy_corpus}


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
        if len(tok(scored_text(r), add_special_tokens=False)["input_ids"]) >= args.min_tokens
    ]


post_e, pre_e = eligible(post_rows), eligible(pre_rows)
post_counts["eligible"], pre_counts["eligible"] = len(post_e), len(pre_e)
print(f"post {post_counts}\npre  {pre_counts}", flush=True)

# --- Min-K%++ on the base checkpoint ---------------------------------------------------
model = AutoModelForCausalLM.from_pretrained(
    MEMBERSHIP_MODEL_ID, dtype=torch.bfloat16, device_map="cuda:0"
)
model.eval()
post_tokens = [token_statistics(model, tok, scored_text(r)) for r in post_e]
pre_tokens = [token_statistics(model, tok, scored_text(r)) for r in pre_e]
membership_dtype = str(next(model.parameters()).dtype).removeprefix("torch.")
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
            "gap_k_percent": gap_k_percent(t, k=args.k, window=args.gap_window),
        }
        for r, t in zip(rows, tokens, strict=True)
    ]


scores = per_example(post_e, post_tokens, "post") + per_example(pre_e, pre_tokens, "pre")

# Membership is the registered PRIMARY instrument and it is finished; write it before the
# generative half begins. Job 145093 reached exactly this point and hit its wall during
# guided completion, and because the script wrote once at the end, a completed Min-K%++ on
# fifteen times the earlier sample was thrown away. Guided completion is registered as a
# null instrument, so losing it costs a line in a table; losing the primary costs the run.
_partial = {
    "membership_model": MEMBERSHIP_MODEL_ID,
    "scored_text": args.scored_text,
    "k": args.k,
    "post": {"path": str(args.post), **post_counts},
    "pre": {"path": str(args.pre), **pre_counts},
    # The per-example scores, not an aggregate: the aggregate is computed after guided
    # completion because it tabulates all three methods, and every membership number can be
    # recomputed from these offline.
    "scores": scores,
    "complete": False,
    "provenance": run_provenance(),
}
args.out.with_suffix(".partial.json").write_text(json.dumps(_partial, indent=2) + "\n")
print(f"wrote {args.out.with_suffix('.partial.json')} (membership only)", flush=True)

# --- Guided completion on the registered model -------------------------------------------
generator = HFGenerator(model_id=MODEL_ID, max_new_tokens=MAX_NEW_TOKENS)
guided_dtype = generator.computed_dtype  # kept: the generator is released before the write


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
            {
                "id": r["id"],
                "window": window,
                "predicted": predicted,
                "reference": reference,
                # Reported beside the verbatim rate, which sat at its floor on the pilot. Which
                # of the two the battery reads is a Stage 1 decision; reporting both is not.
                "edit_similarity": score(predicted, reference)["edit_similarity"],
            }
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
    gap_window=args.gap_window,
)
guided_similarity = {
    window: (
        sum(g["edit_similarity"] for g in guided_records if g["window"] == window)
        / max(1, sum(1 for g in guided_records if g["window"] == window))
    )
    for window in ("post", "pre")
}
print(f"guided edit similarity {guided_similarity}", flush=True)
for method in ("min_k_plus_plus", "min_k_percent", "gap_k_percent", "guided_completion"):
    r = report[method]
    print(
        f"{method:<18} post={r['post_cutoff']:+.4f} pre={r['pre_cutoff']:+.4f} gap={r['gap']:+.4f}",
        flush=True,
    )

args.out.write_text(
    json.dumps(
        {
            "provenance": run_provenance(),
            "membership_model": MEMBERSHIP_MODEL_ID,
            # Two models at two precisions: token statistics come from a bf16 model, guided
            # completion from the registered generator.
            "membership_dtype": membership_dtype,
            "guided_dtype": guided_dtype,
            "guided_model": MODEL_ID,
            "k": args.k,
            "min_tokens": args.min_tokens,
            "scored_text": args.scored_text,
            "post": {"path": str(args.post), **post_counts, "guided_pairs": len(post_pairs)},
            "pre": {"path": str(args.pre), **pre_counts, "guided_pairs": len(pre_pairs)},
            "report": report,
            "guided_edit_similarity": guided_similarity,
            "scores": scores,
            "guided": guided_records,
        },
        indent=2,
    )
)
print(f"wrote {args.out}")
print("BATTERY_OK")
