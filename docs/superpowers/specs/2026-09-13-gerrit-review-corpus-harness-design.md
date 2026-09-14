# Gerrit review corpus harness (design)

**Date:** 2026-09-13 · **Status:** proposed · **Author:** AJ Barea

## Context

- The Federated Agents direction spec
  (`papers/docs/superpowers/specs/2026-09-12-federated-agents-direction-design.md`,
  accepted) sequences an RQ1 feasibility gate before anything federated is built, and
  places it in this repo because the HF and PEFT stack already lives here.
- RQ1 needs refinement pairs mined from Gerrit. So does RQ2, which reuses the same
  corpus with four more organizations and a provenance manifest layered on top.
- The corpus is therefore not throwaway experiment scaffolding. It is the input to every
  first-author paper in the direction, and every threat to validity a reviewer will raise
  about those papers lives in it rather than in the training code.
- Target venue for RQ1 is the MSR 2027 Registered Reports track with EMSE (Stage 1
  report due 2026-11-20, Stage 1 notification 2027-02-04). That imposes two
  requirements on this harness that would otherwise be optional, both recorded in the
  design below: a pilot slice that can justify the sample size before the confirmatory
  study runs, and fetch timestamps that prove the confirmatory data was collected after
  in-principle acceptance.
- `docs/superpowers/plans/` gets the execution plan once this spec is accepted.

## Decision

Build the corpus as a staged pipeline of seven commands under `sphragis/corpus/`, each
writing an immutable artifact plus a manifest, with the confirmatory test window
**defined but not fetched** until after Stage 1 acceptance.

Experiments read from frozen artifacts on disk. No experiment talks to a Gerrit server.

## Precondition

Collection does not start until the RIT Human Subjects Research Office determination is
on file (direction spec, sequence item 2). The `fetch` command refuses to run unless
`corpus/HSRO.md` exists and records a determination date. That is a real gate, not a
comment: repository mining is human-subjects research (Gold and Krinke, EMSE), and an
RR reviewer will look for it.

## Design

### 1. Layout

```
sphragis/corpus/
  __init__.py
  cli.py           # `python -m sphragis.corpus <stage>`
  gerrit.py        # REST client: paging, retry, rate limit, version capture
  scrub.py         # identity stripping, runs before anything is persisted
  examples.py      # raw changes -> refinement pairs
  dedup.py         # near-duplicate detection
  split.py         # time split, change-id grouping, window sealing
  manifest.py      # counts + content hashes; extends sphragis/provenance.py
  score.py         # the metric ladder
datasets/gerrit/
  <org>/
    raw/<YYYY-MM>.ndjson.gz      # never edited after write
    examples/<YYYY-MM>.parquet
    splits/{pilot,train,dev,test}.parquet
    manifest.json
    seal.json
corpus/
  HSRO.md                        # determination record
  provenance/<org>.toml          # shareable/private repo declaration (RQ2)
```

`manifest.py` extends the existing `sphragis/provenance.py` rather than duplicating it.
That module already writes git SHA, branch, package versions and run config to JSON; a
corpus manifest is the same record with corpus counts and hashes added.

### 2. Stages

Each stage is idempotent, resumable, and writes a manifest fragment. Re-running a stage
whose inputs and parameters are unchanged is a no-op that re-verifies hashes.

**`fetch`** — Gerrit REST to `raw/<YYYY-MM>.ndjson.gz`. One file per organization-month.
Queries `status:merged OR status:abandoned` with `o=ALL_REVISIONS&o=ALL_FILES&o=DETAILED_ACCOUNTS`,
plus the per-change comments endpoint. Records in the manifest: instance URL, the exact
query string, page size, Gerrit server version, UTC start and end timestamps of the
fetch, HTTP status counts, and the number of changes retrieved. Honors `Retry-After`,
backs off on 429 and 5xx, and caps concurrency at one request in flight per instance.

The fetch timestamps are the evidence that Stage 2 data collection postdates
in-principle acceptance. They are not decoration.

**`scrub`** — strips author and reviewer names, emails, usernames and numeric account
ids, replacing each with a salted SHA-256 truncated to 12 hex characters. The salt lives
in `.env` and is never committed, so the mapping is not reversible from the repository.
Runs inline in `fetch`, before the first byte is written to disk, so no raw identity ever
lands in the tree. Structural fields that carry no identity (change-id, revision number,
project, timestamps) are kept.

**`build`** — raw changes to refinement pairs. One example is a hunk from patch set *n*
plus the inline reviewer comments anchored inside that hunk, paired with the same hunk at
patch set *n+1*. Pairs whose comments fall outside any changed hunk are dropped, and the
drop count is reported by reason. Every example carries: org, project, change-id,
revision pair, file path, language, created timestamp, and the byte offsets the hunk came
from, so any example can be traced back to its raw record.

**`dedup`** — three stages, in this order. `research(2026-09)`: this is the pipeline
current large-corpus practice converged on (Olmo 3, Blu-WERP, `allenai/duplodocus`), and
the stages are complementary rather than alternatives.

1. **Exact.** Content hash over the normalized pair. Cheap, lossless, run first.
2. **Near-duplicate.** MinHash over shingles, LSH banding for candidates, exact Jaccard
   to confirm. Catches the revision-to-revision near-copies.
3. **Repeated substring.** Corpus-wide shingle frequency: a character k-gram appearing in
   more than `boilerplate_max_docs` distinct examples is repeated text. Catches boilerplate
   that recurs across otherwise distinct examples: license headers, generated bindings,
   translation catalogs, vendored code, requirements pins.

Stage 3 was specified as a suffix array and is implemented as shingle frequency. A
linear-time suffix array needs a C extension, and a pure-Python one is O(n^2 log n) over
the concatenated corpus, which will not hold at full scale. Shingle frequency finds the
same cross-document repetition at k-gram granularity with no new dependency. Revisit if
the boilerplate counts look wrong against a manual read of what it drops.

Stage 3 also differs from the text-corpus version in what it does with a hit. Text
pipelines excise the repeated substring from the document. Excising from a code hunk would
produce code that does not parse, so here a hit **drops the example** and records why,
above a boilerplate fraction threshold fixed in the Stage 1 report.

Normalization strips whitespace and comments only. It does not rename identifiers,
because identifier style is part of what RQ1 is asking about.

Removal counts are reported per stage and per bucket: exact, near-duplicate within one
change, near-duplicate across changes, cross-org, boilerplate.

This is the stage that decides whether the RQ1 result is real. Consecutive revisions of
one change are near-copies, and templated files recur at high volume in both OpenStack
and Qt.

**`split`** — per organization, four disjoint windows ordered by change creation time,
grouped so no change-id straddles a boundary:

| Window | Purpose | Collected |
|---|---|---|
| `pilot` | sample-size justification and outcome-neutral tests for Stage 1 | before Stage 1 |
| `train` | adapter training | before Stage 1 |
| `dev` | internal read on the direction, RPA progress section | before Stage 1 |
| `test` | the confirmatory comparison | **after** Stage 1 acceptance |

`split` writes `test` as a *definition* rather than data: the query bounds, the
organization list, and the filters that will select it, hashed into `seal.json` with a
UTC timestamp. `fetch --window test` refuses to run while `seal.json` has no recorded
in-principle acceptance date.

The `dev` window is what lets the gate be read internally without burning the
confirmatory set. It is a real split, drawn from the same distribution, and any number
computed on it is labeled exploratory wherever it appears.

**`freeze`** — writes `manifest.json`: per-window example counts, per-org and per-project
counts, language histogram, date bounds, dedup removal counts, and a SHA-256 over each
window's sorted example ids. A test asserts the manifest against the parquet files, and
CI runs it, so a paper number cannot drift from the corpus that produced it.

**`score`** — the metric ladder, computed together on every evaluation:

- **Exact match.** Primary, and the only metric the RQ1 pass rule reads.
- **Normalized exact match.** After formatting normalization, so a rewrite that differs
  only in line wrapping is not scored as a miss.
- **Edit similarity.** Character-level, for effect size when exact match is sparse.

Exact match alone is a weak oracle for code refinement and a known reviewer target, so
the other two are reported in every table. The pass rule binds to exact match because a
pre-registered gate needs one unambiguous metric; the ladder exists so a null result
cannot be dismissed as a metric artifact. That division is stated in the Stage 1 report
and cannot be renegotiated afterwards.

**LLM-as-judge is deliberately excluded, from both the pass rule and the ladder.**
`research(2026-09)`: code review evaluation has moved toward reference-free and
judge-based metrics (CRScore, CR-Bench, CodeFuse-CR-Bench, AACR-Bench), and the pull to
follow is real. It is the wrong call for this study on two grounds. Agreement between
judge pipelines (G-Eval, LLM-as-a-judge, across three frontier models) and human labels
measures 0.44 to 0.62 on real bot review comments, and judge outputs are sensitive to
prompt design and sampling, so they do not reproduce reliably. A pre-registered gate
cannot bind to a metric that moves when the judge is re-run. Those metrics also target
review *comment* quality, which is a different task from the refinement generation
measured here. The Stage 1 report states this exclusion with the agreement figure rather
than leaving it as an omission a reviewer has to notice.

### 3. Outcome-neutral tests

These check that the apparatus works, independent of which way RQ1 falls. They are
pre-registered, they run before the confirmatory comparison, and a failure halts the
study rather than producing a result.

1. **Contamination battery.** A date bound is an argument, not a measurement.
   `research(2026-09)`: five contamination-detection families are in current use, and
   three of them apply to a study that does not control the base model's pretraining.
   All three run against the post-cutoff corpus with a pre-cutoff control window drawn
   from the same projects, so each produces a comparison rather than an absolute number.

   - **Time-based partition.** The corpus starts 2024-10-01 against a base model
     published 2024-09-17. This is the design, and it is stated as the weakest of the
     three because it rests on a publication date.
   - **Min-K%++ probability.** Average log-likelihood of the least probable k% of tokens,
     post-cutoff against pre-cutoff. The standard instrument.
   - **Guided completion.** Prompt with the prefix of a held-out hunk, measure verbatim
     continuation rate, post-cutoff against pre-cutoff.

   Known limitation, stated in the report rather than discovered by a reviewer: Min-K%
   and Min-K%++ detect verbatim overlap well and degrade on paraphrased variants, so a
   clean result bounds verbatim memorization and does not rule out paraphrased exposure.
   ConStat and canary-GUID detection are excluded: the first needs rephrased samples and
   reference models, the second only applies when you control pretraining.
2. **Positive control.** An adapter trained on organization A beats the base model on
   A's own held-out data. If it does not, the training setup is broken and the
   comparison between adapters means nothing.
3. **Manipulation check.** Training loss decreases and the adapter weights differ from
   initialization, per run and per seed.
4. **Leakage check.** Near-duplicate rate between any training window and the test window
   is below a threshold fixed in the Stage 1 report.
5. **Non-degeneracy.** Exact match is neither 0 nor 1 for every condition on both test
   sets.

### 4. Sample size

The pilot window feeds a simulation-based power analysis: bootstrap the per-change exact
match variance observed in the pilot, and report the minimum detectable difference in
organization-specific gain at 80% power for the test window's change count. If the test
window is too small for the effect the pilot suggests, the window widens before Stage 1
submission, not after.

This is the number an RR reviewer asks for and the reason the pilot has to exist before
the November deadline.

### 5. Training and inference configuration

Pinned here so the Stage 1 report can state it and the three conditions are identical
except for the training organization. `research(2026-09)` for each value.

| Setting | Value | Why |
|---|---|---|
| Base model, primary | `Qwen/Qwen2.5-Coder-7B-Instruct` | published 2024-09-17, giving a 24-month clean post-cutoff window |
| Base model, secondary | `Qwen/Qwen3-Coder-30B-A3B-Instruct` | released 2025-07-22; exploratory replication over the shorter window |
| LoRA rank | 32 | performance rises with rank to about 32 and flattens above it |
| LoRA alpha | 64 | alpha = 2r; fixed low alpha at high rank is unstable |
| Target modules | `q_proj k_proj v_proj o_proj gate_proj up_proj down_proj` | attention plus MLP beats attention alone, and coverage matters more than rank |
| Decoding | greedy, fixed max new tokens | exact match needs deterministic decoding |
| Seeds | 3 | median reported with range |

**Base model, the real decision.** A current model is the stronger-looking choice and is
the wrong one here. Qwen3-Coder released 2025-07-22, which leaves roughly 14 months of
post-cutoff review data against 24 for the 2024-09 model, and a stronger base solves more
refinements unaided, compressing the very gap the gate is trying to detect. The older
model is registered as primary for both reasons. The cost is the reviewer objection that
a two-year-old 7B model is not what anyone deploys, so the Qwen3-Coder replication is
pre-registered as a secondary, labeled exploratory, reported in full and excluded from
the pass rule.

**A null result has a second explanation, and it gets its own branch.** LoRA learns less
than full fine-tuning, most visibly on code. So "no organization-specific gain" is
consistent with "LoRA at rank 32 lacked the capacity to pick one up." The conditional
analysis in the Stage 1 report pre-commits: if the gate fails, rerun at rank 64 before
concluding anything about fingerprints, and report both.

### 6. Statistics

- The unit of resampling is the **change**, not the example. Examples inside one change
  are dependent, and resampling examples would understate the interval.
- Method: **pairs cluster bootstrap** over change-ids, paired across conditions because
  every condition is evaluated on the same test set. Both adapters see the same resampled
  clusters in each replicate.
- Known property to report: when cluster sizes differ, the number of examples varies
  across bootstrap replicates even though the number of clusters does not.
- Report the effect size alongside the interval, not the interval alone.

### 7. Reproducibility

- Every artifact is content-hashed and every stage records the git SHA that produced it.
- Seeds are fixed per stage and recorded. The repo already keys Python, NumPy and torch
  seeds per `(round, client)` for client training; corpus stages take the same treatment.
- A `make corpus-verify` target re-derives the manifest from the parquet files and fails
  on any mismatch. CI runs it.
- The raw snapshots are the reproducibility floor. Gerrit changes can be edited or
  deleted upstream, so a rerun against the live server months later would not reproduce.
  Nothing downstream may re-fetch.

### 8. Release

Deferred to a separate decision, not blocked here. Before any derived data is published,
check each project's code license and each Gerrit instance's terms of use, and publish no
contributor identity. The manifest and the code are releasable independently of the data,
which is enough for an artifact badge if the data cannot be redistributed.

## ADR

**Decision.** A staged, manifest-backed corpus pipeline under `sphragis/corpus/`, with
identity stripping at ingestion, near-duplicate removal as a first-class stage, a metric
ladder around a single binding metric, and the confirmatory test window defined at Stage
1 but fetched only after acceptance.

**Rejected.**

- *Scripts in a notebook, formalized later.* The corpus is the input to every paper in
  the direction. Retrofitting dedup and provenance onto results that already exist means
  redoing the results.
- *Random split with a duplicate filter.* Consecutive revisions of one change are
  near-copies, so a random split leaks regardless of filtering. Time split plus
  change-id grouping is the only defensible cut.
- *Exact match as the sole metric.* A null gate scored only by exact match invites the
  reading that the metric caused it.
- *Exact match demoted to one of three co-equal metrics.* A pre-registered gate needs one
  binding metric decided in advance, or the pass rule is not a pass rule.
- *Fetching the test window now and promising not to look.* Not enforceable, and it
  forfeits the RR2 evidence that collection postdated acceptance.
- *A separate repository for the harness.* RQ2 federates this corpus on this repo's
  Flower stack, and the manifest work reuses `sphragis/provenance.py`.
- *LLM-as-judge anywhere in the measurement.* Current code-review evaluation is moving
  that way, but judge-to-human agreement of 0.44 to 0.62 and prompt sensitivity make it
  unusable for a pre-registered pass rule. Excluded explicitly, with the figure.
- *A current base model as primary.* Qwen3-Coder would look stronger and would cut the
  clean post-cutoff window from 24 months to about 14 while compressing the effect the
  gate measures. Registered as a secondary replication instead.

**Evidence.** `research(2026-09)`. Each item below is a search-level characterization and
is logged in `papers/related-work/intake.md` as unverified. Nothing here is cited in a
manuscript until the intake log records it as read in full.

| Finding | Effect on this design |
|---|---|
| Large-corpus pipelines converged on exact, then MinHash with Jaccard confirmation, then suffix-array repeated-substring removal (Olmo 3; Blu-WERP; `allenai/duplodocus`) | `dedup` became three stages instead of one |
| Contamination families in current use: time partition, Min-K% probability, guided completion, ConStat, canary GUID; Min-K% degrades on paraphrase | The probe became a three-method battery with a stated limitation |
| Judge-to-human agreement 0.44 to 0.62 for G-Eval and LLM-as-judge on real review comments across three frontier models; judges are prompt- and sampling-sensitive | LLM-as-judge excluded from the pass rule and the ladder, with the figure stated |
| LoRA performance rises with rank to about 32; alpha = 2r; attention plus MLP beats attention alone, and coverage matters more than rank | The configuration table above |
| LoRA learns less than full fine-tuning, most visibly on code | A failed gate gets a rank-64 rerun before any conclusion |
| Pairs cluster bootstrap is the applicable procedure for clustered data; bootstrap sample size varies when cluster sizes differ | The resampling unit is the change, and the caveat is reported |
| Qwen3-Coder released 2025-07-22 | Registered as the exploratory secondary, not the primary |
| Code authorship attribution collapses across contexts: 92.6% on competition data, at or below chance (0.2%, 690 authors) on coursework | Nearest prior framing, and a named threat: a fingerprint result may not transfer off open-source review |
| Fingerprinting AI coding agents on GitHub: 97.2% F1 over five agents from 41 features, commit-message conventions most discriminative | Closest methodological neighbor; the report must say why it measures code hunks and review comments rather than commit metadata |
| OSF registrations are frozen, time-stamped, embargoable to four years; registration is independent of the journal; SE Stage 1 reports are typically also posted to arXiv | Pre-registration goes to both, OSF for the seal |

**Consequences.**

- The confirmatory RQ1 result cannot be produced before Stage 1 notification
  (2027-02-04). The `dev` window covers the internal read and the RPA progress section in
  the meantime, labeled exploratory.
- If MSR Stage 1 is rejected, the seal is discarded and the test window is fetched
  immediately. Nothing else in the design changes, and the corpus is unaffected.
- The harness is a publishable artifact in its own right once four more organizations are
  added for RQ2.
