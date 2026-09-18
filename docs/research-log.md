# Sphragis — research log

The dated record of what was built, measured, found and corrected, moved verbatim from
`IMPL.md` on 2026-09-15 when it outgrew a current-work note. Numbers here are the
evidence the Stage 1 report cites; withdrawn and superseded claims stay, marked, so the
corrections are auditable. `IMPL.md` holds what is in flight; `ROADMAP.md` the long view.

---

## Current focus

### Repo split from phalanx-fl (2026-09-13)

The corpus and measurement code was built inside `phalanx-fl` and moved here. It had grown to
80% of that repo's test suite while importing exactly one function from it, in a repo whose
stated purpose is a Flower testbed.

The decisive argument was not tidiness but a policy conflict: phalanx's roadmap carries a
recurring invariant to ride the latest Flower release, and a paper artifact needs the opposite.
Those are contradictory release policies sharing one lockfile, already visible in Dependabot
alerts pinned by `flwr` on a repo whose paper code never imported `flwr`.

`phalanx.corpus` became `sphragis.corpus`, plan B's three modules became `sphragis.measure`,
and `provenance_header` was copied rather than imported.

### Next: plan A2, stage bodies

Wire `fetch`, `build`, `dedup`, `split` and `freeze` to real artifacts under
`datasets/gerrit/<org>/`.

Everything else is unblocked: `gerrit.fetch_changes` takes a transport seam, so the stage
bodies can be built and tested against recorded fixtures before any live collection.

---

### Corpus yield, measured on live OpenStack data (2026-09-14)

The first real fetch. Numbers the Stage 1 report's sampling section needs, and which no
amount of reading the API docs would have produced.

| | |
|---|---|
| merged post-cutoff changes sampled | 35 |
| with a comment on a code file | 8 (23%) |
| comments examined | 15 |
| anchored **inside** a changed hunk | 4 (27%) |
| within 10 lines of a changed hunk | 10 cumulative (67%) |
| comment on the final patch set, no successor | see note |

**Correction (same session).** The first run counted 4 of 15 comments as "diff errors" and
I attributed them to comments on the final patch set. That attribution was never verified:
the diagnostic run died on a transient network failure, and a clean re-run of 9 comments
found 1 on a final patch set, 8 diffs fetched fine, and **zero** genuine diff errors. So the
final-patch-set condition is real and confirmed, the rate is not established, and some of
the original 4 were probably network flakes rather than data. `has_successor_revision` is
correct either way; the number attached to it was not.

**The anchoring rule is now a pre-registration decision with a measured cost.** The spec
says a comment must fall inside a changed hunk between patch set n and n+1. That is the
defensible rule, because the edit plausibly addresses the comment, and it keeps 27% of
comments. Widening to a 10-line neighbourhood keeps 67%, roughly 2.5x the data, at the
cost of a weaker link between the comment and the edit.

Recommendation: keep strict as primary and report the widened number as the sensitivity a
reviewer will ask for. Widen only if the pilot power analysis says the strict corpus is too
small, so the decision is driven by the minimum detectable effect rather than convenience.

Rough projection at the strict rule: OpenStack's 2,350 merged changes in October 2024 imply
on the order of a few hundred examples per month. Verify against a real month before the
report quotes anything.

### First real fetch: October 2024 OpenStack (2026-09-14)

| | |
|---|---|
| changes returned by the query | 2,350 in 24 pages, 13s |
| **dropped as created before the cutoff** | **672 (29%)** |
| kept | 1,678 |
| snapshot size | 503 KB gzipped NDJSON |
| owners reduced to a pseudonym only | 1,678 / 1,678 |

The 2,350 matches the figure the direction spec quotes for October 2024, so the query is
selecting what it intended. The 29% is the `after:` bug measured rather than argued:
without the client-side `created_on_or_after` filter, 672 changes created *before* the
base model's release date would have entered the corpus, and the contamination argument
would have been false while appearing to hold.

### Half the "review comments" are not reviews (2026-09-14)

Measured on live OpenStack data, 62 code-file comments:

| | |
|---|---|
| authored by the **change owner** | 32 (52%) |
| carrying `in_reply_to` | 34 (55%) |
| message is literally `"Done"` | 15 |

These are the author acknowledging a fix, not an instruction to make one. Feeding them to
the model pollutes the input and **leaks the answer**: "Done" says the edit was applied,
which is what the model is supposed to produce. `is_reviewer_comment` drops comments whose
author is the change owner.

**Two bugs found while fixing this, both of the silent kind.**

*One example per comment.* The spec pairs a hunk with the comments anchored inside it,
plural. Emitting one example per comment produced identical before/after rows that dedup
then discarded as exact duplicates, losing every comment but the first. The tell was a 64%
exact-duplicate rate; after grouping by hunk it is 16%.

*A filter that matched nothing.* The author filter dropped exactly 0 comments on real data
while 73 acknowledgements sailed through. The snapshot's owner had been scrubbed to a
12-hex string at fetch, while the comments endpoint returns a raw integer account id, so
the comparison was `str != int` and always true. The unit tests passed because they used
consistent ids on both sides. `is_reviewer_comment` now raises `TypeError` when the two
sides disagree in type, which converts a silent no-op into a loud failure; verified by
running it against the real pipeline and watching the guard fire.

**Fixed, and measured.** `corpus/fetchers.py` supplies a comment fetcher that scrubs with
the same salt as the change fetch, so both sides are pseudonyms. On 584 real changes:
**278 author comments dropped, against 0 before**, and acknowledgements surviving into the
model input fell from **73 to 5**. The residual 5 are comments carrying no author object,
which are kept deliberately rather than guessed at. The diff fetcher does *not* scrub: a
diff carries no account objects, and running the identity sweep over it would rewrite
anything in the source that merely looks like an email address, corrupting the code under
study.

### First honest example count (2026-09-14)

`build` run against the real October 2024 OpenStack snapshot, 476 of 1,678 changes
processed in 95 seconds before a time cap.

| | |
|---|---|
| **examples produced** | **208** |
| examples per change | 0.437 |
| **projected for the full month** | **~733** |

Drops, by reason:

| reason | count |
|---|---|
| comment on a metadata pseudo-file | 675 |
| comment anchored in no changed hunk | 191 |
| comment on the last patch set | 42 |
| comment with no line anchor | 5 |

**This revises the yield estimate upward, substantially.** The earlier projection chained
two small-sample rates (23% of changes carry a code comment, 27% of those anchor) to imply
roughly 6% of changes yielding an example. The real rate is 0.437 examples per change,
because changes that do carry comments frequently yield several. At ~733 examples per
month, a ten-month training window gives OpenStack alone something in the thousands, with
Qt on top.

**Commit-message comments are the largest single drop category**, 675 against 208 kept.
That makes the pre-registered exclusion of commit metadata a decision with real weight
rather than a formality, and it belongs in the report's sampling section by number.

**The strict anchoring rule looks viable.** 208 anchored against 191 unanchored is roughly
52% of code-file comments landing inside a changed hunk, against the 27% the 15-comment
sample suggested. The case for widening to a 10-line neighbourhood is correspondingly
weaker; decide it on the pilot's minimum detectable effect, not on this, but the strict
corpus is no longer obviously too small.

### Two API facts the code was wrong about, both found by fetching

- **The change payload carries no file content.** `o=ALL_FILES` returns only metadata
  (`lines_deleted`, `old_sha`, `size_delta`). Hunks now come from Gerrit's own diff
  endpoint, which also keeps hunk boundaries identical to what the reviewer saw.
- **`after:` filters on last update, not creation.** `after:2024-10-01 before:2024-10-03`
  returned a change created 2024-08-26. The post-cutoff contamination argument rests on
  creation date, so `created_on_or_after` enforces it client-side.

### Two design changes from current literature (`research(2026-09)`)

**Hunks now carry surrounding context.** arXiv:2607.25851 (*Rethinking Training Data for
Generating Code Review Comments*, read 2026-09-14) names three sources of misalignment
between a code change and its review comment: semantic ambiguity, lack of actionability,
and **context dependence**. The third is a real gap here, and it is the same root cause as
a failure already measured: a bare hunk gave the model no way to know its indentation
level, so a rewrite that was otherwise exactly right lost strict exact match. Context is
prompt material only; the target stays the hunk and scoring compares only the hunk.

**Corpus construction stays deterministic, deliberately.** The field has moved toward
LLM-based filtering for comment actionability, and that same paper reports up to a third of
comments being vague or non-actionable. It also concludes that *detecting misaligned
training pairs remains challenging even with LLM-based approaches*. An LLM filter would
reintroduce exactly the reproducibility problem that kept LLM-as-judge out of the metric,
so non-actionability is treated as a **measured characteristic of the corpus** to report,
not a filter to apply. The deterministic filters stay: author comments, metadata
pseudo-files, and hunk anchoring.

### Training budget pinned (`research(2026-09)`)

A Stage 1 pre-registration item rather than a tuning knob: if the budget differed between
arms, the comparison would measure the budget rather than the organization.

| | | why |
|---|---|---|
| learning rate | 2e-4 | the standard LoRA starting point; usable band 1e-4 to 2e-4 |
| epochs | **2** | accuracy *falls* as epochs rise, and models converge within tens of steps then memorise. With a corpus in the low thousands per organization, overtraining is the likelier failure |
| batch size | 16 | largest that comfortably fits 7B plus optimiser state on one GH200 |
| scheduler | cosine, 3% warmup | |
| max sequence | 2048 | |

Rank stays 32 against current tooling defaults of 16, because the rank-versus-performance
evidence supports it and adapter capacity is precisely what a null result would otherwise
be blamed on.

### Example yield, both organizations (2026-09-14)

| | OpenStack | Qt |
|---|---|---|
| changes kept after cutoff | 1,678 | 3,336 |
| examples per change | 0.437 | 0.331 |
| **projected examples, October 2024** | **~733** | **~1,103** |

Qt yields fewer examples per change but more in total, because it has twice the changes.
Over a ten-month training window that is roughly 7,000 for OpenStack against 11,000 for Qt.

**This is a sampling constraint, not just a statistic.** Matched training set size is a
controlled variable: without it the larger organization's adapter could win for reasons
unrelated to conventions. So the usable per-organization corpus is bounded by the smaller,
OpenStack, and Qt gets downsampled to match. The number to plan against is therefore
OpenStack's, not the total.

Drop profiles differ too. Qt shows no final-patch-set drops in this sample and a higher
rate of comments with no line anchor. Worth reporting per organization rather than pooled.

### Qt and OpenStack page very differently (2026-09-14)

| instance | `n=25` | `n=100` | `n=500` |
|---|---|---|---|
| OpenStack | - | **100** | - |
| Qt | 10 | **10** | 10 |

**Qt caps a page at 10 changes regardless of `n`.** Both months now fetched for real:

| organization | changes | pages | dropped as pre-cutoff |
|---|---|---|---|
| OpenStack, 2024-10 | 2,350 | 24 | 672 (29%) |
| Qt, 2024-10 | 3,863 | **387** | 527 (14%) |

Sixteen times the round trips for 1.6x the changes. **Both totals match the figures the
direction spec quotes**, so the queries select what they were meant to.

The pre-cutoff drop rates differ substantially between the two organizations, 29% against
14%. That is worth reporting rather than averaging away: it means the `after:` operator's
update-time semantics bite the two instances differently, presumably because their review
cadences differ.

The paging loop is already robust to this because it advances by `len(page)` rather than by
the requested size, so nothing breaks; it is a cost and pacing fact, not a bug. It does mean
the two organizations cannot be fetched on the same assumptions, and a Qt fetch wants to be
resumable rather than one long run.

### `urlopen` raises on 5xx, which bypassed the retry budget

`http_transport` originally let `urllib` raise. `urlopen` raises `HTTPError` on 4xx and
5xx, so a retryable 500 from Qt propagated as a fatal exception and the retry logic in
`gerrit._get` never saw it, despite 500 being in its retryable set. The transport now
returns `(status, headers, body)` for error responses too, which is what makes the budget
and `Retry-After` handling real rather than decorative.

### What the pilot may and may not be used for (`research(2026-09)`)

Checked before running a pilot power analysis, and it changes how the result may be
reported.

**Variance from a pilot is legitimate. An effect size from a pilot is not.** Pilot data
gives usable estimates of covariances and error variances, but effect-size estimates from
small samples are highly variable, and a significant result from an underpowered study
systematically overestimates the true effect. NIH explicitly cautions against basing power
on a small pilot's effect size.

`minimum_detectable_effect` is already the right shape for this: it takes the pilot as a
**variance model** and *computes* the detectable effect, rather than estimating an effect
from the pilot and powering to it. That distinction should be stated in the report, because
a reviewer who knows this literature will look for it.

Sample-size justification in descending order of defensibility: pooled estimate from a
systematic review, then a single prior study, then pilot data. So the stronger anchor for
the expected exact-match rate is **published code-refinement numbers**, not this corpus.
The pilot's job is the clustering structure and feasibility, not the headline rate.

**A smaller proxy model cannot stand in for the registered one here.** Exact match is
close to binomial, so its variance depends on the mean rate, and a 1.5B and a 7B will not
share a mean rate. Running the local 1.5B over real examples validates that the
measurement path works end to end; it is a pipeline check, not a variance estimate for the
7B, and must be labelled as such wherever it appears.

### Exact match on real hunks: a base-model floor, not a broken metric (2026-09-14)

First run of the whole measurement path over **real** examples rather than toy ones.
Qwen2.5-Coder-1.5B, 30 real OpenStack hunks, 12 changes, no adapter:

| metric | value |
|---|---|
| exact match | **0.000** |
| exact match, unextracted | 0.000 |
| normalized exact match | 0.033 |
| edit similarity | 0.555 |

On three hand-made toy examples the same model scored 0.67 exact match. On real hunks it
scores **zero**.

**Why this matters more than a weak proxy result.** The Stage 1 protocol makes exact match
the single binding metric and lists non-degeneracy as an outcome-neutral test: *exact match
is neither 0 nor 1 for every condition on both test sets*. That test is designed to halt the
study, and on this proxy it fails. If the registered 7B also floors at or near zero on real
hunks, the gate cannot detect an organization-specific difference no matter how large one
is, and RQ1 returns a null caused by a floor effect rather than by an absence of
fingerprints.

Edit similarity of 0.555 says the model is producing *related* output, not noise. The
problem is the strictness of the oracle against real multi-line, context-dependent hunks,
not the model failing to engage with the task.

**Correction, same session.** The paragraph above was written before checking what
fine-tuned models actually score, and it overstated the risk. `research(2026-09)`:
CodeReviewer reports **EM 30.32%** on code refinement, against T5 at 15.08% and CodeT5 at
24.41%, with ensembles reaching 36.32%. So exact match is demonstrably attainable on this
task in the 15-36% band **once a model is fine-tuned for it**.

That reframes the measurement above entirely. A *base* model, zero-shot, with no adapter,
scoring EM 0 is the expected result rather than an alarm. The study compares **adapted**
models, and adaptation is precisely what lifts EM off the floor. The design is not broken.

What survives is a narrower and still real check: the pilot has to confirm the adapted 7B
clears the floor on *this* corpus, since CodeReviewer's 30% is on its own differently
curated benchmark. If it does not, the pass rule needs rethinking before 2026-11-20. The
prior from the literature says it should.

**What this changes.** The pilot's first job is no longer sample-size estimation, it is
confirming the adapted 7B clears the exact-match floor on this corpus. That has to be settled
**before** 2026-11-20, because the pass rule is what gets pre-registered and it cannot be
loosened afterwards without deviating from the protocol. Options if the 7B also floors, all
of which are pre-registration decisions rather than post-hoc rescues: bind the rule to
normalized exact match instead; restrict to single-line hunks where exact match is
attainable; or keep exact match and pre-register a sample size large enough for a very
small rate to be estimable.

Caveats, stated because the number will be quoted: 1.5B is far weaker than the registered
7B, 30 examples is a small sample, and these are base-model scores with no adapter, where
the study compares adapted models. This is an early warning, not a result.

### Structurally ill-posed examples: 27% of the corpus (2026-09-14)

Independent of the metric question, and actionable now. Inspecting predictions against
references showed several examples no model could answer, so the whole corpus was profiled:

| | share |
|---|---|
| `after` is empty, a pure deletion | **21.5%** |
| `before` is empty, a pure insertion | 4.4% |
| `after` more than 5x `before`, the hunk over-captured | 4.0% |
| `after` under 0.2x `before` | 1.5% |
| plausibly well-posed | **73.1%** |

The length ratio runs to a maximum of **1148x**: one case anchors on a single line and
pairs it against a thirty-line Ansible block.

Pure deletions are the large category and the clearest problem: the target is the empty
string, which a generative model cannot produce naturally and which exact match then scores
as a miss for a structural reason. Other cases are under-determined rather than hard, for
instance a comment asking why a copyright year changed where the correct year (2020) never
appears in the prompt.

These are the misaligned pairs arXiv:2607.25851 describes. Filtering them raised edit
similarity from 0.555 to 0.663, so the model does produce closer output on well-posed
input, though exact match stayed at zero for the base-model reason above.

### 7B throughput measured on a GH200 (2026-09-14, job 143201)

| | |
|---|---|
| load | 27.0s |
| weights | 15.23 GB (peak 15.28 on a 102 GB card) |
| warmup, Triton JIT | 7.6s, once per process |
| **steady throughput** | **38-43 tok/s** at batch of one |

The first probe reported 1.0 tok/s and that was an artefact: it generated exactly once, so
the entire Triton JIT compilation landed inside the only timed call. Separating warmup from
steady state moved the number by a factor of forty. Quoting the first figure would have
over-sized every allocation by the same factor.

**Grid sizing**, at 64 output tokens per example, 14 evaluations, 6 training runs at
roughly three times inference cost for two epochs:

| test window | evaluations | trainings | total |
|---|---|---|---|
| 250 | 1.8h | 4.3h | **6.0h** |
| 500 | 3.4h | 8.5h | **11.9h** |
| 1,000 | 6.7h | 16.9h | **23.6h** |
| 2,000 | 13.2h | 33.7h | 47.0h |

The current `job_for_grid` default of 12 hours therefore covers a test window of roughly
500, which is in range for the measured yield of ~733 examples per organization-month. It
is not comfortable, and a longer window needs either a longer wall clock or batching.

**Batching is the obvious lever and is not yet used.** Peak memory was 15.28 GB on a 102 GB
card, so about six copies of the model would fit; batch-of-one is simply what the benchmark
measured. A batched evaluation loop should cut the evaluation half substantially. Worth
doing before the confirmatory run, not before the pilot.

### The 7B base also floors at exact match 0 (2026-09-14, job 143304)

The first pilot ran the base evaluation before dying on an API change, which is enough to
settle one question:

| model | EM | normEM | edit similarity |
|---|---|---|---|
| Qwen2.5-Coder-1.5B, base | 0.000 | 0.020 | 0.555 |
| **Qwen2.5-Coder-7B, base** | **0.000** | **0.000** | **0.157** |

So the exact-match floor is a property of the task against a base model, not an artefact of
using a small proxy. That was the open question from the earlier proxy measurement and it
is now answered for the registered model.

The 7B's edit similarity is **lower** than the 1.5B's, 0.157 against 0.555, which is the
opposite of what capability alone predicts. *Resolved 2026-09-15:* both models were scored on
the raw prompt, which is out of distribution for an instruction-tuned checkpoint. Under the
chat template the 7B's edit similarity is 0.754. See the withdrawn verbosity claim below.

LoRA attaches as expected: 80,740,352 trainable of 7,696,356,864, 1.05%.

**None of this bears on the study's design**, which compares *adapted* models. It does mean
the adapted number is the one that matters, and the pilot exists to produce it.

### transformers 5 removed `warmup_ratio`

`TrainingArguments` keeps only `warmup_steps`. The declared budget keeps the ratio, because
it is scale-invariant and pinning steps would mean a change in corpus size silently changed
the warmup fraction on a pre-registered parameter. `training.warmup_steps` derives the step
count at construction.

### The NaN was one pair of examples, not the framework (2026-09-14)

Seven diagnostic jobs, roughly two minutes each. Worth recording because the first
conclusion was wrong and the real one is unintuitive.

**The finding.** Two examples from the same change, both version bumps in
`.pre-commit-config.yaml` differing only in digits, produce a NaN gradient **only when
batched together**:

```
'    rev: v4.5.0' -> '    rev: v5.0.0'   comment: "fyi, there's a v5.0.0 of this out now"
'    rev: 6.1.0'  -> '    rev: 7.0.0'    comment: "and 7.0.0 of this"
```

| condition | grad_norm |
|---|---|
| the pair, five repeats | NaN every time, loss identical to four decimals |
| item 160 alone | 5.06 |
| item 161 alone | 4.03 |
| reversed order | NaN |
| either one paired with an unrelated item | fine |

Forward is finite (loss 1.374); the backward is what breaks. Highly correlated gradients
through a low-rank factorisation is a plausible mechanism, but the fix does not depend on
establishing it.

**The wrong turn, recorded because it was instructive.** After four diagnostics I concluded
`transformers.Trainer` was at fault and rewrote the training loop. The rewritten loop then
failed identically. The error: my diagnostic ran **10 optimiser steps** and the failure
occurs at **step 10**, so "stable for 10 steps" was read as stable. A test that stops short
of the failure cannot exonerate anything.

**What was eliminated on the way**, each by measurement: pathological sequence lengths
(median 80, max 678), items with no supervised tokens (zero of 201), the adapter being left
in bf16 (392 tensors, already fp32), padding at batch 2, bf16 autocast, gradient
accumulation, weight growth (norms moved 1.3% and 0.2% in two configs that both broke at
the same step), and the learning rate (4x apart, identical failure point).

That last pair is what made it obvious: two configurations with very different loss
trajectories breaking at **exactly** step 10 is a data-position signature, not an
optimisation one.

**The fix.** `micro_batch` drops to 1 with accumulation raised to 16, keeping the effective
batch at 16 while removing padding and pairing as variables entirely. Throughput is not the
constraint here; the pilot runs in eight minutes against a four-hour budget. The loop also
now skips and counts a non-finite step rather than aborting, because silently stepping on a
NaN gradient is what produced an adapter emitting garbage at edit similarity 0.001.

**Worth revisiting.** Dedup did not catch these two as near-duplicates. They are the same
edit shape on the same file, and shingle Jaccard over the before/after text falls below the
0.8 threshold because the digits differ. Whether the corpus should treat "same file, same
edit shape" as duplication is a sampling question for the Stage 1 report, not a bug.

### PILOT RESULT: the exact-match pass rule survives contact with the corpus (2026-09-14)

Job 143496. Qwen2.5-Coder-7B, 201 well-posed OpenStack examples, split **by change**:
91 changes to 72 train / 19 eval, 156 / 45 examples, zero leakage verified.

| | base | adapted |
|---|---|---|
| **exact match** | **0.000** | **0.200** |
| normalized exact match | 0.022 | 0.222 |
| edit similarity | 0.184 | 0.822 |
| median prediction length | 344 chars | 52 (reference 63) |

Training: loss 1.58 to 0.40 over 18 optimiser steps, no skipped steps.

**What this settles.** The open question since the first proxy measurement was whether
exact match can discriminate at all on this corpus, given a base model floors at zero. It
can. Adaptation lifts it from 0.000 to 0.200 on data the adapter has not seen, which is in
the same band as published fine-tuned figures for this task (CodeReviewer 30.32%, CodeT5
24.41%, T5 15.08%) and unsurprising on the low side given 156 training examples against
their corpus. **The pre-registered pass rule does not need rethinking before 2026-11-20.**

**Withdrawn: "adaptation fixes the verbosity".** This section originally credited the
adapter with cutting answers from 344 to 52 characters and called it evidence of learning
conventions of form. It was the prompt format. The pilot scored both arms on the raw
prompt, which is out of distribution for an instruction-tuned model; the adapter had been
trained on that raw format and the base model had not. Job 143892, base model only, same
45 examples:

| base model | exact match | normalized EM | edit similarity | median answer |
|---|---|---|---|---|
| raw prompt | 0.000 | 0.022 | 0.193 | 344 chars |
| chat template | 0.022 | 0.200 | 0.754 | 64 chars (reference 63) |

Two consequences. The base arm's edit similarity and normalized EM were deflated by the
format, so every ADAPTED - BASE figure above except exact match is confounded. And under
its own template the base model already solves 20% of edits up to whitespace while scoring
2% exact, so the adapter's exact-match gain is largely **whitespace and indentation
fidelity**. That cancels in the RQ1 contrast, where both arms are adapters, and inflates
the positive control, where one is not.

**A leaked result was nearly reported.** The first run with a naive example-level shuffle
gave adapted EM **0.512**. 63% of its eval examples shared a change with training data and
20% shared an exact before text. `sphragis.corpus.split` groups by change precisely to stop
this, and the experiment script bypassed the guard rather than reusing it. Grouping halves
the number, and 0.200 is the one to quote.

**Caveats, stated because this number will be quoted.** 45 eval examples over 19 changes.
*Superseded 2026-09-15:* the paired cluster bootstrap on this run is +0.200 [+0.086,
+0.419], but the run itself is not the one to quote: it skipped dedup, scored a
format-confounded base arm, and trained under a loop that never reached one example. The
rerun under corrected conditions replaces it. One month, one organization, one seed. And this is
*not* the RQ1 comparison, which contrasts an adapter trained on one organization against
one trained on another, evaluated on the first. This measures only that the metric has room
to move.

### PILOT RERUN: the number to quote (2026-09-15)

Job 143893, same registered model and budget, with every confound the review found removed:
deduplicated first (174 of 201 kept), prompt through `render_chat` for training and both
arms, every training example seen exactly twice, LoRA init seeded before attachment.
Change-grouped split: 88 changes to 70 train / 18 held out, 147 / 27 examples, **0 of 27**
held-out examples repeating a training pair.

| | base (chat template) | adapted |
|---|---|---|
| **exact match** | **0.074** | **0.407** |
| normalized exact match | 0.259 | 0.407 |
| edit similarity | 0.745 | 0.834 |
| median answer | 81 chars | 66 (reference 70) |

Training: 20 of 20 optimiser steps applied, 294 exposures (147 x 2), loss 0.85 to 0.30.

| contrast, 95% pairs cluster bootstrap over 18 changes | estimate | interval |
|---|---|---|
| exact match, pooled over examples | +0.333 | [+0.133, +0.565] |
| exact match, averaged over changes | +0.333 | [+0.139, +0.556] |
| normalized exact match, pooled | +0.148 | [+0.036, +0.292] |

Adapted hits: 11 of 27, spread over 9 of 18 changes. Base hits: 2.

**What it says.** Exact match moves, and by more than the first run reported, because the
first run's held-out set was padded with duplicate misses. Roughly half the exact-match gain
is whitespace fidelity: the base model is 0.185 below its own normalized score and the
adapter closes that gap entirely. The other half survives normalization, +0.148 with an
interval above zero, so the adapter is also getting more edits *right*, not only formatted.
For RQ1 the whitespace half cancels, since both arms are adapters.

**Caveats.** 27 held-out examples; the interval is wide and the false-positive rate of
this bootstrap at 18 changes is about 6% two-sided. One month, one organization, one seed.
Built before the acknowledgement filter, so 4 examples carrying only "Done"-type comments
are still in it. Still not the RQ1 contrast, which needs the Qt corpus.

**Decoding ceiling.** Both arms generated at most 96 new tokens. 4 of 172 deduplicated
references are longer (99, 114, 143 and 154 tokens), and 1 of the 27 held-out examples
was among them, so it was a miss for both arms by construction. It cannot favour either
arm and moves both exact-match rates by at most 1/27. The adapted model's longest answer
was 54 tokens, so nothing it did produce was cut. `MAX_NEW_TOKENS` is now 256 for every
generator.

### Contamination battery: what the time partition actually rests on (2026-09-15)

`research(2026-09)`. The design says the corpus "starts 2024-10-01 against a base model
published 2024-09-17" and calls the time partition the weakest of the three methods
because it rests on a publication date. It rests on less than that.

**What the Qwen2.5-Coder technical report states** (arXiv 2409.12186, section 3.1.1), the
only date in it: "We collected public repositories from GitHub created before February
2024". That bounds when a *repository was created*, not when its contents were captured,
so a repository created in 2015 and crawled later can carry code from any date. Pull
requests, commits, Jupyter notebooks, Kaggle data and the Common Crawl text-code data are
listed with no date at all. Decontamination (section 5) removed HumanEval, MBPP, GSM8K and
MATH by 10-gram overlap, which says nothing about Gerrit review data.

**No official knowledge cutoff exists.** A Qwen GitHub discussion (QwenLM/Qwen3 #1093)
carries community claims of June 2024 and March 2024, with neither confirmed by a
maintainer. Neither can be cited as a cutoff.

**Consequences for the design.**

- The time partition is an argument that the corpus postdates every date the developer
  has stated, not evidence that it postdates pretraining. That makes Min-K% and guided
  completion the measurements, and a flat post-versus-pre gap an ambiguous result: either
  no exposure, or an instrument that cannot see exposure. The report has to pre-commit to
  reading it that way.
- **Control month: 2024-01.** The last full month before the report's February 2024
  repository bound, and earlier than both unofficial cutoff claims, from the same projects.
- **Min-K% runs on the base checkpoint `Qwen/Qwen2.5-Coder-7B`, not the Instruct model.**
  Samuel, Zhou and Zou (COLING 2025, "Towards Data Contamination Detection for Modern
  Large Language Models") find detection methods mutually inconsistent on current models
  and degraded by instruction fine-tuning. The Instruct model is a fine-tune of the base
  (same 7,615,616,512 parameters), so the base is the checkpoint whose likelihoods reflect
  pretraining exposure. Guided completion stays on the registered Instruct model, since it
  asks what the evaluated model reproduces.

### Contamination battery, first run: it executes, and it is underpowered (2026-09-15)

Job 143898. OpenStack 2024-10 against the 2024-01 control, both deduplicated. Min-K%++ and
Min-K% on the base `Qwen/Qwen2.5-Coder-7B`; guided completion on the registered Instruct
model. `datasets/results/contamination-openstack-after.json`.

| | post-cutoff | pre-cutoff control | gap | 95% bootstrap, by change |
|---|---|---|---|---|
| Min-K%++, k = 20 | -1.680 | -1.900 | +0.221 | [-0.052, +0.524] |
| Min-K%, k = 20 | -7.655 | -7.810 | +0.155 | [-0.635, +0.930] |
| guided completion | 0 of 33 | 0 of 25 | 0 | at the floor |

Scored: 35 post-cutoff examples over 22 changes and 28 control examples over 18, the ones
reaching the 32-token minimum (median 51 tokens in both windows). No position had an
undefined Min-K%++ score.

**Reading, as pre-committed.** Neither membership interval excludes zero, and guided
completion reproduced no hunk in either window. This is a battery that runs end to end and
cannot yet separate the windows. It is not evidence of no exposure. The point estimates lean
the unexpected way (post-cutoff scored as slightly *more* familiar), which at this sample is
indistinguishable from content differences between the months.

**What it changes for the confirmatory design.**

- **Sample.** The 32-token minimum keeps about 20% of a deduplicated month (35 of 172, 28 of
  133), because hunks are short. The control has to be a multi-month window, sized like the
  post-cutoff window it is compared against, not a single month.
- **Scored text.** Scoring the hunk with its surrounding context instead of the bare `after`
  lines would bring most examples over the minimum. `Hunk` already carries
  `context_before` and `context_after`; `build` does not emit them yet. Adding them changes
  the example schema, so it is a Stage 1 decision.
- **Guided completion at the floor** in both windows means it has no discriminating power on
  this corpus as prompted. Either report it as a null instrument, or register a softer match
  (normalized edit similarity against the reference) before seeing the confirmatory data.

### Contamination battery on hunks with context: enough sample, still flat (2026-09-15)

Job 143956, same windows and checkpoints as 143898, scoring each hunk with its three context
lines either side. `datasets/results/contamination-openstack-with_context.json`.

| | examples, post / pre | changes | gap | 95% bootstrap, by change |
|---|---|---|---|---|
| Min-K%++, bare hunks (143898) | 35 / 28 | 22 / 18 | +0.221 | [-0.052, +0.524] |
| **Min-K%++, with context** | **161 / 116** | **85 / 57** | **-0.058** | **[-0.200, +0.084]** |
| Min-K%, with context | 161 / 116 | 85 / 57 | -0.216 | [-0.661, +0.225] |
| guided completion, edit similarity | 55 / 46 | | -0.007 | [-0.118, +0.085] |

Guided completion reproduced 0 of 55 post-cutoff hunks and 1 of 46 control hunks verbatim;
mean edit similarity 0.380 against 0.387.

**Reading.** Context lifted the scored sample from about 20% of each deduplicated month to
94% (161 of 172) and 87% (116 of 133), and halved the Min-K%++ interval. The small-sample
lean toward "post-cutoff looks more familiar" is gone. No method separates October 2024 from
January 2024. As pre-committed, that is ambiguous: no exposure to this Gerrit data, or
probes that cannot see it. It is not a clean bill.

**For Stage 1.** Score hunks with context; bare hunks leave too few examples to read. Guided
completion, verbatim or by edit similarity, shows no discrimination on this corpus and should
be reported as a null instrument or replaced, decided before confirmatory data exists.

### RQ1 PILOT: the grid runs end to end, and the design confounds organization with volume (2026-09-15)

Job 143899, `scripts/rq1_pilot.py`, 16m44s on one GH200. One month per organization
(2024-10), deduplicated, held out by change; one seed; both arms under the chat template.
`datasets/results/rq1-pilot.json`.

| exact match | held out: OpenStack (27 ex, 18 changes) | held out: Qt (133 ex, 48 changes) |
|---|---|---|
| base | 0.074 | 0.038 |
| adapter trained on OpenStack (145 examples) | **0.370** | 0.271 |
| adapter trained on Qt (422 examples) | 0.407 | **0.316** |

| matched minus mismatched | pooled over examples | averaged over changes |
|---|---|---|
| OpenStack | -0.037 [-0.152, +0.087] | -0.039 [-0.178, +0.072] |
| Qt | +0.045 [**+0.000**, +0.099] | +0.008 [-0.073, +0.082] |

Pilot-scale gate: **fail** (neither lower bound strictly above zero). Not the RQ1 answer: one
month, one seed, 18 held-out OpenStack changes. Normalized exact match tells the same story
(Qt +0.038 [-0.009, +0.097]).

**Three findings the design has to absorb before Stage 1.**

1. **Organization is confounded with training-set size.** The Qt adapter trained on 2.9x the
   examples and beat the matched adapter on OpenStack's own data. Performance grows roughly
   logarithmically with training data (arXiv 1712.04008), so the unmatched contrast partly
   measures volume. Fixed in the driver: `--equalize-train` subsamples every organization to
   the smallest one's size, seeded. Registering equal-size training is a Stage 1 decision.
2. **The estimand changes the Qt result.** Pooled +0.045, change-averaged +0.008. This is the
   divergence flagged earlier, now on real data: Qt's held-out set has changes with many hunks.
3. **The pass rule's boundary is reachable.** Qt's lower bound is exactly 0.0. Exact match is
   binary, the bootstrap distribution is discrete, and a bound landing on zero is not rare.
   The rule reads strictly greater than zero; the report must say so.

*Withdrawn: "the Qt adapter's loss of 0.069 is consistent with memorization".* That figure is
the loss of the final optimizer step, which holds only the examples left over after full
batches of 16: 422 leaves 6, and OpenStack's 145 leaves 1. The equalized rerun trained the same
145 OpenStack examples with the same seed and recorded 0.010 where this run recorded 0.578,
which is what a one-example sample looks like. The last step's loss says nothing about fit.
The driver now reports the mean over the final epoch. A one-example final step also takes a
full learning-rate update on a single example's gradient; that matches standard trainers
without drop-last and is noted rather than changed.

### RQ1 pilot, equalized: both contrasts change sign (2026-09-15)

Job 143957, 12m41s. Identical to 143899 except both adapters train on 145 examples (Qt
subsampled from 422, seeded). `datasets/results/rq1-pilot-equalized.json`.

| exact match, one seed | held out: OpenStack (27 ex, 18 changes) | held out: Qt (133 ex, 48 changes) |
|---|---|---|
| base | 0.074 | 0.038 |
| adapter trained on OpenStack (145) | **0.407** | 0.293 |
| adapter trained on Qt (145 of 422) | 0.370 | **0.256** |

| matched minus mismatched | unequal sizes (143899) | equalized, pooled | equalized, change-averaged |
|---|---|---|---|
| OpenStack | -0.037 [-0.152, +0.087] | +0.037 [-0.069, +0.182] | +0.072 [-0.022, +0.200] |
| Qt | +0.045 [+0.000, +0.099] | -0.038 [-0.089, +0.009] | -0.069 [-0.149, -0.004] |

Pilot-scale gate: fail, again. Normalized exact match agrees in direction.

**Reading.**

- **Training volume outweighs organization at this scale.** Cutting the Qt adapter from 422 to
  145 examples lowered its score on Qt's own held-out data from 0.316 to 0.256, a larger move
  than either contrast. Equal-size training (registered) is necessary, not a refinement.
- **Run-to-run variation is as large as the effect.** The OpenStack adapter trained on the same
  145 examples with the same seed in a different order moved from 0.370 to 0.407 on OpenStack,
  exactly one of 27 examples. Every contrast above sits inside that range, and both changed
  sign between runs.
- **No evidence of an organization-specific gain at pilot scale**, and none against it: one
  seed, one month, 18 OpenStack changes. The Qt change-averaged interval excluding zero on the
  wrong side is one draw at one seed, which is the case three registered seeds exist for.
- **What it means for Stage 1:** the power analysis must be built on these variances, at the
  test window's size, with seed-to-seed variation inside the simulation rather than assumed
  away. That analysis is running.

### Power: the test window could detect about 3 exact-match points (2026-09-15)

`scripts/power_rq1.py datasets/results/rq1-pilot-equalized.json --size openstack=880 --size qt=2400`,
on the equalized RQ1 pilot's per-change outcomes.

| smallest matched-minus-mismatched gain detected at 80% power | pilot size | 200 changes | test window (estimated) |
|---|---|---|---|
| OpenStack (18 pilot changes) | +0.321 | +0.081 | **+0.030** at 880 |
| Qt (48 pilot changes) | +0.126 | +0.047 | **+0.012** at 2,400 |

**Found and fixed on the way (`ff46b58`).** The simulation resampled the pilot as observed, so
the pilot's own difference acted as part of the null: OpenStack's observed +0.037 gave +0.0067
at 880 changes, a 29-fold drop from 18 where sampling noise predicts about 7-fold. It now
swaps arms per change at random before adding the effect. Tripped against the old code: a
pilot that always favours treatment had power 1.0 at zero added effect; the fix gives 0.0.

**Report-grade rerun: the estimate holds.** 400 trials (power's standard error about 2
points), 1,000 resamples, lift tolerance 0.005, simulation seeds 11, 12 and 13, about 57
minutes each. `datasets/results/power-rq1-report-grade.txt`.

| detectable gain at 80% power | median of 3 seeds | range |
|---|---|---|
| OpenStack, 18 changes | +0.287 | +0.276 to +0.314 |
| OpenStack, 200 changes | +0.067 | +0.064 to +0.071 |
| **OpenStack, 880 changes** | **+0.030** | **+0.028 to +0.031** |
| Qt, 48 changes | +0.115 | +0.109 to +0.116 |
| Qt, 200 changes | +0.046 | +0.045 to +0.047 |
| **Qt, 2,400 changes** | **+0.011** | **+0.011 to +0.012** |

Every lift now sits above the bisection's resolution, including Qt's at 2,400 (0.016 against
a 0.005 step), which the first run could not resolve.

**How far to trust these.**

- The first run's settings (100 trials, 100 resamples, tolerance 0.02, one seed) are
  superseded by the table above; its figures agreed within their stated imprecision.
- The window sizes are estimates: about 88 OpenStack and 240 Qt changes a month from 2024-10,
  over the ten-month test window.
- The gate needs both organizations, so **OpenStack binds: about 3 exact-match points.** Both
  single-seed pilot contrasts were about 4 points in magnitude and changed sign between runs,
  so an effect of that size sits near what the window can see. That bounds variance, not the
  effect.

### Outcome-neutral checks, run on the equalized pilot's real data (2026-09-15)

`sphragis/experiment/neutral.py`, applied locally to `rq1-pilot-equalized.json` with the
pilot's split recomputed (dedup, change-grouped holdout seed 0, equalized training seed 0).
The recomputed held-out sets are identical to the ones the pilot scored (27 and 133 ids).

| check | OpenStack | Qt |
|---|---|---|
| 2, positive control (own adapter against base, one-sided) | pass, +0.333 [+0.133, +0.565] | pass, +0.218 [+0.120, +0.337] |
| 5, non-degeneracy (no condition at 0 or 1) | pass | pass |
| 4, near-duplicates train against held-out, Jaccard >= 0.8 | 0 of 27 | 0 of 133 |
| same, Jaccard >= 0.7 / >= 0.5 | 1 of 27 / 1 of 27 | 0 / 0 |

Test 3, the manipulation check, cannot be computed from this run: it did not save per-step
losses or adapter norms. The next TIGRIS run of `rq1_pilot.py` records both.

**Reading test 4.** A zero rate is expected here and says little: both sides come from one
month, and dedup already removed pairs at Jaccard >= 0.8 within it. The number the Stage 1
threshold should rest on is the rate **across windows**, train against dev, which the
collection now running makes measurable. The test window stays sealed.

### The seal right-censors the collected windows: dev loses about a fifth (2026-09-15)

Snapshots select merged changes by **last update** (Gerrit `after:`/`before:`), windows assign
them by **creation**, and the seal refuses every month from 2025-11 on. So a change created in
a collected window whose last update falls on or after 2025-11-01 is in no snapshot. The loss
is concentrated in the changes reviewed longest, near the boundary.

Estimated from OpenStack's collected snapshots (25,191 merged changes, 2024-10 to 2025-10).
Creation-to-last-update lag, from changes created 2024-10 to 2024-12 (ten or more months of
follow-up, so lags under nine months are fully observed): median 3.8 days, p90 71.8, p99
272.3; P(lag > 7 d) 0.391, > 14 d 0.291, > 30 d 0.196, > 61 d 0.116, > 90 d 0.077, > 180 d
0.027. Expected share of each creation window's true cohort missing:

| window | collected changes created there | estimated share missing |
|---|---|---|
| dev, 2025-09 to 10 | 4,104 | **21.6%** |
| train, 2025-08 | 1,820 | 9.5% |
| train, 2025-06 to 07 | 3,452 | 5.4% |
| train, 2024-11 to 2025-05 | 13,452 | 1.4% |

Approximations: creation days are taken from the collected, already truncated changes, and
last update includes post-merge comments; both probably understate the loss.

**Qt, same method** (52,895 merged changes; early cohort 11,438): lag median 0.4 days, p90
15.5, p99 128.0; P(lag > 7 d) 0.172, > 30 d 0.058, > 90 d 0.018, > 180 d 0.006.

| share of each window's true cohort missing | OpenStack | Qt |
|---|---|---|
| dev, 2025-09 to 10 | 21.6% | 7.9% |
| train, 2025-08 | 9.5% | 2.2% |
| train, 2025-06 to 07 | 5.4% | 1.1% |
| train, 2024-11 to 2025-05 | 1.4% | 0.2% |

**The censoring is unequal across organizations.** Qt reviews close far faster, so the seal
removes almost three times as much of OpenStack's dev window. RQ1 compares organizations, so
under creation-based windows this is a confound rather than shared noise: the two dev windows
are drawn from differently truncated populations. It strengthens the case for assigning
windows by last-update month, where neither organization is censored.

**Why it matters.** The missing changes are the slow, long-reviewed ones, which plausibly
carry different review comments. The dev window, and the late train months, are biased toward
quick reviews.

**Options, a Stage 1 decision (not changed in code):**

1. Assign windows by last-update month rather than creation. Collection and assignment align
   and nothing is censored; changes stay disjoint, but review activity near a boundary overlaps.
2. A gap between dev and test that excludes changes created in the final months before the
   seal, the standard embargo for temporal splits; shortens a window and so power.
3. Keep creation windows, report the bias, and register a follow-up horizon for the test
   window: after acceptance, fetch through its end plus about six months (under 3% missing).

### Collection: our own volume got connections to opendev dropped (2026-09-15)

Building the train and dev windows stalled twice against review.opendev.org. DNS was healthy
throughout (16 timed lookups, none failed). Failed requests completed the lookup and never
completed the TCP handshake, including with the address pinned. After hours of unpaced
building, at about 20 requests a second, a quarter to a third of new connections were dropped.
After 20 minutes of sending nothing, 0 of 30 were. Qt's server showed no such pattern.

Three changes to the transport, in order of discovery: one persistent connection per host
with a 15-second timeout; a failed comments fetch dropping one change as `comment_error`
instead of aborting the build (the diff fetch already had that guard); and a minimum interval
between requests to one host, `--request-interval`, default 0.2 seconds. Changes lost to
`comment_error` are counted in each month's drop profile, so collection loss stays auditable.

**It is both servers, and 0.2 s is not enough.** Qt's server did the same thing after about
three hours of building: the process sat in `SYN-SENT` and an independent curl from the same
box also timed out, which is the opendev signature exactly. So this is not one operator's
policy but what a community Gerrit does to a client that sustains requests for hours, and the
0.2-second interval (5 requests a second) only postponed it.

The build was stopped rather than left to run. Its retries would have exhausted and dropped
changes as `comment_error`, which is counted and therefore auditable, but a final month
carrying hundreds of such drops would differ materially from the other twelve, and that month
feeds the dev window that censoring has already thinned. Twelve of thirteen months were
already written, so the cost was one month's fetching.

**Qt's block outlasts the traffic; opendev's did not.** After 25 minutes of sending nothing,
10 of 12 probes to Qt still failed, where opendev had recovered fully (0 of 30) after 20.
It is Qt-specific rather than local: in the same minute opendev, GitHub and Google answered
5 of 5 while Qt answered 1 of 5. That shape, a refusal that persists after the load stops,
reads as a firewall ban rather than load shedding, so waiting is the only remedy and the
interval that avoids it has to be well below the 5 requests a second that triggered it.

**What it costs, which is less than it looks.** Qt's twelve built months are 2024-10 through
2025-09, so the *pilot* window (2024-10) and the whole *train* window (2024-11 to 2025-08)
are complete. Only 2025-10 is missing, which is the second of the dev window's two months.
Training is therefore unaffected for both organizations; Qt's dev window is half size, which
widens its intervals and is stated wherever its numbers appear.

**Measuring how the sampling window fooled me.** Reading the build's bytes over 5 seconds
showed zero and looked like a stall; over 60 seconds it read 2.6 MB and was healthy. A paced,
bursty client needs a sampling window longer than its retry backoff (1+2+4+8+16 = 31 s) before
"no progress" means anything. Both readings were reported before the longer one corrected them.

### OpenStack corpus complete, and the leakage threshold has evidence (2026-09-15)

Thirteen months, 2024-10 to 2025-10, collected and built in 3h32m.
`datasets/results/window-report-openstack.json`.

**5,959 examples, 5,498 after dedup** (265 exact, 196 near-duplicate, 0 boilerplate). Drops
over ~25,000 merged changes: 37,918 metadata-only files, 16,547 author comments, 8,197 with
no anchored hunk, 2,378 with no successor revision, 1,995 ill-posed, 1,885 acknowledgements,
73 with no line anchor, 13 diff errors, 8 comment errors.

| window | examples | changes |
|---|---|---|
| pilot (2024-10) | 606 | 194 |
| train (2024-11 to 2025-08) | 4,327 | 1,737 |
| dev (2025-09 to 2025-10) | 565 | 235 |

**Cross-window near-duplicate rate**, the evidence outcome-neutral test 4's threshold should
rest on:

| | J>=0.8 | J>=0.7 | J>=0.6 | J>=0.5 |
|---|---|---|---|---|
| pilot into train | 0.0000 | 0.0025 (11) | 0.0055 (24) | 0.0072 (31) |
| train into dev | 0.0000 | **0.0106 (6)** | 0.0142 (8) | 0.0177 (10) |

**Reading.** The 0.8 column is zero by construction: dedup removes pairs at that threshold
across windows as well as within them, so registering the threshold there makes a test that
cannot fail. Train into dev at J>=0.7 is 1.06%, the closest available analogue of the
train-into-test rate the report must bound. A registered threshold at a looser similarity,
informed by that 1.06%, is a test with something to detect; at 0.8 it is a tautology.

**Two side notes.**

- The dev window is small: 565 examples over 235 changes, and censored (an estimated 21.6%
  of its true cohort is missing). Scaled to the test window's ten months, 235 changes per two
  months implies roughly 1,175, or about 1,500 uncensored. The power analysis assumed 880, so
  its estimate of about 3 detectable exact-match points is conservative rather than optimistic.
- Both guards added during the run fired in production: `comment_error` 8 times and
  `diff_error` 13, in 2025-08 and 2025-09. Before the comments guard existed, the first of
  those would have aborted the build at month 11 of 13.

### Both corpora windowed, and the collection etiquette that has to change (2026-09-15)

`datasets/results/window-report-{openstack,qt}.json`.

| | examples | after dedup | pilot | train | dev |
|---|---|---|---|---|---|
| OpenStack (13 months) | 5,959 | 5,498 | 606 / 194 ch | 4,327 / 1,737 ch | 565 / 235 ch |
| Qt (12 months) | 10,622 | 9,918 | 1,300 / 403 ch | 8,254 / 3,160 ch | 364 / 175 ch |

Both train windows are complete; Qt's dev holds 2025-09 only. Both dev windows clear the
bootstrap's ten-change floor. Equalized training is 4,327 examples per organization.

Cross-window near-duplicate rate, Qt: pilot into train 0.0000 / 0.0005 / 0.0011 / 0.0028 and
train into dev 0.0000 / 0.0055 / 0.0082 / 0.0302 at Jaccard 0.8 / 0.7 / 0.6 / 0.5. OpenStack's
train into dev is 0.0000 / 0.0106 / 0.0142 / 0.0177. A threshold registered at 0.7 must sit
above roughly 1.1%, the larger of the two, to be a bound both organizations can meet.

**Collection etiquette, which the transport got wrong twice.** One comments request per change
plus one diff request per surviving comment is roughly 5,000 to 6,000 requests per month of
Qt data, about 70,000 for thirteen months, issued at the 5 requests a second the 0.2-second
interval allows. Both servers tolerated that for about three hours and then stopped completing
handshakes. The first incident was read as transient and answered with pacing tuned to make
the symptom go away; the correct reading was that the rate itself was wrong for a
volunteer-run Gerrit. Future collection runs at about 1 request a second and stops rather than
retrying when handshakes begin to fail, since the frozen corpus is fetched once and reused.

### What the base model's own report says about its training data (2026-09-15)

The contamination battery compares a post-cutoff window against a pre-cutoff control, so the
cutoff is load-bearing and had never been sourced. Search returns "June 2024" for
Qwen2.5-Coder with some confidence. It does not survive the primary source: in
[QwenLM/Qwen3 discussion 1093](https://github.com/QwenLM/Qwen3/discussions/1093) a user
*asks* whether the cutoff is June 2024, a maintainer pings two team members, neither answers,
and a third participant offers June as a personal opinion. No Qwen maintainer states a date.
It is a community guess with a citation-shaped shadow, and the battery must not rest on it.

The technical report does make a sourceable statement. Qwen2.5-Coder
([arXiv:2409.12186](https://arxiv.org/abs/2409.12186), 3.1.1 Data Composition):

> Source Code We collected public repositories from GitHub created before February 2024,
> spanning 92 programming languages. ... In addition to raw code, we also collected data from
> Pull Requests, Commits, Jupyter Notebooks, and Kaggle datasets

Three things follow, and two of them cut against the study.

- **That is a repository-creation filter, not a content cutoff.** A repository created in 2011
  and crawled in mid-2024 carries 2024 content. OpenStack and Qt are both far older than the
  filter, so it bounds nothing about how recent their content may be. The report gives no
  content cutoff, and the honest registered statement is that it is unstated.
- **Pull requests and commits are named as sources.** The refinement targets are post-comment
  code, which is exactly what a commit contains, so the *targets* are plausibly exposed even
  though the review comments are not.
- **Gerrit is not GitHub.** OpenStack reviews live on review.opendev.org and Qt's on
  codereview.qt-project.org. The inline review comments that make up the prompt side of every
  example are not on GitHub in any form. Only the code side has a plausible route into
  pretraining.

**What this does not change.** The post-cutoff window starts 2024-10-01 and the checkpoint was
published 2024-09-17, so the post arm is clean by construction rather than by the report's
word: that data did not exist when the model shipped. Only the control depends on the cutoff,
and it depends on it in the safe direction, since a control that turns out *not* to be in
training weakens the contrast rather than manufacturing one.

**What it changes.** The control moves back to a six-month window ending 2024-01, which sits
comfortably inside any reading of the evidence, instead of the single month that ended one
month before the stated repository boundary.

### The temporal probe is weaker than the design assumed (2026-09-15)

Zhang et al., *Test of Time: Rethinking Temporal Signal of Benchmark Contamination*
([arXiv:2509.00072](https://arxiv.org/abs/2509.00072), ACL 2026), argue that post-cutoff
performance decay, the signal the time-partition probe reads, is not dependable evidence of
contamination. Their result is that the *construction* of the items distorts the temporal
pattern independently of the source material: model-transformed questions and cloze questions
drawn from the very same documents produce markedly different temporal signals.

Their specific confound is controllable here and is now controlled. Every example in both
windows comes from one deterministic pipeline with one anchoring rule, and the control window
was re-collected as a single uniform window with the cutoff at its own first day so that it is
constructed identically to the window it is compared against, rather than as six months each
built as their own window. Construction is held fixed; only the date varies.

Their general point survives that fix. A time partition cannot separate exposure from ordinary
distribution shift, because a codebase's conventions, reviewers and subject matter all move
over eighteen months. So the registered reading changes:

- **Min-K%++ on the base checkpoint is the primary instrument.** It scores the text directly
  and does not infer membership from a performance difference across dates.
- **The time partition is corroborative only.** A gap in the expected direction supports the
  membership result; a gap on its own is not read as contamination, and its absence is not
  read as a clean bill. This was already the pre-committed reading of an ambiguous battery;
  it is now also the registered reason.
- **Guided completion was already at its floor** in both windows, verbatim and by edit
  similarity. Two of three probes are therefore doing little, which is worth stating in the
  report rather than presenting a battery of three.

### The censoring confound, after two bugs in the inputs (2026-09-15, corrected 2026-09-16)

`scripts/censoring.py`, `datasets/results/censoring.json`. Snapshots select changes by last
update; windows assign them by creation. A change created inside a window whose last update
falls after the final collected month is absent entirely, and what disappears is the slow
reviews. The loss cannot be counted, because a truncated change leaves no record; the lag
from creation to last update can be, for changes whose lag was short enough to be seen. That
is right truncation, and Lynden-Bell (1971) gives its nonparametric MLE.

**The estimator is right. The inputs were wrong twice, and the published figures with them.**
Review checked the estimator against simulated data from known distributions at n up to 4M,
including the case where the largest lag equals the largest horizon, and found a maximum
absolute error of 0.0008 scaling as 1/sqrt(N): Monte Carlo noise, no bias. Then it found both
things feeding it were broken.

- **A Gerrit Change-Id names a family, not a change.** It is shared across cherry-picks and
  relation chains, so restricting the sample by `change_id` admitted every sibling of an
  example-bearing change, including siblings carrying no example that settle faster. Qt's
  sample was inflated 69%, OpenStack's 18%. The key is now `(project, change_id, created)`.
- **The last collected month was a shared constant.** Qt's examples stop at 2025-09 because
  the ban blocked 2025-10, so every Qt horizon was one month too long, every risk set held a
  cohort-month that could not have produced an observation, and Qt's loss came out too small.
  It is now derived per organization from the months actually built.

| P(lag <= t months), example-bearing | t=0 | t=1 | t=2 | t=3 | t=6 |
|---|---|---|---|---|---|
| OpenStack | 0.465 | 0.752 | 0.839 | 0.895 | 0.959 |
| Qt | 0.592 | 0.840 | 0.914 | 0.946 | 0.978 |

| window | OpenStack missing | Qt missing | differential | previously reported |
|---|---|---|---|---|
| train | 5.0% | 4.2% | **0.83 pt** | 3.30 pt |
| dev | 39.1% | 70.4% | **31.26 pt** | 19.14 pt |
| test, fetched 2027-02 | 1.1% | 0.6% | **0.54 pt** | 0.74 pt |

Qt's dev figure is not all censoring: its window is 2025-09 and 2025-10, and 2025-10 was never
collected, so 70.4% is one absent month plus 41% of the other month's cohort. That is the
honest accounting for the corpus as it stands, and better than the earlier number, which
silently assumed a month that does not exist.

**The decision survives; the numbers that justified it did not.** The confound is still the
dev window abutting the collection boundary rather than creation assignment, and the test
window fetched at in-principle acceptance still carries 0.54 points of differential against
the dev window's 31.26, a factor of 58 rather than 26. Creation windows stand, no embargo gap,
no last-update assignment. The registered horizon remains: **the test window is fetched no
earlier than three months after its final month**, which holds the differential to 1.16 points.

**Three limitations, one of which is a live failure.**

- **OpenStack's fit is rejected; quasi-independence is not the reason.** Superseded by the
  entry below: the per-cohort rate comparison that first suggested a latency trend is biased
  by the truncation it is meant to detect, and the conditional Kendall tau rejects
  quasi-independence for neither organization. Calibrated against its own simulated null, the
  goodness-of-fit statistic still rejects OpenStack at p = 0.017 and clears Qt at p = 0.580,
  so OpenStack's fit is off for a reason that remains unidentified. A refit on recent cohorts
  bounds the effect at about seven points on its dev figure, in the direction that makes the
  window less censored, and leaves the test window's figure smaller still.
- **That check is not fully independent.** Where the largest lag equals the largest horizon,
  the estimator's top step is algebraically the reference cohort's own empirical CDF, so the
  two must agree at the top regardless. It is a sanity check on the middle of the curve.
- **The tail beyond the longest observable lag is unidentified.** F(max) = 1 is imposed, so
  every figure is a lower bound. At an assumed 2% tail the dev figures move to 40.4 and 71.0
  and the test window at acceptance to 3.1 and 2.5. The sensitivity used to be computed
  against a hardcoded ceiling of 1.0 above the support, which put back exactly the tail mass
  it had removed and understated itself by a quarter to a half; it now carries the fitted
  CDF's own supremum forward.

### The sealed window is about twice the size the power analysis assumed (2026-09-15, corrected 2026-09-16)

The power analysis took 880 OpenStack changes and 2,400 Qt for the test window, scaled from
the dev window, which the capture model says is the worst available base. The train window,
missing 5.0% and 4.2%, is the right one, which frees the dev window to be a held-out check on
the model rather than an input to it.

| | train changes | implied true rate | dev predicted | dev actual | miss |
|---|---|---|---|---|---|
| OpenStack | 1,737 over 10 months, captured 0.950 | 182.9/month | 223 | 235 | -5.3% |
| Qt | 3,160 over 10 months, captured 0.958 | 329.9/month | 195 | 175 | +11.6% |

**Both organizations now pass, and Qt's passing is what tells the story.** The first version
of this analysis predicted Qt's dev window at 527 against an actual 175 and printed the 201%
miss as informational text, then used the same model on the next line to project the sealed
window. The miss was not Qt being strange: it was the two input bugs in the capture model, a
change key that admitted cherry-pick siblings and a horizon one month too long. With those
fixed the same check lands at +11.6% with nothing else changed. A check that can only be read
is not a check, so it now refuses to project the sealed window when the miss exceeds 15%.

| test window projection | changes | power analysis assumed | ratio |
|---|---|---|---|
| OpenStack | ~1,809 | 880 | 2.06x |
| Qt | ~3,281 | 2,400 | 1.37x |

So the registered MDEs are conservative rather than optimistic. Two things the check cannot
separate: it tests the capture model and a constant arrival rate together, and the arrival
rate is visibly not constant (OpenStack's example-bearing changes per created month range 136
to 232). A pass is therefore evidence that the two errors are small or cancel, not that either
is zero.

### RQ1 on the study's own windows (2026-09-15, job 144345)

The first run using the train and dev windows rather than a random holdout from one month.
Both organizations, base plus both adapters on both dev windows, equalized training, one
seed. `datasets/results/rq1-windows.json`. Completed in 2:19:50 against a 4:30 request; all
seven outcome-neutral checks pass and the apparatus holds.

| arm | exact match | normalized EM | edit similarity |
|---|---|---|---|
| base, OpenStack dev | 0.048 | 0.209 | 0.687 |
| base, Qt dev | 0.033 | 0.187 | 0.717 |
| OpenStack adapter on OpenStack | 0.306 | 0.358 | 0.815 |
| Qt adapter on OpenStack | 0.285 | 0.335 | 0.813 |
| Qt adapter on Qt | 0.319 | 0.343 | 0.824 |
| OpenStack adapter on Qt | 0.308 | 0.332 | 0.819 |

| contrast, matched minus mismatched | pooled | change-averaged |
|---|---|---|
| OpenStack, 235 changes | +0.0212 [-0.0029, +0.0466] | +0.0338 [-0.0062, +0.0744] |
| Qt, 175 changes | +0.0110 [-0.0208, +0.0433] | +0.0094 [-0.0341, +0.0534] |

**Gate: fail, under both estimands.** Neither lower bound clears zero, and OpenStack's misses
by 0.003. Normalized exact match agrees throughout (+0.0230 and +0.0110 pooled), so this is
not an artifact of the strict metric.

**The first thing that is new.** Both contrasts are positive. The two earlier pilots had
opposite signs, and the signs swapped when training sets were equalized, which left
run-to-run variation as large as the effect. Here both organizations lean the way RQ1
predicts, on windows chosen by the design rather than by a random split.

**The second thing, which matters more.** Adaptation is enormous and organization-specificity
is tiny. Training moves exact match from 0.048 to 0.306 on OpenStack, roughly a sixfold gain
of 26 points. The organization-specific part of that is 2 points. An adapter trained on
OpenStack scores 0.308 on Qt's held-out refinements, against 0.319 for Qt's own adapter, and
actually edges its own organization's 0.306. Whatever the adapters learn is almost entirely
the task -- the form of a refinement, the conventions of a diff-shaped answer -- and almost
none of it is the organization. If a fingerprint exists it is about one thirteenth the size
of the adaptation effect it rides on.

**What this does to the power argument, and it is not what scaling suggested.** A square-root
extrapolation from the pilot's +0.030 at 880 changes gives about +0.021 at the test window's
projected size, which would have put the observed effect exactly at the detection threshold.
Running the simulation on these variances instead (`datasets/results/power-rq1-windows.txt`,
400 trials, 1,000 resamples, tolerance 0.005) says otherwise:

| | observed effect | MDE at projected test size | ratio |
|---|---|---|---|
| OpenStack, 1,804 changes | +0.0212 | **+0.0141** | 1.50 |
| Qt, 3,197 changes | +0.0110 | **+0.0133** | 0.83 |

**Qt binds, and the earlier analysis said OpenStack did.** That reversal is the finding. What
binds is not the smaller MDE but the smaller ratio of effect to MDE, and Qt's observed effect
sits *below* what its own sample can detect. The gate requires both organizations to clear
zero, so on these estimates RQ1 fails on Qt even if the effect is real in both. OpenStack, by
contrast, has about 50% headroom where the scaling argument predicted none.

Three things this does not license. The MDE and the observed effect come from the same run,
so "observed exceeds MDE" is a statement about this sample's variance structure, not a
guarantee about the test window. One seed underlies both. And the dev windows are censored
unequally, which is the direction that would most distort Qt's estimate relative to
OpenStack's.

The Stage 1 decision it forces is real, and belongs at registration rather than after: a pass
rule requiring both organizations is, on current estimates, a rule Qt cannot satisfy. Either
that conservatism is accepted and stated, or the rule is something else, and either way it is
fixed before the test window is opened.

**What this is not.** One seed, not the registered three. The dev windows, not the sealed test
window. And both dev windows are censored, OpenStack missing an estimated 37.0% of its
example-bearing cohort and Qt 17.9%, unequally and against the slow reviews; Qt's window is
also one month of two because of the ban, which is why it carries 175 changes against
OpenStack's 235 and has the wider interval despite Qt being the larger corpus. This is the
best available pre-registration estimate of RQ1. It is not the RQ1 answer.

**Training, for the record.** Both adapters ran 542 steps on equalized sets, 4,321 and 4,323
items after refusals, reaching final-epoch mean losses of 0.288 and 0.221 from first losses of
0.733 and 1.060. Adapter weight norms 18.41 and 18.26, close enough that neither adapter can
be said to have moved further than the other.

### Qt's ban lifts, and the corpus is complete (2026-09-16)

`scripts/resume_when_allowed.sh` probed Qt's Gerrit with five requests every twenty minutes
from 20:29. Fourteen probes answered nothing. The fifteenth, at 01:45, answered all five, and
the script built 2025-10 at one request a second, finishing 04:30: **826 examples, and Qt's
thirteenth month**. The snapshot had been on disk since the fetch that preceded the ban, so
nothing was refetched; only the build, which needs a comments request per change and a diff
request per comment, had been blocked. Total ban duration from the first refused handshake:
about thirty-three hours.

| Qt | examples | after dedup | pilot | train | dev |
|---|---|---|---|---|---|
| 12 months (before) | 10,622 | 9,918 | 1,300 / 403 ch | 8,254 / 3,160 ch | 364 / 175 ch |
| **13 months** | **11,448** | **10,695** | 1,301 / 404 ch | 8,443 / 3,208 ch | **951 / 462 ch** |

The dev window is where the missing month lived, so it nearly triples in changes and Qt's dev
window is now **larger than OpenStack's 235**, reversing the relationship every result so far
was measured under.

**The censoring figures move a third time, and settle.** Qt's dev was being reported as 70.4%
missing, which was one absent month plus real censoring of the other. With the month built it
is 28.8%, all of it censoring.

| window | OpenStack missing | Qt missing | differential | with Qt's month absent |
|---|---|---|---|---|
| train | 5.0% | 3.0% | 2.08 pt | 0.83 pt |
| dev | 39.1% | 28.8% | **10.33 pt** | 31.26 pt |
| test, fetched 2027-02 | 1.1% | 0.7% | **0.36 pt** | 0.54 pt |

Qt's estimator check passes at 0.019 against a two-standard-error band of 0.050. OpenStack's
still fails at 0.086 against 0.071, which is the cohort-trend limitation and is unaffected by
any of this.

**The decision has now survived three revisions of the numbers that justified it.** Creation
windows stand: the confirmatory contrast carries 0.36 points of differential censoring against
the dev window's 10.33, a factor of 29. The ratio has been 26, then 58, now 29, while the
underlying figures moved by tens of points. That the conclusion never depended on the figures
being right is worth more than any one of them.

**What it costs the windowed RQ1 result.** Job 144345 measured Qt's contrast on 175 changes,
+0.011 [-0.0208, +0.0433]. Job 145084 reruns it on 462, which should narrow that interval by
roughly a third without touching OpenStack's. Nothing about the earlier run was wrong; it was
measured on a corpus that was missing a month, and said so.

### The cohort-trend worry, tested properly (2026-09-16)

`scripts/quasi_independence.py`, `datasets/results/quasi-independence.json`. Lynden-Bell needs
the lag and its truncation limit to be quasi-independent, and the limit here is a
deterministic function of creation month, so the assumption is that review latency is
stationary over the eighteen months the corpus spans. The log recorded that it fails, citing
OpenStack's P(lag<=1) rising from 0.667 in the oldest cohort to 0.905 in a recent one. That
citation was wrong, and the conclusion drawn from it was too strong.

**The obvious diagnostic is biased, and most of that slope is the truncation.** Each cohort's
rate is conditional on its own horizon, so a cohort with three months to settle reports
P(lag<=1 | lag<=3), not P(lag<=1). Simulating from a single stationary law with the real
cohort sizes reproduces 0.753 rising to 0.904 on its own. The apparent trend is mostly an
artifact of the very truncation the analysis exists to correct.

| | conditional Kendall tau (Tsai 1990) | quasi-independence |
|---|---|---|
| OpenStack | +0.0173 [-0.0162, +0.0492] | not rejected |
| Qt | -0.0018 [-0.0310, +0.0267] | not rejected |

The tau is restricted to pairs whose order the truncation could not have hidden, which is
what makes it survive the bias above. Neither organization rejects.

**The goodness-of-fit check was also the wrong yardstick, and its verdict survives anyway for
one organization.** `censoring.py` compares the fit against the longest-horizon cohort and had
been calling a gap outside a binomial two-standard-error band a failure. The statistic is a
maximum over thirteen correlated lags, whose null is much wider than any single comparison, so
that band was too tight by construction. Calibrated against its own simulated null instead:

| | max gap | null median | null 95th | p | verdict |
|---|---|---|---|---|---|
| OpenStack | 0.0857 | 0.0359 | 0.0670 | **0.017** | rejected |
| Qt | 0.0192 | 0.0217 | 0.0443 | 0.580 | not rejected |

So OpenStack's fit really is off, at p = 0.017 rather than the clear failure the crude band
implied, and Qt's is squarely consistent. Quasi-independence is not what is wrong with
OpenStack: the tau does not reject it, and whatever the misfit is, it is not a latency trend
that a cohort covariate would absorb.

**How much it could matter, bounded by refitting.** Restricting the fit to the six most recent
cohorts trades the identified tail for cohorts that resemble the windows being estimated:

| | dev missing, pooled | dev missing, recent cohorts | test at acceptance |
|---|---|---|---|
| OpenStack | 39.1% | 32.2% | 1.1% -> 0.0% |
| Qt | 28.8% | 26.3% | 0.7% -> 0.0% |

The dev figures could be about seven points lower for OpenStack and two for Qt, in the
direction that makes the dev window less censored than reported. The test window's figure only
shrinks. So every way of fitting this points the same way on the decision: the confirmatory
contrast is nearly uncensored, and the dev window is not. The registered figure stays the
pooled one, which is the more conservative of the two.

**Corrected in the record.** The earlier entry's claim that quasi-independence "is violated,
and OpenStack's own check says so" rested on a biased diagnostic and a miscalibrated band. The
misfit is real but milder, the mechanism is unidentified, and the cohort-aware refit is a
bound rather than a fix.

### The prior question: is organization decodable at all? (2026-09-16)

`sphragis/measure/probe.py`, `scripts/separability.py`, `datasets/results/separability.json`.

RQ1 measures behaviour: whether an adapter trained on one organization generates better
refinements for it. It answers weakly, two exact-match points inside a twenty-six point
task-adaptation effect. The prior question had never been asked. If organizational identity
is not decodable from the review text at all, then that small advantage is not an adapter
failing to use a signal; there is no signal. If it is decodable, the gap between what a probe
reads and what an adapter uses is itself a finding, and a named one in the 2026 literature.

A bag-of-words naive Bayes over reviewer comment text, deliberately the weakest instrument
that could show the effect: deterministic, no hyperparameters to tune into a result, CPU
only. Changes are held out whole, as in the generative contrast, and the interval is a
cluster bootstrap over changes.

**Accuracy alone would have meant nothing.** OpenStack's examples are 47% Python and Qt's are
49% C++, so a classifier reading file extensions scores in the nineties having learned only
which language it is looking at. Two controls, following Liu et al. (arXiv:2606.07103, 2026):
restrict to one suffix present in both organizations, and compare against two projects inside
a single organization, which is what "different codebase, same organization" looks like.

| condition | changes | balanced accuracy |
|---|---|---|
| raw, any file type | 4,048 | 0.837 [0.812, 0.837] |
| **cross-organization, `.py` only** | 319 | **0.849 [0.769, 0.867]** |
| within OpenStack, two projects, `.py` | 180 | 0.828 [0.726, 0.838] |
| within Qt, two projects, `.py` | 39 | 0.892 [0.749, 0.954] |

**The cross-organization probe does not beat the within-organization baselines.** Its estimate
sits inside both of their intervals. Two projects in one organization are as separable as two
organizations, so what the classifier reads is the codebase, and organization is the wrong
altitude to look for it.

**This is not the representation-behaviour dissociation it was expected to be.** The design of
that check anticipated a probe that could see what the adapter could not use. Instead both
instruments agree: near-zero at the organizational level, strong at the project level. Two
methods with nothing in common reaching the same answer is the most informative thing to come
out of the corpus so far.

**What it suggests, and what the corpus can test.** The study fixes the organization as the
unit. The evidence says the codebase is. The same apparatus can test it without modification:
assemble two project-level corpora inside one organization, train an adapter on each, and run
the identical matched-versus-mismatched contrast. If project-level adapters separate where
organization-level ones do not, the finding is that the learnable unit is the codebase, which
is directly consequential for the direction: a privacy perimeter drawn around an organization
is not drawn where the signal lives.

**Three bugs found while building it, all in the probe rather than the data.**

- Folds were assigned by shuffling changes round-robin, leaving each fold's label mix to
  chance. Measured at 0.25 to 0.79 on a balanced sample, which moved a chance-level probe to
  0.65. Folds are stratified by label now.
- The bootstrap renamed each resampled change so a twice-drawn change became two clusters.
  That is right for a difference of means and wrong for a cross-validated classifier: the
  copies carry identical text, landed in different folds, and trained the model on what it was
  scored on. The interval rose clear of the estimate it was meant to bracket, 0.837 against
  [0.856, 0.874].
- Balanced accuracy was pooled across folds before averaging. A fold predicting everything one
  label and another predicting everything the other each score 0.5 alone, but their pooled
  recalls average above it, which returned 0.55 for a probe with nothing to learn. It averages
  per fold now, and the null fixture sits at exactly 0.5.

**Stated limits.** A bag-of-words model is weak, so a null from it is weaker evidence than a
null from a learned encoder would be. Qt's within-organization pair is 39 changes against a
Python-bindings project, which is a poor control; OpenStack's 180-change pair is the one to
read. And `.py` is the only suffix both organizations carry in quantity, so the content
control costs most of the corpus.

### OpenStack's capture misfit: three explanations tested, none survives (2026-09-16)

Calibrated against its own simulated null, the fit's maximum gap against the longest-horizon
cohort rejects OpenStack at p = 0.017 and clears Qt at p = 0.580. The roadmap carried this as
an open item with a named suspect. The suspect is innocent, and so are the next two.

- **A latency trend violating quasi-independence.** Ruled out already: Tsai's conditional
  Kendall tau rejects quasi-independence for neither organization.
- **A mixture of project families.** OpenStack's corpus spans 264 projects, and the two big
  communities settle at genuinely different speeds: F(0) is 0.594 for `starlingx/*` against
  0.390 for `openstack/*`, a twenty-point gap in same-month settling. Their mix also drifts,
  from 32% starlingx in the oldest cohort to 48% in the newest. That is a real compositional
  effect, and it does not explain the misfit: fitting `openstack/*` alone still rejects at
  p = 0.035.
- **Qt being the more homogeneous corpus.** False, and measurably so. Qt's per-project lag
  distributions spread further than OpenStack's family-level gap: F(0) runs from 0.512 for
  `qt/qtdeclarative` to 0.758 for `qt-creator/qt-creator`, a spread of 0.247 across five
  projects with at least 120 changes each. Qt's top project also drifts more across cohorts
  than OpenStack's, 6.2 points against 3.4. Whatever distinguishes the two organizations
  here, it is not that one is a cleaner single population.

**So the misfit stays open**, and the honest reading is that the fit is off for OpenStack for
a reason not yet identified. The bound already recorded still holds: refitting on recent
cohorts moves the dev figure about seven points toward less censoring and leaves the test
window's figure smaller, so every fit points the same way on the decision that depends on it.

**The side finding is worth more than the answer that was being chased.** Review latency
differs sharply between projects inside one organization. `qt-creator` settles 76% of its
example-bearing changes in the month they were created; `qtdeclarative` settles 51%. That is
a quarter of the whole distribution's range, between two projects under one organizational
roof, measured on process rather than prose.

It corroborates the separability probe from a direction with nothing in common with it. The
probe read reviewer word choice and found projects as distinguishable as organizations; this
reads how long review takes and finds the same thing. Two measurements sharing no mechanism,
agreeing that the project is where the variation lives, is a stronger argument for the
granularity question than either alone.

### The contrast is not blind (2026-09-16, job 145092, condition `marker-1`)

`datasets/results/calibration-marker-1.json`. One organization's train window split into two
halves that no change spans, a fixed annotation appended to every refinement in the second,
and the identical matched-versus-mismatched contrast RQ1 uses.

| arm | exact match |
|---|---|
| base on half b | 0.000 |
| a's adapter on a | 0.310 |
| a's adapter on b | 0.000 |
| b's adapter on b | 0.316 |
| b's adapter on a | 0.000 |

| contrast | estimate |
|---|---|
| half a, 182 changes | **+0.3096 [+0.2488, +0.3761]** |
| half b, 182 changes | **+0.3158 [+0.2652, +0.3672]** |

**The question this was built to answer is answered.** Every null this study has produced was
ambiguous between "there is no organizational fingerprint" and "this contrast cannot see
fingerprints of any size". It can see them. Planted at full strength the contrast returns a
third of an exact-match point per example with intervals nowhere near zero, and the gate
passes. So RQ1's nulls are nulls about organizations, not about the instrument.

**The scale is worth stating plainly.** A convention imposed on every refinement moves the
contrast +0.31. The real difference between OpenStack and Qt moves it +0.021. Whatever
separates two organizations is roughly a fifteenth of a mechanical rule applied to everything.

**And the apparatus refused to read it, correctly, for the first time on a real run.** The
mismatched arms score exactly 0.000, because an adapter trained on annotated refinements
appends the annotation to everything and can never exact-match an unannotated reference. Test
5, `non_degeneracy`, requires every condition's exact match to lie strictly between 0 and 1,
so it failed and `apparatus_holds` went false: **APPARATUS FAILS: H1 would not be read.**

That is the halt rule doing its job on live output rather than in a unit test. The ceiling
condition is degenerate by construction, and a study that reported "gate passes, +0.31" from
it would be reporting an artifact of a rule that makes one arm unscoreable. The pass is
evidence the instrument works; it is not a result, and the apparatus is what says so.

**So the floor is not yet known.** Full strength is too strong to measure with: it saturates
one arm. The detection floor lives at the weaker rates the sweep already generates, realised
0.039, 0.086, 0.251 and 0.501 for the annotation and 0.007 to 0.164 for quote style, where
the mismatched arm can still score above zero. Those conditions are what turn a null into a
bound, and they are the next run.

**Recorded caveat.** The annotation is deliberately the easiest thing a model could learn, so
its +0.31 is a ceiling and not an estimate of what any realistic convention would produce.
The quote-style conditions are the realistic end, and their ceiling is a realised 0.164
because most refinements carry no single-quoted literal.

### SPORC as the interim target (2026-09-17)

Research Computing retired `rc-onboard` for research jobs on 2026-09-16, and `fl-mlm` has no
TIGRIS association: in ColdFront both `fl-mlm` and `prdiscourse` carry **Needs Review**, and
the RC documentation grants every project without a pending review a TIGRIS Slurm account.
`sacctmgr show assoc` lists `fl-mlm` on `sporc` only, and `sbatch --test-only` against the
`tigris` partition fails with `Invalid account or account/partition combination`.

`fl-mlm` is valid on SPORC today. Measured there, not taken from node tags:

| | |
|---|---|
| architecture | x86_64 Intel, RHEL 9.8 |
| GPU | A100 40 GB (job 21705076, `nvidia-smi`); one node of 4x H100 80 GB |
| driver | 610.43.02, CUDA 13.3, so the cu130 torch wheel applies; the `cuda11` feature tag is stale |
| network | compute nodes reach PyPI, the PyTorch index, GitHub and Hugging Face (job 21705465) |
| submission | from the TIGRIS login with `--clusters=sporc`; `$HOME` is shared |

**Not yet measured:** whether the 7B training fits in 40 GB. No TIGRIS run logged peak GPU
memory, and the GH200's unified memory would not have surfaced an overflow. The first SPORC
run records it, along with seconds per step, since every `--time` in `scripts/` was sized on a
GH200.

**Constraint this places on results.** A result set that mixes GH200 and A100 runs confounds
the contrast with hardware. Each job's output now records its cluster, job, GPU and peak memory,
so a mixed set is detectable from the files rather than from memory. Results written before
2026-09-17 carry no such record; all of them ran on TIGRIS GH200s under `rc-onboard`.

### Where RQ2 sits against the 2026 attacks (2026-09-17)

RQ2 asks what a federated adapter update leaks about the client that produced it. Three lines of
prior work bound what is already known, and none of them asks RQ2's question.

**The strongest 2026 attack is exact-membership, and it assumes the easy threat model.**
ProjRes (arXiv:2604.21197, April 2026), read in full: an honest-but-curious server observing
**per-client** gradients, with **neither secure aggregation nor differential privacy** assumed.
It reports AUC 1.000 on CoLA and SST across BERT-Base, GPT2-Large, Llama3-8B and Qwen2.5-14B,
and LoRA leaks at AUC 1.000 for batch sizes up to 16 and 0.957 at 512. Two things limit how
far it reaches into this study:

- **It degrades on long inputs.** AUC falls to 0.807 (BERT-Base) and 0.819 (GPT2-Large) on
  IMDB, and the authors attribute it to sequence length. A code-review refinement with its
  context is long, so the headline "near 100%" should not be expected on this corpus.
- **It names RQ2's question as out of scope.** Quoting its limitations: "ProjRes operates as a
  data-level MIA, identifying membership only when a sample exactly matches one in the training
  set ... this overlooks the semantic generalization ability of LLMs." Inferring a property of
  the data, rather than the presence of one sample, is the part it does not do.

**Secure aggregation is not a safe harbour, so RQ2 cannot lean on it.** Gradient disaggregation
(arXiv:2106.06089) recovers individual updates from aggregates using how often each client
participated across rounds. Kerkouche, Ács and Fritz (arXiv:2303.03908, WPES at CCS 2023), read in full,
infer client-specific properties from aggregated updates alone, passively, through the
linearity of aggregation over rounds whose client composition varies. Their method, PROLIN, is
evaluated on **MNIST, CIFAR-10 and Fashion-MNIST with a LeNet**, and the only properties studied
are **membership** and **misbehaviour** (gradient-inversion and gradient-ascent poisoning). Data
source is not studied. Its F1 falls as clients grow, from 50 to 200 in their CIFAR-10 runs.

The more useful line is in their future work: PROLIN "is not limited to membership inference and
misbehaving detection, it can disaggregate the linear features of any supervised detector
model." That hands RQ2 its attack for the secure-aggregation threat model rather than leaving it
to be invented: a source-identity detector, trained the way the separability probe is, plugged
into PROLIN's disaggregation. Neither the property nor the domain has been tried.

**What none of them target: where a client's data came from.** Every attack above infers
something about individual samples or client behaviour. RQ2's property is the client's
**source** -- which organization, or which project -- and this corpus is what makes that
question testable, with two organizations, 345 projects and a time-split design.

**And tonight's evidence changes what the question should be.** The separability probe found
two projects inside one organization as distinguishable as two organizations, and review
latency differs between projects under one roof by a quarter of its whole range. If source
leaks, it most likely leaks at the project, not the organization. So RQ2 is sharper framed as
two altitudes than one: does an adapter update reveal its organization, does it reveal its
project, and which boundary does a privacy policy drawn around organizations actually protect.

**The design this implies.** Two threat models, because the literature has shown the second is
not a defence: a server seeing per-client updates, and a server seeing only aggregates across
rounds of varying composition, attacked through the linearity that 2303.03908 exploits. And
the long-input regime is where the contribution is sharpest, since that is exactly where the
exact-membership state of the art is weakest.

### The contamination battery separates the windows, on fifteen times the sample (2026-09-17, job 148092)

`datasets/results/contamination-openstack-6mo-with_context.partial.json`. Six post-cutoff months
(2024-10 to 2025-03) against the six-month control (2023-08 to 2024-01), built by one pipeline
under one cutoff, scored on hunks with context on the base `Qwen/Qwen2.5-Coder-7B`. The job was
still running guided completion when these were read: the membership scores are written before
the generative half starts, which is the change made after job 145093 lost exactly this result
to its wall.

| method | post-cutoff | pre-cutoff | gap | 95% bootstrap, by change |
|---|---|---|---|---|
| **Min-K%++, k = 20** | -1.7952 | -1.7212 | **-0.0740** | **[-0.1286, -0.0175]** |
| Min-K%, k = 20 | -7.4461 | -7.3169 | -0.1291 | [-0.2824, +0.0287] |

1,971 post examples over 797 changes, 2,503 pre over 877. The point gap is `compare_windows`,
the battery's own definition; the interval resamples changes within each window independently,
since the windows are separate populations rather than paired.

**The first interval in this battery's history to exclude zero, and it points the right way.**
A higher Min-K%++ score means the model finds the text more familiar. The pre-cutoff control,
which predates the checkpoint's release and plausibly sits in its training data, reads as more
familiar than six months the model cannot have seen. That is the direction membership predicts
for a known-member control.

| run | control | scored | Min-K%++ gap |
|---|---|---|---|
| 143898, bare hunks | 1 month | 35 / 28 | +0.221 [-0.052, +0.524] |
| 143956, with context | 1 month | 161 / 116 | -0.058 [-0.200, +0.084] |
| **148092, with context** | **6 months** | **1,971 / 2,503** | **-0.074 [-0.129, -0.018]** |

The point estimate held still between the last two runs while the interval tightened around it
until zero fell outside. That is what more sample does to an effect that is there, and what it
does not do to noise, which on the first run had the sign backwards.

**What it supports: less than the first reading of it claimed.** This entry originally called
the gap evidence that the instrument works. A control run an hour later withdrew that.

Meeus et al. (SoK, SaTML 2025, arXiv:2406.17975) show that membership benchmarks built post hoc,
with members and non-members from different periods, carry distribution shifts strong enough
that a **model-less bag-of-words classifier** separates them, and that this invalidates reading
the separation as memorization. This battery is such a split, so their control was run on
exactly the 4,473 examples Min-K%++ scored, using the separability probe's classifier with
changes held out whole:

| discriminator, post against pre | score |
|---|---|
| model-less bag-of-words, no model at all | balanced accuracy **0.589 [0.555, 0.639]** |
| Min-K%++ on the base checkpoint | AUC **0.540 [0.516, 0.564]** |
| Min-K% | AUC 0.530 [0.497, 0.561] |

**The blind baseline does at least as well as the membership score, and probably better.**
Balanced accuracy is a lower bound on a classifier's AUC, so the comparison is tilted toward
Min-K%++, and it still does not come out ahead. By the SoK's criterion the -0.074 gap cannot be
told apart from ordinary drift across eighteen months of OpenStack: the windows differ in what
they are about, and that alone is enough to produce a gap this size.

So the battery neither shows exposure nor validates the instrument. It is uninformative, which
is a legitimate result for a check registered as corroborative, and it means the study's
protection against contamination rests where it always did: **the post-cutoff windows postdate
the checkpoint's release**, so they cannot have been in its pretraining data, whatever a
membership score says. The SoK's recommended alternatives, randomized splits and injected
canaries, need control over the training data, which a released checkpoint does not give.

**Min-K% does not separate the windows.** Its interval covers zero on the same examples. That is
the pattern expected if Min-K%++ is the more sensitive of the two, which is the reason it is the
registered primary.

**The completed job agrees with the partial, and guided completion stays at its floor.** The full
`contamination-openstack-6mo-with_context.json` reproduces both membership gaps to four places,
so writing membership first lost nothing. Guided completion reproduced 1.13% of 798 post-cutoff
hunks verbatim against 0.88% of 1,027 control hunks, a gap of +0.0025: the floor, as registered.
Its edit similarity leans the same way as Min-K%++, 0.364 post against 0.403 pre, and the
blind-baseline result applies to it exactly as it does to the membership score, so it is not
read as exposure either.

### The calibration sweep: the adapter, not the contrast, sets the floor (2026-09-18, jobs 148088-148091)

`datasets/results/calibration-marker-{0,0.05,0.1,0.25}.json` beside `marker-1`. One organization's
train window split into two halves no change spans, a fixed annotation appended to a fraction
of refinements in half **b** only, and the identical matched-versus-mismatched contrast RQ1 uses.
One condition per job, all under `fl-mlm`.

| condition | realised plant | contrast on a | contrast on b | gate |
|---|---|---|---|---|
| marker-0 | 0.000 | -0.018 [-0.046, +0.008] | -0.014 [-0.038, +0.009] | fail |
| marker-0.05 | 0.039 | -0.005 [-0.034, +0.028] | +0.019 [-0.002, +0.043] | fail |
| marker-0.1 | 0.086 | -0.009 [-0.034, +0.016] | -0.012 [-0.038, +0.014] | fail |
| marker-0.25 | 0.251 | **+0.214 [+0.147, +0.288]** | **-0.086 [-0.137, -0.037]** | mixed |
| marker-1 | 1.000 | +0.310 [+0.249, +0.376] | +0.316 [+0.265, +0.367] | pass, degenerate |

**The negative control is clean.** Two halves differing in nothing return intervals covering zero
on both sides, the adapters emit the annotation 0.000 of the time, and the apparatus holds. The
contrast does not fire on a difference that is not there.

**What sets the floor is what the adapter learns, and that is not linear.** The annotation's
presence in the planted adapter's outputs, against its presence in the training data:

| realised in training | emitted by the planted adapter, on a / on b |
|---|---|
| 0.000 | 0.000 / 0.000 |
| 0.039 | 0.007 / 0.006 |
| 0.086 | 0.033 / 0.076 |
| **0.251** | **0.617 / 0.656** |
| 1.000 | 0.991 / 0.998 |

Below about a tenth of refinements the adapter barely absorbs the convention at all, so there is
nothing for the contrast to see: 0.039 and 0.086 return nulls not because the instrument is blind
but because the adapter never learned what was planted. At a quarter it absorbs it and then
**amplifies** it, applying a convention seen in 25% of training refinements to roughly 64% of its
outputs. Greedy decoding is the registered decoder and is the likeliest amplifier, since it turns
a sharpened distribution into a deterministic choice; that is a hypothesis, not yet tested.

**Amplification makes the contrast non-monotone, and that is a validity problem for the gate.** At
0.25 the planted adapter adds the annotation to references in its own half that do not carry it,
scoring 0.160 there against the unplanted adapter's 0.246. So the matched adapter **loses on its
own organization's data**, and the contrast on the planted side reads -0.086, excluding zero on
the refuting side. The fingerprint is learned, strongly, and the gate reads it as evidence against
the hypothesis. Between the floor and the ceiling the gate never passes: its only pass is the
degenerate case where one arm cannot score.

**What that means for RQ1's null.** An organizational convention present in under a tenth of
refinements would not be learned by these adapters, so it could not show; one present in a
quarter could be learned and still read negative. RQ1's +0.021 is consistent with no fingerprint,
and also with a low-frequency one the adapter never absorbs. The honest bound is therefore on what
**LoRA adaptation under greedy decoding absorbs and reproduces**, not on what organizations
contain, and the Stage 1 report has to say so rather than presenting the null as a statement about
organizations alone.

**What is known already, and what is not.** The two tendencies underneath are not new. LoRA's
low-rank constraint favouring dominant patterns over minority ones is reported for classification
(Scientific Reports, 2026, from its summary only), and generative models over-weighting their
modes is a recurring theme of the model-collapse literature. The closest recent work, Skobelev,
Fithian and Han (arXiv:2609.16454, 15 September 2026, read in full), fine-tunes with LoRA on
surveys and CodeNet and finds that supervised fine-tuning moves output diversity toward the
target from either direction depending on model and data. It measures collision probability
**under sampling**, and it does not address greedy decoding at all, nor compare a pattern's
training frequency with its output frequency.

What this entry adds is the consequence for measurement: under the greedy decoding that exact
match requires, a matched-versus-mismatched adapter contrast is **non-monotone in the strength of
the convention it is supposed to detect**, so a fingerprint learned strongly enough to be
over-applied reads as evidence against itself. That is a statement about how adapter-attribution
studies score their own results, and it does not depend on why the amplification happens. Whether
greedy is the amplifier is the separate, testable question, and testing it would set this result
directly beside 2609.16454's sampling-based one.

**Two limits on reading this, stated.** The plant is asymmetric, one half carrying a convention and
the other none, whereas two organizations each carry their own. A symmetric version, convention X
on one half and convention Y on the other, is the closer analogue and would show whether the
matched adapter wins on both sides once neither is convention-free. And the annotation is the
easiest thing a model could learn, so the absorption threshold for a realistic convention is
likely higher than a tenth, not lower.

### RQ1's central finding has not been measured before (2026-09-18)

A novelty check on the result the windowed run produced: that adaptation to code-review
refinement is large and almost entirely task-general, with the organization-specific share about a
thirteenth of it (0.048 to 0.306 exact match, of which 2 points separate matched from mismatched).

The closest work fine-tunes for exactly this task and metric and does not make the decomposition.
Begolli, Aksoy and Neider (arXiv:2507.19271, v2 October 2025, read in full) fine-tune for code
refinement scored by exact match and BLEU, on five industrial C# repositories from one partner plus
the CodeReviewer benchmark. They **do not compare within-project against cross-project
performance, and do not separate task-general gains from organization-specific ones**; they
attribute what they observe to general C# patterns and domain shift. LLaMA-Reviewer
(arXiv:2308.11148) likewise reports the gain from parameter-efficient tuning without decomposing it.

So the matched-versus-mismatched design does something the literature has not: it splits a
fine-tuning gain into the part any organization's data would have taught and the part only that
organization's could. That is the contribution to state first in the Stage 1 introduction, and it
holds whether RQ1's gate passes or fails, because the decomposition is the measurement and the gate
is only one reading of it.

### A codebase fingerprint inside one organization (2026-09-18, job 148203)

> **Qualified later on 2026-09-18.** qt-creator's home advantage holds against qtbase only; against
> qtdeclarative it is +0.005, covering zero. See "The qt-creator fingerprint is a property of the
> pair, not the project" below.

`datasets/results/projects-qt-creator_qt-creator-qt_qtbase.json`. The identical matched-versus-
mismatched contrast RQ1 runs, between two projects inside Qt: `qt-creator/qt-creator` (a) and
`qt/qtbase` (b), holdout by change within the train window, equalized at 1,517 training examples
each, one seed. Both are C++, so the language confound that separates OpenStack from Qt is absent.

| side | matched minus mismatched |
|---|---|
| **qt-creator** | **+0.0788 [+0.0412, +0.1182]** over 154 changes |
| qtbase | -0.0187 [-0.0462, +0.0085] over 166 changes |

Gate: mixed. All seven outcome-neutral checks pass.

| exact match | on qt-creator | on qtbase |
|---|---|---|
| qt-creator adapter | **0.352** | 0.259 |
| qtbase adapter | 0.273 | 0.241 |
| base model | 0.017 | 0.016 |

**qt-creator carries something learnable that is specific to it.** Its own adapter beats the other
project's on its refinements by 7.9 points, interval clear of zero, roughly four times the +0.021
separating OpenStack from Qt. That is not qt-creator's data being easier: it is easier for any
adapter, qtbase's included (0.273 there against 0.241 at home), but the contrast compares two
adapters on the identical examples, so difficulty lifts both arms and cancels. What remains is what
only qt-creator's training data could teach.

**qtbase shows no home advantage, and the reason is visible.** The qt-creator adapter is the stronger
of the two in general and edges ahead even on qtbase, 0.259 against 0.241. A project whose own
adapter cannot beat a neighbour's on its home data either has no distinctive conventions an adapter
absorbs, or has them below the floor the calibration found. The fingerprint is a property of some
projects, not of every project.

**Two measurements with no mechanism in common single out the same project.** qt-creator is also the
outlier on review latency, settling 76% of its example-bearing changes in their creation month
against 51% for `qtdeclarative`. The separability probe, the latency analysis and now the generative
contrast each point at the project as the unit where variation lives, and two of them at this
project specifically.

**Recorded caveats.** The split is holdout by change inside one window, not the time split the
windowed RQ1 used, and holdout is the easier test because train and evaluation share a period. The
fairer comparison is therefore the organization-level pilots, which were holdout by change too and
returned intervals straddling zero, though on far fewer changes. One seed. And one project pair: the
claim this licenses is that a codebase-level fingerprint exists and can be larger than the
organizational one, not that every project has one.

### Greedy decoding adds to the amplification; it does not cause it (2026-09-18, job 148202)

`datasets/results/decoding-marker-0.25-t1.0.json`. The saved `marker-0.25` adapter, trained with the
annotation on 25.1% of refinements, re-evaluated on exactly the held-out examples the calibration
scored, with only the decoder changed.

| evaluated on | greedy | sampling, T = 1 |
|---|---|---|
| half a, 449 examples | 0.617 | 0.499 |
| half b, 488 examples | 0.656 | 0.545 |

Sampling from the adapter's own unaltered distribution removes about 0.11 on each half and still
emits the annotation at roughly **twice** the rate it was trained on. So the over-weighting is in what
the adapter learned; greedy decoding amplifies it by about a fifth on top. Changing the registered
decoder would not make the gate monotone, which settles the roadmap question of whether it could,
and places this beside Skobelev, Fithian and Han (arXiv:2609.16454), whose sampling-based results
report fine-tuning moving output diversity toward the target: here, for a planted minority
convention, it overshoots.

### The interval measures one source of variance of two, and a null crossed zero (2026-09-18)

> **Corrected later on 2026-09-18.** The inference below overreaches. The run-to-run swing is what
> seed-by-change noise produces (z = -1.47 and 0.00 against it), and a single-seed change bootstrap
> already carries that noise. The only variance it omits is a seed main effect, which two runs give
> no evidence of. The retraction of single-seed results and of the conjunctive power is withdrawn
> pending a measurement. See "The run-to-run swing is noise the interval already carries" below.

The most consequential finding of the night, and it is about the gate rather than about
organizations.

**A pure null excluded zero.** `sym-0` plants nothing, so at a fraction of zero it is the same
experiment as `marker-0`. Verified identical: one SHA-256 per half across the two, the same held-out
examples arm for arm, the same split, training and bootstrap seeds. The two runs disagree:

| identical null | contrast on a | contrast on b |
|---|---|---|
| marker-0 (148088) | -0.018 [-0.046, +0.008] | -0.014 [-0.038, +0.009] |
| **sym-0 (148199)** | **-0.036 [-0.065, -0.007]** | -0.014 [-0.039, +0.010] |

**Nothing on the GPU is bit-reproducible.** Final training losses differ in the fourth decimal (a:
0.30191 against 0.30273). Adapter arms change 19 to 27% of their greedy predictions between the
runs, 83 to 118 of about 440. **The base model, with no adapter, changes 23 to 28 of them.** So
inference is not deterministic either, although `HFGenerator` justifies greedy decoding on exactly
the ground that exact match needs a deterministic output. The mechanism is documented: Yuan et al.
(arXiv:2506.09501, "Understanding and Mitigating Numerical Sources of Nondeterminism in LLM
Inference", read from the abstract page) report bfloat16 greedy decoding varying by up to 9% in
accuracy on DeepSeek-R1-Distill-Qwen-7B across differences in GPU count, GPU type and evaluation
batch size, and attribute it to floating-point arithmetic not being associative. Their remedy,
LayerCast, keeps weights in 16-bit and computes in FP32. What was seen here is the stronger case:
the same GH200 type and the same configuration, run twice.

**The consequence is the gate, not these two runs.** The pairs cluster bootstrap resamples changes
and holds the trained adapters and the decoder fixed. It measures sampling variance only. The
variance from retraining, seed and GPU nondeterminism alike, is about 0.018 on a single contrast,
the same size as the organizational effects this study has been reading (+0.016 to +0.034). An
interval that omits it is too narrow, and a null crossed zero because of it. Bouthillier et al.
(MLSys 2021, arXiv:2103.03098) show that variance from initialization and data sampling "impact
markedly the results" and that a comparison needs trials across those sources, not a bootstrap over
test data alone. The registered design has three seeds for this reason, but the gate reads the
median seed's interval, which is still a single-seed interval.

**What the literature does, and why neither half is enough.** Du et al. (arXiv:2511.19794, November
2025) bootstrap over per-seed deltas on a fixed test set, the opposite choice: seed variance in,
sampling variance out, and three values to resample. At three seeds their protocol never declared a
0.5 to 2.0 point gain significant, even where unpaired t-tests gave p < 0.05. This study's effects
are 1.6 to 3.4 points. A **two-level bootstrap**, resampling seeds and then changes within each,
carries both sources and is the interval the gate should read.

**What it changes in numbers already reported.**

- **No single-seed contrast in this log can be read as excluding zero.** That includes Qt's +0.034
  [+0.010, +0.060] on the full dev window and qt-creator's +0.079 [+0.041, +0.118]. The second is
  about four times the run-to-run swing and is likely to survive; the first is not.
- **The conjunctive power of 0.757 and 0.833 is overstated.** It was simulated from example-level
  resampling of one run, so it too omits seed variance, and the true power at the registered design
  is lower.
- **The gate's non-monotonicity result is unaffected in kind**: the swings there (+0.214 against
  -0.086) are ten times the run-to-run variation.

**What it does not change.** The calibration's negative control is still clean in the sense that
matters: both nulls fail the gate, and the one excursion lies on the refuting side. The contrast
does not manufacture a pass from nothing. It can, however, manufacture an interval that looks
decisive, which is the reason to widen it.

## Open bugs & findings

- **The estimand is not pre-registered.** `paired_difference` pools examples, so a change
  with 40 hunks counts 40 times one with a single hunk. The design fixes the resampling unit
  (the change) and never the estimand. On the seed-0 pilot split one change is 18 of 45
  eval examples; change-weighted exact match is 0.261 against 0.200 pooled. Measured gaps
  between the two estimands reach 118% of a 0.08 effect at 19 changes. A Stage 1 decision.
  On the deduplicated rerun, where the largest held-out change has 5 examples, both give
  +0.333, so the choice did not move this pilot; it will on a window with a large change.
- **The gate reads one-sided.** Now implemented as the lower bound of the 95% interval,
  alpha 0.025. The Stage 1 skeleton still says "excludes zero" and must say which.
- **`model.py` has no tests.** The budget arithmetic moved into `training.py` where CI
  reaches it; `HFGenerator` and the loop body remain untested on CPU.

### Both halves carrying a convention: the gate passes on both (2026-09-18, job 148200)

`datasets/results/calibration-sym-0.25.json`. The symmetric calibration: half a carries `  # approved`
on 25% of refinements, half b carries `  # reviewed` on 25%, equal-length markers, neither half
convention-free. This is the realistic case, two organizations each with their own habit, and it is
the case the asymmetric sweep could not speak to.

| side | matched minus mismatched |
|---|---|
| a | **+0.084 [+0.049, +0.124]** over 183 changes |
| b | **+0.039 [+0.005, +0.071]** over 183 changes |

Gate: **pass**. All outcome-neutral checks pass.

| emission of | on half a | on half b |
|---|---|---|
| a's adapter, its own `# approved` | 0.577 | 0.570 |
| b's adapter, its own `# reviewed` | 0.665 | 0.656 |

Neither adapter ever emits the other's marker. Each over-applies its own at about two and a half
times the trained rate, as in the asymmetric sweep, and on both halves alike, so emission tracks
what the adapter was trained on and not what it is evaluating.

**What this settles.** At 0.25 the asymmetric condition refuted the hypothesis on the planted side
(-0.086), because the one adapter carrying a habit lost to an adapter carrying none. With a habit on
each side, over-application costs both adapters on the other's half, and the matched adapter wins at
home on both. The non-monotonicity found in the sweep is a property of the one-sided design, not of
the gate in the case RQ1 is about. It remains a stated limitation for a fingerprint present in one
organization only.

**What it costs.** Exact match halves: 0.147 and 0.148 at home against about 0.31 in the null
(`sym-0`). The gate detects a learned convention even when learning it hurts the metric overall,
which is the right behaviour for a fingerprint test and the wrong one to read as usefulness. Side b's
lower bound, +0.005, sits close to zero on one seed.

### The qt-creator fingerprint is a property of the pair, not the project (2026-09-18, jobs 148377, 148378)

The two remaining pairs among qt-creator, qtbase and qtdeclarative, same protocol as job 148203,
one seed each. qtdeclarative is the smallest of the three, so both new pairs equalized at 788
training examples per side, against 1,517 for the first pair.

| pair (a vs b) | train per side | a: matched minus mismatched | b: matched minus mismatched |
|---|---|---|---|
| qt-creator vs qtbase | 1,517 | **+0.079 [+0.041, +0.118]** | -0.019 [-0.046, +0.008] |
| qt-creator vs qtdeclarative | 788 | +0.005 [-0.024, +0.032] | **+0.042 [+0.005, +0.074]** |
| qtbase vs qtdeclarative | 788 | -0.024 [-0.060, +0.008] | +0.017 [-0.025, +0.059] |

**qt-creator's home advantage does not replicate.** Against qtdeclarative, qtdeclarative's adapter
scores 0.323 on qt-creator's refinements against qt-creator's own 0.328. So the earlier entry's claim
that qt-creator "carries something learnable that is specific to it" holds relative to qtbase only.
Two of six directional contrasts exclude zero, both on the supporting side, in different projects.

**qtbase never wins at home.** Its adapter loses on its own refinements to both neighbours' (0.241
against 0.259, 0.243 against 0.267). qtbase teaches an adapter less than the other two do, at home
and away, which reads as heterogeneous or harder training data rather than an absence of habits.

**The pairs differ in training size as well as in projects.** The first pair trained on twice the
data. Job 148408 reruns qt-creator vs qtbase cut to 788 per side (`TRAIN_SIZE=788`, a nested
subsample of the 1,517), which separates the two explanations for qt-creator's +0.079.

**What survives for Plan D.** The separability probe still reads the codebase and not the
organization. The generative contrast between projects is real in two directions of six and not
organized by project, so "the codebase is the unit" is not yet supported by the contrast itself.

### The run-to-run swing is noise the interval already carries (2026-09-18)

Corrects "The interval measures one source of variance of two" above.

A single-seed contrast is the true effect, plus a seed main effect b that shifts every change at
once, plus per-change terms, plus seed-by-change interaction e. The change bootstrap resamples
changes, and e varies from change to change, so its spread is inside the interval already. What
the interval cannot see is b. The swing between the two identical nulls therefore indicts the
interval only if it is larger than e alone would make it.

It is not. Per example, the difference between the two runs' contrasts, clustered by change and
bootstrapped (10,000 resamples), gives the swing that seed-by-change noise alone would produce:

| half | marker-0 | sym-0 | swing | its SE with no seed effect | z |
|---|---|---|---|---|---|
| a | -0.0178 | -0.0356 | -0.0178 | 0.0122 | -1.47 |
| b | -0.0137 | -0.0137 | 0.0000 | 0.0087 | 0.00 |

Only 3.7 to 5.3% of per-example contrasts change between the runs. Adapter predictions change on
18.5 to 26.3% of examples, but exact match on only 1.8 to 2.9%: 87 to 91% of the changed predictions
go from one wrong answer to another (base model: 22 of 23 and 27 of 28). A moment estimate from the two halves puts b's
SD near 0.005, which would widen a single-seed standard error of about 0.014 by some 6%. Two runs
cannot distinguish that from zero, and cannot rule out more.

**What this withdraws.** The claim that no single-seed contrast can be read as excluding zero, and
that the conjunctive power of 0.757 and 0.833 is overstated. Both rested on reading the 0.018 swing
as omitted variance. The sym-0 excursion below zero is the kind of tail event a mildly
anti-conservative interval produces across the dozen null contrasts this log has read.

**What stands.** Nothing on the GPU is bit-reproducible, and the registered design's three seeds
still need an interval that spans them rather than one seed's. That interval is built (next entry),
and the seed main effect is now being measured rather than inferred: `sym-0` at seeds 2 and 3.

### An interval that spans the seeds: built, reviewed, and its coverage measured (2026-09-18)

`sphragis/measure/stats.py` `crossed_bootstrap`, `sphragis/experiment/walk.py` `crossed_gate`.
Seeds and changes are crossed, every seed scoring every change, so the interval is Owen's
pigeonhole bootstrap (Ann. Appl. Stat., 2007): one draw of seeds and one of changes, independent
and with replacement, the statistic over their intersection. A nested bootstrap (changes, then
runs within each) would treat a seed's shift as independent per change and average it away; an
industry comparison of that nested form found it very conservative, 98 to 99% coverage against
95 (Indeed Engineering, July 2026). Built beside the registered gate, not registered.

**Coverage under a null** (`datasets/results/crossed-coverage.json`, `scripts/crossed_coverage.py`).
Changes, their sizes and difficulty drawn from sym-0's real null arm; stable per-arm outcomes and
per-seed redraws calibrated to the three things the identical nulls measured (single-seed SE,
run-to-run SD, churn). A seed main effect sigma_b is added, sized by bisection so the effect
realized after clipping is the one labelled, and re-measured from the trials (0.0051, 0.0104,
0.0203 at three seeds). 1,000 trials a cell, 2,000 resamples, nominal 0.025 a side, standard
error about 0.005.

| seeds | sigma_b | median seed: above / below | crossed: above / below | width: median / crossed |
|---|---|---|---|---|
| 3 | 0 | 0.023 / 0.012 | 0.024 / 0.013 | 0.0536 / 0.0524 |
| 3 | 0.005 | 0.018 / 0.025 | 0.020 / 0.026 | 0.0534 / 0.0530 |
| 3 | 0.01 | 0.035 / 0.027 | 0.026 / 0.020 | 0.0540 / 0.0557 |
| 3 | 0.02 | **0.063 / 0.059** | 0.037 / 0.036 | 0.0556 / 0.0647 |
| 5 | 0 | 0.019 / 0.015 | 0.021 / 0.021 | 0.0531 / 0.0501 |
| 5 | 0.005 | 0.021 / 0.021 | 0.032 / 0.024 | 0.0533 / 0.0508 |
| 5 | 0.01 | 0.020 / 0.025 | 0.020 / 0.024 | 0.0539 / 0.0529 |
| 5 | 0.02 | 0.042 / 0.034 | **0.025 / 0.028** | 0.0549 / 0.0607 |

The 0.02 rows need churn of 0.063 to produce their effect, above the 0.037 to 0.053 the real nulls
showed, so they are a stress case rather than the observed regime.

**What it shows.**

- **With no seed effect the crossed interval costs nothing**: nominal, and no wider (narrower at
  five seeds, since averaging seeds shrinks the churn term).
- **The registered median-seed rule degrades as the seed effect grows**, to 0.122 two-sided at
  three seeds and sigma_b 0.02, two and a half times nominal. Reading one seed's interval cannot
  see a seed's shift, by construction.
- **The crossed interval holds nominal at five seeds** throughout, and at three up to sigma_b
  0.01. At three seeds and 0.02 it reaches 0.073 two-sided: resampling from three seeds understates
  their variance by a third, the few-clusters problem every cluster-robust method shares
  (MacKinnon and Webb). More seeds is the remedy the literature gives, not a cleverer resample.
- One cell sits above nominal on one side (five seeds, 0.005: 0.032, 1.4 standard errors). The
  same trial seeds gave 0.030 in an earlier pass, so that is one draw, not two.

**What it decides, once the real seed effect is measured** (sym-0 seeds 2 and 3, queued): register
the crossed interval as the gate's, retiring the median-seed rule; keep three seeds if the measured
sigma_b is at or below 0.01, and register five if it is above.

**Reviewed in three rounds; every finding reproduced and fixed** (PR #13 comment). The simulated seed
effect was under-sized by clipping; a merge accepted runs trained at different sizes as seeds of
one study; seed runs without a tag overwrote seed-1 results; results recorded the commit at write
time rather than the one the job ran, so a deploy mid-job would have mislabelled them. The last
is why jobs 148404 to 148408 run and record 497ebf2, and the next deploy waits for them.

### What an adapter update's direction reveals: initialization first, training length second, source barely (2026-09-18, job 148438)

`datasets/results/adapter-geometry-s1.json`, `scripts/adapter_geometry.py`. RQ2's per-client
question in its simplest form: is a LoRA update's direction in weight space closer to updates from
its own organization? The cosine between two updates dW = (alpha / r) B A, for every pair of the 35
saved adapters and all 196 adapted modules, computed through r x r traces without forming dW. Run
from a worktree pinned at e23b905 so the queued jobs' checkout was untouched; CPU only, three
minutes. Weight-space provenance is established for the training objective (Paul,
arXiv:2604.08844, AUC 1.00 from spectral features), which names "organic drift from distributional
shift", the same objective on different data, as untested. This is that case.

| pair | cosine |
|---|---|
| identical data and seed, two runs (marker-0 vs sym-0, halves a and b) | 0.964, 0.894 |
| identical data, different seed (sym-0 a, seeds 1 to 3) | 0.128 to 0.137 |
| same seed, disjoint OpenStack halves (sym-0 a vs b, seeds 1 to 3) | 0.132 to 0.142 |
| same seed, disjoint Qt projects, 1,517 each (qt-creator vs qtbase) | 0.135 |
| same seed, OpenStack half vs qt-creator or qtbase | 0.106 to 0.116 |
| same seed, OpenStack half vs qtdeclarative (788) | 0.133 to 0.140 |
| same seed, Qt projects at 788 (qtdeclarative vs either) | 0.212 |
| same seed, OpenStack vs Qt train windows, 4,327 each | 0.055, 0.056 |
| different seed and different data, same organization | 0.031, 0.032 |
| different seed, different organization (OpenStack s2 vs Qt s1) | 0.016 |

**Initialization decides most of it.** Retraining on byte-identical data at the same seed returns
nearly the same update (0.96), with training's own nondeterminism visible (0.89 on the other half);
a different seed on the same data returns 0.13. An observer comparing updates across seeds sees
almost nothing of the data. Federated rounds share an initialization, so the same-seed rows are the
realistic ones.

**Training length decides most of the rest.** Short runs stay closer together (0.21 at 788
examples) and long ones drift apart (0.055 at 4,327), whatever the source. The 0.055 between the
OpenStack and Qt windows is mostly length: at matched size the cross-organization cosine is 0.11.

**Source is a small term.** At matched size, same-organization pairs sit at 0.13 and
cross-organization at 0.11, a 0.02 margin smaller than the effect of training size, and an OpenStack
update is closer to a small Qt project's update than to its own sibling half. Two Qt projects align
exactly as much as two OpenStack halves, so even that margin cannot be separated from language:
OpenStack is Python, Qt C++.

**What it means for RQ2.** An update's raw direction is a weak witness to its source: a
nearest-neighbour attack on cosine would mostly recover seed and training length. RQ2's per-client
attack therefore has to be learned against reference updates at matched initialization and size,
which is FedAttr's design (paired subsets with and without the target), not a similarity lookup. The
null here is informative in its own right: the privacy question is not answered by the geometry
alone, and a defence argued from "updates look alike" would be arguing from the wrong quantity.

Caveats: one seed of the organization-window pair beyond the first; halves and projects overlap
the train windows they were drawn from, so window-versus-subset rows share data; cosine over all
modules pools layers whose roles differ, and a per-layer or spectral reading (Paul's features)
could separate what the pooled cosine does not.

### Training size does not explain the pairwise pattern (2026-09-18, job 148408)

`datasets/results/projects-qt-creator_qt-creator-qt_qtbase-n788.json`. qt-creator vs qtbase rerun at
788 training examples per side (`TRAIN_SIZE=788`, a nested subsample of the 1,517), the size the two
qtdeclarative pairs trained at. Same held-out changes, one seed.

| qt-creator vs qtbase | qt-creator side | qtbase side |
|---|---|---|
| 1,517 per side (148203) | +0.079 [+0.041, +0.118] | -0.019 [-0.046, +0.008] |
| 788 per side (148408) | **+0.044 [+0.013, +0.077]** | -0.003 [-0.035, +0.031] |

At the same 788 examples where qt-creator showed no advantage over qtdeclarative (+0.005), it still
beats qtbase. The difference between the pairs is the pair, not the training size, which confirms
the reading of the earlier entry: qt-creator's refinements carry something qtbase's adapter lacks
and qtdeclarative's has. The effect roughly halves with half the data, as a learned property
should, and qtbase still never wins at home.

### RQ2's first result: a client's update reveals its organization, within a language too (2026-09-18, jobs 148513, 148621)

> **Corrected the same day.** The interpretation below overreaches. Per project there is no
> organization pull beyond the codebase family: see "What the client updates identify is the
> codebase family" below. The numbers stand; the organizational reading does not.

`datasets/results/client-updates.json`, `client-geometry.json`, `client-attribution.json`. The
geometry of the RQ1 adapters said an update's raw direction is dominated by initialization and
training length. So this holds both fixed, as a federated round does: 34 clients of exactly 64
examples, disjoint by change, each restored to one identical initial LoRA state (checked) and
trained 8 steps on Qwen2.5-Coder-7B-Instruct. Update norms land in a band of 3.54 to 3.76.
Sources are eight projects labelled organization, content and project, so organization and
language can be told apart: Python in both organizations (OpenStack nova, neutron, ironic; Qt
pyside-setup), documentation in both (starlingx/docs, qtdoc), C++ in Qt (qt-creator, qtbase). The
content labels are majorities, not purities: the Python projects are 69 to 75% `.py`, starlingx/docs
99% `.rst`, qtdoc 67% `.qdoc`. Run from worktrees pinned at ec7cb80 and fbefafb.

The attack is the simplest learned one: an attacker holding reference updates from each candidate
assigns a new update to the class whose other members it aligns with most (mean cosine, leave one
out). 10,000 label permutations each.

| attributing | classes | accuracy | majority | p |
|---|---|---|---|---|
| **organization** | 2 | **0.912** (31 of 34) | 0.647 | 0.0001 |
| project | 8 | 0.500 | 0.176 | 0.0001 |
| content | 3 | 0.647 | 0.353 | 0.0005 |
| **organization, Python clients only** | 8 vs 4 | **0.917** | 0.667 | 0.012 |
| **organization, documentation only** | 4 vs 6 | **0.900** | 0.600 | 0.014 |

| relation between two clients | mean cosine | pairs |
|---|---|---|
| same organization and content, other project | 0.264 | 57 |
| same project | 0.260 | 64 |
| other organization and content | 0.228 | 208 |
| same organization, other content | 0.219 | 176 |
| other organization, same content | 0.215 | 56 |

**The update carries its organization, and not through its language.** Restricted to one kind of
content, where both organizations contribute and language cannot carry it, organization is still
attributed at 0.90 and 0.92. Clients from different organizations writing the same kind of content
are the least aligned pairs of all.

**At this altitude the signal is organizational, not per project.** Two clients from different
projects of one organization align as closely as two from the same project (0.264 against 0.260),
and more than same-content clients across organizations (0.215). This is the reverse of what RQ1's
generative contrast and the separability probe suggested, where the project looked like the unit.
The two instruments measure different things: the contrast asks whether an adapter *performs*
better at home, the geometry whether updates *look* alike. An organization can leave a mark on how
its adapters move without that mark buying held-out exact match.

**Where it lives.** Per-module attribution rises toward the output: median organization accuracy
0.794 over layers 0 to 7 and 0.926 over layers 24 to 27; project 0.353 against 0.588. (The best single
module reaches 1.000, but that is the maximum of 196, so it is not evidence.)

**Caveats, in order of weight.**

- **The permutation p-values treat clients as exchangeable, and clients share their project.** The
  organization claim rests on eight projects, and within Python the Qt side is one project, within
  documentation both sides are, so the within-content rows are also between-project tests. A
  project-level test, the unit that can support an organizational claim, needs more projects per
  organization than this corpus's train window offers in matching content.
- One initialization and one client size (64 examples, 8 steps). Longer local training moves
  updates apart (the RQ1 geometry), and whether the signal survives it is untested.
- Per-client updates, no secure aggregation. The aggregate threat model is the next question
  (FedAttr's paired-subset design with the watermark removed).

**Against the state of the art.** Weight-space provenance was shown for the training objective
(Paul, arXiv:2604.08844), which named same-objective different-data drift as untested; source
attribution under secure aggregation was shown for deliberate watermarks (FedAttr,
arXiv:2605.06596). This is the untested case, same objective and natural data, and the answer at
this scale is that an organization's own conventions are enough.

### What the client updates identify is the codebase family, not the organization (2026-09-18)

Corrects the interpretation of the entry above, whose tables stand. The organization accuracy of
0.912 is a statement about class means, and class means inherit the class's composition. Per
project, measured from the same cosines:

| from | toward | relation | mean cosine |
|---|---|---|---|
| pyside (Qt, Python) | Qt C++ | same organization | 0.215 |
| pyside | OpenStack Python | same language | 0.212 |
| pyside | Qt docs | same organization | 0.181 |
| starlingx/docs (OpenStack) | OpenStack Python | same organization | 0.227 |
| starlingx/docs | Qt docs | same content | 0.219 |
| qtdoc (Qt) | Qt C++ | same organization | 0.230 |
| qtdoc | OpenStack docs | same content | 0.219 |
| nova | neutron, ironic | same organization and language | **0.274** |
| qt-creator | qtbase | same organization and language | **0.263** |
| OpenStack Python | Qt C++ | neither | 0.252 |

Qt's one Python project is no more Qt-like than Python-like; it is an island. The documentation
projects lean toward their own organization by 0.01 at most. What stands out is a **family**: the
OpenStack Python services align with one another as closely as clients of one project do, and so
do Qt's two C++ libraries. Neither organization alone nor language alone raises alignment; the
two together do.

So the within-content rows of the entry above are family tests in disguise: within Python the Qt
side is one project, within documentation each side is one project, and a project is recognisable
from its update. **The defensible claim is that a client's update identifies its codebase family.**
Whether the organization is what defines a family, a house style shared across the organization's
projects in one language, cannot be separated here, because each organization-and-language cell
holds one to three projects and the only cross-organization comparison within a language rests on
one Qt Python project.

**This agrees with the separability probe and with RQ1's project contrasts**, both of which put the
signal at the codebase rather than the organization. Three instruments now point the same way.

**What would separate them.** Several projects per organization *in one shared language*, on both
sides. The train and dev windows offer that only for OpenStack Python (starlingx/test, config,
distcloud, swift, nova, neutron, ironic); Qt's Python is pyside alone. A third Gerrit organization
with several Python projects (Wikimedia's hosts pywikibot and many Python tools) would give the
cross-organization, same-language, multi-project cell this design lacks.

### AOSP as RQ2's third organization, and why it can only ever be RQ2's (2026-09-18)

To separate an organization from its codebase family, RQ2 needs several projects per organization
in one shared language on both sides. Qt's C++ libraries have that; a second organization with
several C++ projects on public Gerrit supplies the other side. AOSP (android-review.googlesource.com)
does: seven C++-dominant projects (art, system/core, bionic, frameworks/native, frameworks/av,
external/perfetto, system/netd), about 40% of merged changes carrying inline comments in a sample
of thirty. It answers residential addresses and refuses datacenter ranges, so it is fetched from a
workstation, and `fetch --project` bounds it and records the restricted query in the snapshot.

**Its public review record ends on 2025-03-27.** The fetch shows 547 to 753 merged changes a month
for these projects through 2025-03, then 2 to 9. Google moved Android development to internal
branches that week and made aosp-main read-only (9to5Google, 2025-03-26; Android Authority).
AOSP therefore has no dev or test window under RQ1's registered windows and cannot join RQ1. RQ2's
client design needs no time split, so its months 2024-11 to 2025-03 serve, extended back to 2024-01
for volume.

**The same filters, a different mix of what they drop.** Per month AOSP yields about 220 examples
against OpenStack's 458 and Qt's 880. The drop profile differs in composition rather than in kind:
comments by the change's own author account for 40% of AOSP's drops against 21 to 24% elsewhere,
metadata files for 30% against 39 to 55%, unanchored comments for 19% against 12 to 30%. Android
reviewers and authors work in the review thread more, which the author filter removes on all three
organizations alike.

### The seed main effect, measured: about 0.013, and it is what limits power (2026-09-18, jobs 148199, 148405, 148407)

`datasets/results/seed-effect-sym-0.json`, `scripts/seed_effect.py`. The null `sym-0` at seeds 1, 2
and 3, same corpora, split and held-out examples. Per organization half, the contrast at each seed,
and per pair of seeds the shift against the change-clustered noise of the per-example difference
(10,000 resamples), which is what seed-by-change churn alone would produce.

| half | seed 1 | seed 2 | seed 3 |
|---|---|---|---|
| a | -0.0356 | -0.0200 | -0.0067 |
| b | -0.0137 | +0.0252 | +0.0137 |

Pairwise z against churn alone: +0.88, +1.66, +0.76 on a; +2.50, +1.70, -0.75 on b. Between-seed
variance 0.000305, of which churn accounts for 0.000139, leaving **sigma_b^2 = 0.000166, sigma_b =
0.0129**. Three seeds and two halves is little to estimate a variance from, and this is the null at
about 1,800 training examples, not RQ1's windows at 4,327; RQ1's own seeds 2 and 3 (jobs 148404,
148406) give the estimate in the study's setting.

**The registration rule fixed in advance gives five seeds**: the seed effect exceeds 0.01, past which
three seeds under the crossed interval do not hold nominal.

**It also decides power, which the rule did not address.** Conjunctive power at the projected test
window (`conjunctive-power-s*-b*.json`, 300 trials each), for the effects the windowed run showed:

| seeds | sigma_b | crossed interval | median-seed rule |
|---|---|---|---|
| 3 | 0 | 0.790 | 0.770 |
| 3 | 0.005 | 0.600 | 0.670 |
| 3 | 0.01 | 0.387 | 0.573 |
| 5 | 0 | 0.820 | 0.773 |
| 5 | 0.005 | 0.690 | 0.723 |

A seed effect of this size is the size of the organizational effects themselves (+0.011 to +0.034),
so an honest interval must carry it, and power falls with it. The median-seed rule's higher figures
are what ignoring it buys, the same excess its coverage showed. **Seeds, not test changes, are now
the lever on power**: the seed component shrinks only as 1/S. Cells at sigma_b 0.013 for five, seven
and ten seeds are running, and the registered seed count becomes the smallest giving conjunctive
power of 0.80 at the seed effect measured in RQ1's own setting.

**This corrects the morning's correction.** "The run-to-run swing is noise the interval already
carries" was right about the two same-seed reruns, which differ only by GPU nondeterminism. Across
seeds the contrast moves by more than churn explains, so the seed main effect is real, and the
crossed interval is needed for the reason first given, at a size now measured rather than
inferred.

### Review of the RQ2 and power code: one packing flaw, four small ones (2026-09-18)

Commits 38936f3 to 405462b reviewed, every finding reproduced and fixed. The one that touches a
result: `clients.partition` could get stuck behind a client no remaining change completed, losing
every client after it, so client counts depended on the shuffle. The executed run (148513) was
affected in count only, its clients disjoint and exactly sized: starlingx/docs gave 4 where 5 are
possible. The packer is now first-fit decreasing over open clients, which reaches the maximum for
every corpus but starlingx/docs (5 of 6, where very large changes cannot pack exactly). The next
client run, with AOSP added, uses it. The others: a skipped micro-batch now stops a client as a
skipped step does, since it leaves the client short; `conjunctive_power` refuses a seed effect with
one seed instead of recording one it did not simulate; `seed_effect` no longer crashes on a pair
with no noise; adapter geometry skips the zero-norm initial state.

### Organization beyond project, tested with the project as the unit (2026-09-18)

The client-level permutation treated clients as exchangeable, and clients share their project. The
honest test leaves each client's own project out of every class mean, so an organization has to be
recognised from its *other* projects, and permutes organization labels across projects, not
clients (`attribution.group_permutation_p`, exact over all distinct relabelings when few enough).
On the 34 clients of job 148513:

| scope | projects | accuracy | p |
|---|---|---|---|
| all clients | OpenStack 4, Qt 4 | 0.794 | 0.057, exact over 70 relabelings |

Suggestive and not significant, and eight projects cannot give much less than this. No single
content type has two projects on both sides, so the language-controlled version cannot run here.
The AOSP and Qt C++ cell (seven and six projects, 1,716 relabelings) is built for exactly this test.

**The review of the packing fix caught a confound in the fix itself.** First-fit decreasing opens
clients in order of change size, so keeping the first `limit` kept the clients built around each
project's largest changes: one change and one reviewer each for a project with big changes, twenty
small changes for another. The attack could have read the packing instead of the source. Clients
now take at most a quarter of their examples from any one change, and the `limit` kept are a seeded
random draw from all full clients. The executed run predates both packers' flaws only in count;
the next run is the first under the corrected one.

### RQ1 at three seeds, and the seed effect in the setting that counts (2026-09-18, jobs 148198, 148404, 148406)

`datasets/results/rq1-qtfull-seeds.json`, `seed-effect-qtfull.json`. The windowed RQ1 run repeated
at seeds 2 and 3, same corpora, split and held-out examples, read by `crossed_reread`.

| seed | OpenStack | Qt |
|---|---|---|
| 1 | +0.0159 | +0.0337 |
| 2 | +0.0177 | +0.0263 |
| 3 | +0.0177 | +0.0337 |

| reading | OpenStack | Qt | verdict |
|---|---|---|---|
| median seed, pooled (registered) | +0.0177 [-0.0099, +0.0439] | +0.0337 [+0.0089, +0.0600] | mixed |
| crossed, pooled | +0.0171 [-0.0061, +0.0403] | +0.0312 [+0.0080, +0.0565] | mixed |
| median seed, change-averaged | +0.0290 [-0.0131, +0.0724] | +0.0354 [+0.0062, +0.0645] | mixed |
| crossed, change-averaged | +0.0300 [-0.0085, +0.0690] | +0.0312 [+0.0041, +0.0594] | mixed |

**All four readings agree: mixed.** Qt's refinements carry an organization-specific gain that clears
zero on three seeds and both estimands; OpenStack's do not. The dev-window contrast is stable
across seeds, and the earlier single-seed numbers hold.

These are dev-window numbers, and the seal right-censors that window: 39.1% of OpenStack's and
28.8% of Qt's changes in it were still open when collection closed, a 10.3-point differential. The
test window at its registered fetch horizon carries 1.1% and 0.7%, so the confirmatory contrast is
not read under this distortion, and a dev result that depends on slow reviews would not survive
into it.

**The seed main effect in this setting is indistinguishable from zero**: the per-seed contrasts vary
by 0.0008 (OpenStack) and 0.0035 (Qt), every pairwise shift sits within seed-by-change churn (|z| at
most 0.64), and the moment estimate is negative, with a one-sided 95% upper bound of **0.0098** on two
degrees of freedom. Seeds 1 and 3 happen to give identical contrasts, which is coincidence, not a
repeated run: their adapters differ (final losses 0.048 against 0.68, distinct norms) and they share
only 309 of 565 predictions.

**This narrows an earlier claim.** "Seeds are the lever on power" came from the null's sigma_b of
0.013, measured on 1,800 training examples. RQ1 trains on 4,327, where the effect is smaller than
the measurement can see. Longer training converging more consistently is the plausible reason, and
the two settings are now both on record.

**By the rule fixed before any of these runs landed, the registered design takes three seeds**, and
the crossed interval, which costs nothing at this seed effect and holds nominal to 0.01 where the
median-seed rule does not. The power figures computed at sigma_b 0.013 stand as the worst case,
not the expected one.

### Inference numerics registered: fp32 (2026-09-18, jobs 148667, 149196)

`datasets/results/determinism-sym-0-gh-a-081.json`, `-gh-a-003.json`. The base model over the same
150 prompts, four passes a job, two jobs on different nodes.

| comparison | predictions differing, of 150 |
|---|---|
| fp32, the two jobs | **0** |
| bf16, the two jobs | 5 to 7 |
| bf16, repeated inside one job | 0 to 2 |
| bf16, a reloaded model inside one job | 1 to 2 |
| bf16 against fp32 | 17 to 19 |

**bf16 greedy decoding does not reproduce, even within one process**, which is what the two nulls'
disagreement was. **fp32 reproduces exactly across jobs and nodes**, matching Yuan et al.
(arXiv:2506.09501v2, read from the paper): "FP32 precision consistently achieves near-perfect
reproducibility with negligible variance, FP16 shows moderate variability, while BF16 exhibits
substantial instability", and their recommendation, "If using greedy decoding with a single run,
please use FP32 precision to improve the reproducibility of your results". Their LayerCast keeps
weights in bf16 and computes in fp32 to save memory; this upcasts the weights as well, which a
GH200 has room for at 7B, and is the stronger of the two. The earlier reading that
the node was the variable is wrong: a fresh job on the stored run's own node differs from it as much
as a job on another node.

`HFGenerator` now upcasts the bf16 weights to fp32, exactly, before generating, and every result
records `inference_dtype`. This is the rule fixed in the ROADMAP before the jobs ran: fp32 if bf16
differs between nodes and fp32 does not.

### Secure aggregation does not hide which source was in the round (2026-09-18)

`sphragis/measure/aggregate.py`, `scripts/aggregate_attack.py`,
`datasets/results/aggregate-attack.json`. The per-client attack assumes the server sees each
update; secure aggregation shows it only the round's mean, which is what a federated deployment
would claim as its protection. Both detectors below are what an honest-but-curious server could
run with reference updates of its own, computed exactly from the Gram matrix `adapter_geometry`
already recorded, without touching the weights.

| target | rounds of 4 | rounds of 8 | paired difference at 8: own | a stranger's |
|---|---|---|---|---|
| OpenStack | AUC 0.729 | AUC 0.750 | +0.038 | -0.002 |
| Qt | AUC 0.650 | AUC 0.732 | +0.016 | -0.002 |

**Membership**: rounds of one size, half holding exactly one client of the target and half none,
scored by the round's alignment with the attacker's reference updates from that source, the
detected client never among them. 0.5 is no signal.

**Paired difference**: FedAttr's mechanism (arXiv:2605.06596) with the watermark removed, the
average of rounds holding the target minus the average of rounds without it, read against the
source's direction. It recovers the target's own direction and nothing for a client of another
organization, which is the baseline it has to beat.

**Bigger rounds score no worse.** A larger round dilutes the target's share as 1/size, but averages
the other participants' noise away at the same rate, so the alignment holds: 0.729 to 0.750 for
OpenStack, 0.650 to 0.732 for Qt.

**What it does not yet show.** The reference is the rest of the target's clients, which in this
corpus means the rest of its codebase family, so this inherits the per-client result's open
question: the detector may be reading the family rather than the organization. The rounds are drawn
from one source against all others rather than from a realistic mixed cohort, one client of the
target a round, and the client updates are a single initialization and a single local-training
length. What it does establish is the shape of the leak: aggregation over four to sixteen clients
does not remove it, and the mechanism that finds a planted watermark finds a natural source too.

### Mixed rounds, and a detector that was reading its own reference (2026-09-18)

Extends the entry above to the deployment question: not "was this named client in the round" with
the other participants drawn from other organizations, but "did this organization take part" with
the round drawn from every client. The organization's clients are split once into the attacker's
reference and the participants it may contribute, so no round is scored against itself.

| target | round of 4 | round of 8 | round of 16 |
|---|---|---|---|
| OpenStack, 1 of its clients present | AUC 0.765 | 0.726 | 0.725 |
| OpenStack, 2 of its clients present | 0.918 | 0.835 | 0.863 |

Detection rises with how many of the organization's clients are in the round and falls only
slightly as the round grows, which is what a mean over participants predicts. Qt is not reported:
with 22 of the 34 clients it leaves too few outsiders to fill a round of sixteen without it.

**First run of this said AUC 1.000, and that was a defect in the detector.** The two classes were
scored against references of different sizes, eleven clients for rounds holding the target and six
for rounds without, and a larger reference has a more central direction, so the comparison read
reference size rather than membership. One fixed reference for both classes gives the numbers
above. A test now pins the split.

### What the design can detect: a sensitivity analysis, replacing power at an observed effect (2026-09-18)

`datasets/results/sensitivity-b0.0098.json`, `scripts/sensitivity.py`. The test window's size is
fixed by what exists, so the Stage 1 question is not "what is the power" but "what is the smallest
effect this design detects". Power computed from a pilot's own estimate is biased upward, the more
so when the pilot looked promising (Albers and Lakens, JESP 2018; Lakens, Collabra 2022), which is
why the 0.833 figure is withdrawn rather than updated.

Simulated at the projected test-window sizes, under the crossed interval, at the seed main effect's
one-sided 95% upper bound of 0.0098, with marginal power 0.894 per organization so the conjunctive
gate reaches about 0.80:

| organization | changes | three seeds | five seeds |
|---|---|---|---|
| OpenStack | 2,171 | **+0.0235** | +0.0210 |
| Qt | 3,937 | **+0.0211** | +0.0179 |

**The design detects about two exact-match points per organization.** Two more seeds buy 0.002 to
0.003, which is why three is registered: the seed term is already small beside the change term at
these window sizes.

**Bracketed by the seed effect's own uncertainty** (`sensitivity-b0.json`), since three seeds
estimate a variance poorly and the honest statement is a range rather than a figure:

| seed main effect | OpenStack | Qt |
|---|---|---|
| 0.000, the point estimate | +0.0160 | +0.0129 |
| 0.0098, the 95% upper bound | +0.0235 | +0.0211 |

So the design resolves somewhere between 1.3 and 2.4 exact-match points. Qt's dev-window effect
(+0.031) is above the whole range and OpenStack's (+0.017) inside it, which is why one organization
clears zero and the other does not, and why the Stage 1 report should state the range.

**Read against the dev window, this predicts the outcome we have.** Qt's dev-window effect is
+0.031, above its threshold; OpenStack's is +0.017, below its own. The gate came out mixed on
exactly that pattern, and a test window that behaves like the dev window would return mixed again.
That is a statement about what the study can resolve, and it is better made now, in the Stage 1
report, than discovered afterwards.

### Exact match earns its registration: the quieter metrics lose more signal than noise (2026-09-18)

The sensitivity analysis puts the design's resolution at about two exact-match points, which is
where the observed effects are, so the obvious lever is a less noisy outcome. Exact match is
binary and maximally variable; the run already records a normalised exact match and an edit
similarity. Read over the three seeds under the crossed interval, on the same clusters:

| metric | OpenStack: effect, half-width, ratio | Qt: effect, half-width, ratio |
|---|---|---|
| exact match (registered) | +0.0171, 0.0232, **0.74** | +0.0312, 0.0243, **1.28** |
| normalised exact match | +0.0183, 0.0247, 0.74 | +0.0298, 0.0255, 1.17 |
| edit similarity | -0.0003, 0.0117, 0.03 | +0.0059, 0.0092, 0.64 |

**Edit similarity halves the interval and costs five-sixths of the effect.** An adapter trained on
the right organization is not generally closer in string distance to the reference; it is more
often exactly right. The organizational gain lives in the last token as much as the first, which is
what an exact-match criterion is for and what a graded string distance averages away.

So the registered metric is the one the data supports, not merely the one chosen first, and the
resolution limit is a property of the question rather than of the instrument. The change-averaged
estimand does not help either: its Qt interval is wider relative to its effect (0.77 against 1.28).

### A secondary estimate pooled across organizations (2026-09-18)

The conjunctive gate asks whether *each* organization shows the effect, which is what a generality
claim needs and what costs the design its resolution. A pooled estimate answers the weaker question,
whether organizations leave a fingerprint on average, and is the sharpest reading the same data
supports. Over the three seeds, every change from both organizations as one cluster set:

| reading | estimate | interval | effect over half-width |
|---|---|---|---|
| OpenStack alone | +0.0171 | [-0.0057, +0.0407] | 0.74 |
| Qt alone | +0.0312 | [+0.0079, +0.0565] | 1.28 |
| **both pooled, 697 changes** | **+0.0260** | **[+0.0090, +0.0440]** | **1.48** |

**It is a secondary, never the gate.** Pooling treats the two organizations as one population of
changes, so a single organization with a strong effect can carry it, which is the failure mode the
conjunctive rule exists to prevent; and with two organizations there is no way to model
between-organization heterogeneity rather than assume it away. Registered as a secondary estimate,
reported beside the gate, and fixed now while the test window is sealed.

### Why one organization shows the effect and the other does not (2026-09-18, exploratory)

The dev-window contrast broken down by the project each held-out change belongs to, pooled over the
three seeds. Exploratory, on the dev window, with small per-project samples and no multiplicity
correction: a description of where the organizational average comes from, not a test.

| Qt, by project | contrast | examples | | OpenStack, by project | contrast | examples |
|---|---|---|---|---|---|---|
| qt-creator | +0.021 | 291 | | starlingx/docs | +0.041 | 138 |
| qtbase | +0.030 | 135 | | starlingx/test | -0.050 | 47 |
| qtdeclarative | +0.017 | 98 | | starlingx/distcloud | -0.078 | 34 |
| qtdoc | +0.012 | 81 | | openstack/kayobe | +0.027 | 25 |
| qtopenapi | +0.023 | 57 | | starlingx/update | +0.042 | 24 |
| qtmultimedia | +0.000 | 56 | | openstack/ironic | -0.030 | 22 |

**Qt's effect is spread across its projects**; every project with more than fifty examples is at or
above zero. **OpenStack's projects disagree in sign** and average to nothing.

This is the same picture RQ2's client updates gave, from the generative side. OpenStack's Gerrit
hosts a federation of loosely coupled projects, StarlingX and Zuul among them, whose conventions
need not agree; Qt's projects are one house. So "organization" is a useful boundary exactly when
the organization's projects share conventions, and the gate's mixed verdict is not two noisy
measurements of one quantity but two different situations. RQ1's registered claim, that
organizations leave a learnable fingerprint, is therefore too strong as stated for a federation and
about right for a house, which is a finding rather than a failure, and it is what the conjunctive
rule was built to expose.
