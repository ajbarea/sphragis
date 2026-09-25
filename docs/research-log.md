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

> **Corrected the same day, and again with a third organization.** The interpretation below
> overreaches: per project there is no organization pull beyond the codebase family, and once a
> content type is carried by two organizations at several projects each, organization is not
> attributable at all (0.416 against a 0.506 majority). See "What the client updates identify is the
> codebase family" and "The organization is not in the update" below. The numbers stand; the
> organizational reading does not.

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
from its update. (The C++ cell built to settle this later returned 0.395 against a 0.698 baseline:
no organization effect at all beyond content and project.) **The defensible claim is that a client's update identifies its codebase family.**
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

Averaged over eight random reference splits, each drawn from a stream independent of the rounds,
with the standard deviation across splits:

| target | round of 4 | round of 8 | round of 16 |
|---|---|---|---|
| OpenStack, 1 of its clients present | AUC 0.659 (sd 0.070) | 0.688 (0.062) | 0.753 (0.086) |
| OpenStack, 2 of its clients present | 0.771 (0.138) | 0.828 (0.090) | 0.887 (0.084) |
| Qt, 1 of its clients present | 0.668 (0.061) | 0.721 (0.051) | not measurable |
| Qt, 2 of its clients present | 0.765 (0.063) | 0.852 (0.075) | not measurable |

Detection rises with how many of the organization's clients are in the round, and rises with the
round rather than falling: a larger round dilutes the target's share and averages the other
participants' noise away at the same rate. Qt's rounds of sixteen are not measurable here, since
with 22 of the 34 clients it leaves too few outsiders to fill one without it.

**The ordering is reliable and the difference is small.** Behind OpenStack's 0.887 the two classes'
mean scores are 0.7151 and 0.7065: one part in a hundred. An aggregate does not shout its
participants; it leans, consistently, and the next entry is what that leaning is worth to an
attacker who watches more than one round.

(The first run of this table read 0.765, 0.726 and 0.725 on OpenStack's one-client row, from a
single reference split taken in listing order; see the correction below.)

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

### The privacy LoRA provides by design is the wrong privacy for an organization (2026-09-18)

Malekmohammadi and Farnadi, "LoRA Provides Differential Privacy by Design via Random Sketching"
(arXiv:2409.17538, revised 2026-02-10), prove that low-rank adaptation is equivalent to training
with noisy batch gradients, the noise decreasing in the rank, and derive an inherent differential
privacy guarantee when the adaptation matrices A are frozen, with the level set by rank and batch
size. They offer it as the reason LoRA-tuned models resist privacy attacks.

**That guarantee is about examples, and RQ2 is about sources.** Differential privacy at the example
level bounds what an adversary learns about whether one training example was used. It does not
bound what an update reveals about *where its data came from*: an organization's conventions are a
property of the distribution, shared across every example, and a mechanism that hides each example
individually can leave the distribution entirely legible. The attacks measured here read exactly
that residue, and they work on updates whose A matrices were trained rather than frozen, so the
paper's regime is not even the one they run in.

**This is the sharpest positioning RQ2 has.** LoRA-Leak (arXiv:2507.18302) attacks membership of
examples; FedAttr (arXiv:2605.06596) attributes a deliberate watermark; Malekmohammadi and Farnadi
bound example-level leakage by design. None of them asks whether an update betrays its source, and
the answer here is that it does, through secure aggregation, without a watermark, at the altitude a
data-governance policy is actually written at.

### Capacity and placement: where the source signal sits agrees with where adapters store things (2026-09-18)

Tan, Du and Feng, "How Many Bits Can an Adapter Write?" (arXiv:2607.21351, 2026-07-23), measure how
much a LoRA adapter can hold: 1.7 to 2.8 bits a trainable parameter against full fine-tuning's 3.6,
with memorization varying nearly twofold by **placement**, MLP against attention, at matched
parameter counts. They measure total information stored, not where it came from, and report that
membership inference "did not resolve the difference between these adapters".

Two things follow for RQ2. Their placement finding matches ours from the other side: per-module
attribution here is highest in late MLP projections (median organization accuracy 0.926 over layers
24 to 27 against 0.794 over layers 0 to 7, the single best module being layer 27's MLP gate
projection). Where an adapter stores most is where its source is most legible, which is one
statement rather than two coincidences.

And their negative result frames ours. Membership inference failed to separate their adapters;
source attribution separates ours. The quantity that survives is not which examples were used but
which distribution they were drawn from, which is also the quantity a data-sharing agreement
between organizations is written about. Their proposed next step, measuring an adapter's capacity
before it is shared rather than after it is attacked, is the same instinct as RQ2's, one altitude
up.


### The reference split was following the order clients arrive in (2026-09-18)

Found by reading the defence curve rather than the code: Qt's detection ran *below* chance and fell
further with noise (0.386 at four times the update norm, 0.336 at sixteen), which no amount of
masking explains. A detector that is anti-correlated is measuring something other than membership.

The attacker's clients were split into reference and participants by taking the first half of a
sorted list. Clients arrive grouped by project, so for an organization spanning several projects,
Qt's C++, documentation and Python, the reference came from different projects than the
participants: the score then measured the distance between two projects, and rounds holding the
target scored lower than rounds without it. The split is now random, seeded, in both the Gram-based
attack and the vector-space defence curve, and a test builds an organization of two projects in
listing order and fails if the detector runs backwards.

The corrected mixed-round figures are in the entry above. What the bug did not touch: the per-client
attribution, which uses every other client rather than a split, and the paired-subset difference,
which holds a target out by name.

### What RQ2 is about, in the words of the people proposing it (2026-09-18)

Federated fine-tuning of code models across organizations is not a hypothetical RQ2 invents. Luo et
al., "When Fine-Tuning LLMs Meets Data Privacy: An Empirical Study of Federated Learning in
LLM-Based Program Repair" (arXiv:2412.01072, in TOSEM), federate program repair over as many as 100
simulated clients with QLoRA and FedAvg, uploading adapter parameters to a central server. Their
motivation is stated plainly: federated learning "facilitates private entities to utilize their data
collaboratively, while addressing the concern of data privacy by learning a model without exposing
the raw data of each client", and "Data privacy is also protected by not exposing local data to each
participating client". They name differential privacy and secure aggregation as possible additions
and implement neither, and they state no adversarial threat model. The same setting appears for
code translation between proprietary entities (arXiv:2501.05724).

**That is the claim RQ2 tests, and the claim is about raw data.** What the server receives is the
adapter, and this log's measurements say an adapter identifies where its data came from, through
secure aggregation, with no watermark, at the altitude a data-sharing agreement is written at. The
gap is not that these papers are wrong about raw data staying home; it is that "the data never
leaves" and "nothing about the data leaves" are different sentences, and only the first is
demonstrated.

### The aggregate betrays the project more clearly than the organization (2026-09-18)

`datasets/results/aggregate-attack-project.json`. The same mixed-round detector, with the target a
project rather than an organization: rounds drawn from every client, half holding one of the
target's clients, scored against the target's other clients, eight reference splits.

| target project | clients | AUC, rounds of 4 | paired difference: own, a stranger's |
|---|---|---|---|
| qt/pyside-setup | 4 | 0.821 | +0.053, +0.006 |
| openstack/starlingx-docs | 4 | 0.814 | +0.059, -0.023 |
| openstack/neutron | 3 | 0.741 | +0.077, +0.012 |
| openstack/nova | 3 | 0.721 | +0.083, +0.014 |
| qt/qtbase | 6 | 0.706 | +0.046, -0.023 |
| qt/qt-creator | 6 | 0.643 | +0.036, +0.037 |
| qt/qtdoc | 6 | 0.627 | +0.036, +0.005 |
| openstack/ironic | 2 | 0.476 | -0.009, +0.022 |

Against the organizations' 0.659 to 0.753 on the same rounds, most projects are detected more
clearly than either organization that contains them, and the two most distinctive, pyside-setup and
starlingx/docs, are the ones whose content sets them apart from their organization's other
projects. ironic, with two clients and so a one-client reference, is at chance.

**This is the per-client result seen through secure aggregation.** What a round leaks is the
codebase; the organization is legible only in so far as its projects resemble each other. A policy
that treats the organization as the unit of disclosure is protecting the wrong boundary in both
directions: it over-promises for a federation whose projects are separately identifiable, and it
under-describes a house whose projects all carry the same hand.

### A fingerprint measured in a moving medium (2026-09-18)

Xu et al., "code_transformed: The Influence of Large Language Models on Code" (arXiv:2506.12014,
revised 2026-02), measure style across more than 20,000 GitHub repositories linked to arXiv papers
from 2020 to 2025 and report conventions shifting toward what models write: snake_case function
names in Python rise from 40.7% in Q1 2023 to 49.8% in Q3 2025. They also measure similarity across
projects; whether they claim convergence is not something the abstract settles and the full text has
not been read.

Two consequences for this study, neither yet a measurement here.

**Timeliness.** If model-written code is spreading through the corpora, an organization's own hand
is being overwritten while the study measures it. That makes the measurement worth making now and
makes any null harder to read: a fingerprint that has faded is indistinguishable from one that was
never there.

**A threat to the train-to-test transfer.** RQ1 trains on 2024-11 to 2025-09 and will test on
2025-11 to 2026-10, a year later, in exactly the period this drift is measured over. An adapter
learns the hand its training window carried; if the test window's hand has moved toward the models',
the contrast is attenuated by drift rather than by an absence of fingerprint. The apparatus can
already say something about this without unsealing anything: the dev window sits between the two,
and the per-project breakdown could be recomputed on the earliest and latest months of the train
window to see whether the contrast is shrinking with time.

### The medium is not visibly moving, at least not the part the probe reads (2026-09-18)

`scripts/separability_over_time.py`, `datasets/results/style-drift.json`. Two readings of whether
the corpus drifts under the study, prompted by the naming-convention shift Xu et al. measure.

**The cross-organization series is not available here, and that is worth stating.** Holding content
fixed needs a suffix both organizations carry, and the only one is Python, which Qt stops writing
during the window: 39 to 122 examples a month in late 2024 against 0 to 31 through 2025, with two
quarters at zero. The accuracies that come out (0.880, then 0.656, then 0.724) track that collapse,
not the organizations' hands, and no other suffix reaches fifteen examples a month on both sides in
more than two months. A time series of organizational separability is simply not measurable on this
corpus.

**Within each organization, its earliest quarter against its latest is at chance**, on its own
dominant suffix, whole changes held out together:

| organization | 2024-10 to 12 against 2025-08 to 10 | changes | accuracy |
|---|---|---|---|
| OpenStack (.py) | ten months apart | 369 | 0.552 [0.466, 0.602] |
| Qt (.cpp) | ten months apart | 986 | 0.510 [0.487, 0.572] |

Both intervals cover 0.5. Over the span that separates RQ1's training window from its test window,
the review text a classifier can read does not identify which end of the year it came from.

**The code half is at chance too** (`style-drift-code.json`). `probe.code_shapes` reads the
refinement itself as convention shapes rather than identifiers, since identifiers name a project's
subject matter while shapes are how it writes them: snake_case, camelCase, PascalCase, SCREAMING
case, dunders, arrows, scope resolution, f-strings, braces, line-final semicolons, each occurrence
one token. On the same quarters:

| organization | changes | accuracy |
|---|---|---|
| OpenStack (.py) | 308 | 0.511 [0.434, 0.562] |
| Qt (.cpp) | 887 | 0.513 [0.453, 0.546] |

So neither the reviewers' words nor the refinements' conventions identify which end of the year
they came from. The drift Xu et al. measure is over three years and across 20,000 repositories with
finer features than ten regular expressions; this says only that within one year, in these two
organizations, on the medium this study actually trains on, the ground is not visibly moving. That
is enough for the train-to-test transfer and not enough to contradict them.

### The calibration survives the change of numerics (2026-09-18, job 149248)

`datasets/results/calibration-marker-0.25-fp32.json`. The condition that produced the gate's
non-monotonicity, rerun under the registered fp32 inference on a different node.

| | bf16 (148090) | fp32 (149248) |
|---|---|---|
| contrast on half a | +0.2138 [+0.1473, +0.2877] | +0.2116 [+0.1455, +0.2851] |
| contrast on half b | -0.0861 [-0.1370, -0.0374] | -0.0840 [-0.1340, -0.0376] |
| marker emission, planted half | 0.656 | 0.650 |
| marker emission, other half | 0.617 | 0.628 |
| exact match, planted adapter at home | 0.160 | 0.154 |

Every figure moves in the third decimal, which is the training run's own nondeterminism, not the
decoder's: training is unchanged and still bf16. So the non-monotonicity, the over-emission at two
and a half times the planted rate, and the refutation on the planted side are properties of what the
adapter learned, and the registration of fp32 costs the calibration nothing. The earlier conditions
are not rerun: this is the one the Stage 1 report's limitation rests on.

### How much of adaptation is organizational: 7% and 10% (2026-09-18, three seeds)

The decomposition the study exists to make, averaged over the three seeds of the windowed run:

| | base model | adapter trained elsewhere | adapter trained at home |
|---|---|---|---|
| OpenStack's refinements | 0.048 | 0.291 | 0.308 |
| Qt's refinements | 0.039 | 0.324 | 0.355 |

**Adaptation is worth +0.260 and +0.316 exact match; being adapted on the right organization is
worth +0.017 and +0.031 of that, 7% and 10%.** Almost everything an adapter learns about writing
this corpus's refinements transfers across the organizational boundary: the task is shared, the
house style is a tenth of it at most.

That ratio is the finding a reader should carry away, and it is what makes the measurement hard: the
organizational term is a tenth of the effect the apparatus can see easily, which is why the design
needs the seeds, the crossed interval and the sensitivity analysis to say anything honest about it.
It also frames RQ2, where the same small term is enough to identify a source from a weight update.

### The altitude below ours has already been shown learnable (2026-09-18)

Dai et al., "MPCoder: Multi-user Personalized Code Generator with Explicit and Implicit Style
Representation Learning" (ACL 2024, arXiv:2406.17255), learn *per-user* coding style: an explicit
residual for syntactic conventions, an implicit representation trained with contrastive learning to
separate users, and a Coding Style Score over structure, formatting and naming to say whether the
generated code resembles the user it was generated for.

So individual style is learnable and separable, by a method built to separate it. That is the
altitude below this study's, and it cuts both ways for RQ1. It supports the premise, since a style
signal exists somewhere in the hierarchy; and it sharpens the question, because MPCoder needs
contrastive training *designed* to pull users apart, while RQ1 asks whether ordinary fine-tuning on
an organization's own refinements picks up that organization's hand as a side effect. A method
built to separate sources and a method that happens to absorb one are different claims, and the
7 to 10% organizational share measured here is what the second is worth.

It also leaves the middle of the hierarchy, the project and the organization, where this study and
its RQ2 attacks now sit, with the finding that the codebase is the unit that carries.

### Masking an update is a delay, not a defence (2026-09-18)

`datasets/results/defence-curve.json`, `scripts/defence_curve.py`. What a federated deployment would
actually do against the aggregate attack: each client masks its update with a Gaussian of a given
fraction of the mean update norm, drawn afresh every round. The attacker runs FedAttr's statistic,
averaging the aggregates of rounds holding the target, subtracting the average of rounds without,
and scoring the difference against its reference direction. Rounds of eight, 300 draws, four
reference splits, on the sketched updates.

| target | noise | 1 round | 10 rounds | 50 rounds | 200 rounds |
|---|---|---|---|---|---|
| OpenStack | none | 0.553 | 0.654 | 0.822 | 0.861 |
| OpenStack | 1x | 0.549 | 0.661 | 0.851 | 0.929 |
| OpenStack | 4x | 0.536 | 0.650 | 0.839 | 0.953 |
| OpenStack | 16x | 0.503 | 0.580 | 0.683 | 0.814 |
| Qt | none | 0.543 | 0.623 | 0.705 | 0.766 |
| Qt | 16x | 0.515 | 0.565 | 0.597 | 0.699 |

**One round hides the source and two hundred do not.** The paired statistic is near chance in a
single round, because differencing two rounds of eight adds the other participants' variation to
the target's small contribution. Watch longer and that variation averages away while the target's
direction does not: without noise, 0.86 and 0.77 by 200 rounds; with a mask sixteen times the
update's own norm, still 0.81 and 0.70. A per-round mask buys rounds, not secrecy, which is what
composition across rounds means in a privacy budget and what a deployment quoting a per-round
epsilon would have to account for.

**An unexplained non-monotonicity.** Moderate noise sometimes scores *higher* than none: 0.929 and
0.953 at one and four times the norm against 0.861 without, at 200 rounds, beyond the roughly 0.03
sampling error of 300 draws. Participants are paired across noise levels by construction (the same
stream draws them, and a zero-scale normal consumes it identically), so it is not a sampling
artefact of the comparison. The likely mechanism is the finite pool: with twelve outsiders, rounds
without the target quickly average to nearly the pool mean, leaving a near-deterministic score that
a little noise perturbs in the attacker's favour. That is a guess about this corpus's size, not a
result, and a larger client pool would settle it.

### The organization is not in the update. The content and the codebase are. (2026-09-18, job 149461)

> **Qualified within the hour.** This entry reads the *classification* operating point, where
> organization is indeed not attributable. A targeted detector, which is what an attacker has,
> does find it: AUC 0.736 for AOSP within C++, beating random relabelings of the same nine projects
> at p = 0.012. See "The organization is there, at the operating point an attacker actually has".

`datasets/results/client-attribution-cpp-early.json`. The design the earlier runs could not
support: 77 clients over 18 projects and three organizations, with a content type carried by two
organizations at several projects each, C++ in AOSP (3 projects, 13 clients) and in Qt (6 projects,
30 clients). Same protocol as before: one initialization, 64 examples a client, disjoint by change,
attribution by mean cosine leaving each client out.

| attributing | classes | accuracy | majority | p |
|---|---|---|---|---|
| **content**: C++, Python, documentation | 3 | **0.909** | 0.558 | 0.0002 |
| **project** | 18 | **0.442** | 0.078 | 0.0002 |
| **organization** | 3 | **0.416** | 0.506 | 0.22 |
| organization within C++ (AOSP against Qt) | 2 | 0.395 | 0.698 | 0.67 |
| organization beyond project, within C++ | 2 | 0.395 | - | 0.75, exact over 84 relabelings |

| relation between two clients | mean cosine | pairs |
|---|---|---|
| same project | 0.270 | 146 |
| same organization and content, other project | 0.262 | 571 |
| other organization, same content | 0.253 | 483 |
| other organization and content | 0.240 | 1324 |
| same organization, other content | 0.222 | 402 |

**An update betrays what kind of code it was trained on, and which codebase; it does not betray who
owns the codebase.** Organization is attributed below its own majority baseline, and within C++,
where both organizations bring several projects, it is at 0.395 against a 0.698 baseline, with the
project as the exchangeable unit giving p = 0.75. The relation table says the same: two clients of
one organization writing *different* kinds of content are the least aligned pairs in the corpus,
below two clients that share nothing.

**This overturns the earlier reading, and the earlier number was composition.** The 0.912
organization accuracy of job 148513 came from a corpus where OpenStack was mostly Python and
documentation and Qt mostly C++: the classifier read the content mix and the label followed. Adding
a third organization that writes C++ like Qt, and enough projects to hold content fixed, removes
the confound and the effect with it. The correction was available only because the design was built
to make it: content was labelled separately from organization from the first run, and the test that
would break the claim was named before the data existed.

**What survives.** Every leak this log has measured is real; what changes is its name. A client
update identifies its codebase family, a family being a project or a set of projects sharing
conventions and a language, and through secure aggregation a round's aggregate identifies one too.
An organization is legible only through the codebases it happens to own, which for a federation
means barely at all. For RQ2's framing this is sharper than the original claim, not weaker: a
governance boundary drawn around an organization does not match the boundary the leak respects.

### RQ1 under the registered numerics, and two registrations that earned their keep (2026-09-18, jobs 149258 to 149260)

`datasets/results/rq1-qtfull-fp32-seeds.json`. The windowed run repeated at three seeds under fp32
inference, the precision registered this morning. Same corpora, split and held-out examples as the
bf16 three-seed read.

| reading | OpenStack | Qt | verdict |
|---|---|---|---|
| pooled, crossed (registered) | +0.0230 [**+0.0000**, +0.0457] | +0.0316 [+0.0089, +0.0567] | mixed |
| pooled, median seed | +0.0230 [-0.0004, +0.0448] | +0.0295 [+0.0058, +0.0541] | mixed |
| change-averaged, crossed | +0.0380 [+0.0017, +0.0759] | +0.0334 [+0.0054, +0.0615] | **pass** |
| change-averaged, median seed | +0.0372 [-0.0053, +0.0807] | +0.0350 [+0.0057, +0.0650] | mixed |

Against bf16 at three seeds (+0.0171 and +0.0312 pooled), Qt is unchanged and OpenStack sits about
half a point higher, still not clear of zero.

**The estimand decides the verdict here, and it was registered before this run existed.** Pooled
returns mixed; change-averaged under the same interval returns a pass on both organizations. Had
the choice been open at this point, a pass was available by taking the other estimand, and the
reasons for preferring it could have been written afterwards. They were written in advance instead,
on cluster-size informativeness and on a measured false-positive rate, and they chose pooled. This
is the clearest demonstration this study will produce of what pre-registration is for, and it costs
the headline result.

**The boundary earned its keep too.** OpenStack's pooled lower bound is exactly 0.0, not a rounded
zero: `supports_direction` reads `low > 0.0` and returns False. The registration fixed the strict
inequality after a pilot where Qt's bound sat exactly at zero in 19 of 20 bootstrap seeds. It has
now decided a verdict twice.

**The contrast is the same at every seed while the arms move.** OpenStack's per-seed contrasts are
+0.0230, +0.0230, +0.0230 (SD 0.0000) although the three runs are genuinely different: final losses
0.066, 0.0015 and 0.716, adapter norms 18.46, 19.00 and 18.86, and only 303 of 565 predictions
shared between the first two. Both arms move together, so their difference does not. That is the
strongest evidence yet that the contrast is a property of the data rather than of a training run,
and it is why the measured seed effect in this setting is indistinguishable from zero.

### The organization is there, at the operating point an attacker actually has (2026-09-18)

`datasets/results/aggregate-attack-cpp-beyond-project.json`. Qualifies the entry above, which read
the classification result alone.

Restricted to the 43 C++ clients, so content is fixed, with the attacker's reference split over
**projects** rather than clients, so an organization must be recognised from projects other than the
target's own, and with the projects relabelled to test it:

| target | detector AUC, rounds of 4 | project-level permutation |
|---|---|---|
| AOSP (3 of the 9 C++ projects) | 0.736 | **p = 0.012**, 1 of 84 relabelings as extreme |
| Qt (6 of them) | 0.618 | p = 0.071 |

The true grouping of projects into organizations beats random groupings of the same nine projects.
So an organization's projects do share something beyond each project's own identity, within one
language, and a detector with reference updates from that organization can use it.

**Why this and the classification result are both true.** They are different operating points.

- *Classification*, "which organization does this client belong to", leaving out its project:
  0.395 against a 0.698 majority. A classifier must beat every rival class, and with 30 Qt clients
  against 13 AOSP ones the nearest-class rule simply answers Qt.
- *Detection*, "was one of this organization's clients in this round", given reference updates from
  its other projects: 0.736, and better than chance relabelings at p = 0.012.

An attacker holds the second position, not the first: a server running federated fine-tuning knows
which organizations it recruited and holds updates from each, so it is asking whether a known
candidate contributed, not sorting an unlabelled update among all possible owners. The privacy
claim is about the attacker's question.

**What this study can honestly say, then, at three altitudes.** Content is read almost perfectly
(0.909). The codebase is read strongly (0.442 over 18 projects against a 0.078 baseline). The
organization is a weak residual: invisible to a classifier, detectable by a targeted detector at
0.74 for a three-project organization and 0.62 for a six-project one, on 13 and 30 clients. The
ordering is stable across every instrument this log has used, and the third term is the one a
data-sharing agreement is written about.

### Every leakage number in this study was an average-case metric (2026-09-18)

`# research(2026-09)`. Carlini et al., "Membership Inference Attacks From First Principles"
(IEEE S&P 2022, arXiv:2112.03570), argue that an AUC averages over the whole ROC curve, including
false-positive rates no attacker would operate at, and that an attack can score well on it while
recovering no member at a usable threshold. Privacy is breached by the confident identifications,
not by the average case. Every RQ2 number in this log was an AUC, so every one of them was open to
that objection.

`aggregate.tpr_at_fpr` now reports the share of member rounds caught at a false-positive rate the
defender would tolerate, beside every AUC, and the achieved rate rather than the requested one,
since `n` draws cannot resolve a rate below 1/n. Re-run at 600 draws, C++ only, reference split
over projects:

| target | rounds | AUC | TPR at 1% FPR |
|---|---|---|---|
| AOSP | 4, one of its clients | 0.738 | 0.092 |
| AOSP | 4, two of its clients | 0.885 | 0.347 |
| AOSP | 16, two of its clients | 0.858 | 0.364 |
| Qt | 8, one of its clients | 0.725 | 0.208 |
| Qt | 8, two of its clients | 0.859 | 0.456 |

The signal survives the metric that was built to expose signals that do not. At one false alarm in
a hundred, a server catches 9% of the rounds holding one AOSP client and 35% of those holding two,
against the 1% a coin would get: nine to thirty-five times chance, not a reordering of noise.
Qt's figures are higher than its AUC suggests, which is the asymmetry Carlini et al. describe --
the ordering is mediocre on average and confident at the top.

The defence curve is read at both points from now on, so a mask that moves the average case while
leaving the confident identifications intact cannot be reported as a defence.

### The mask that helps the attacker: a mechanism, not a bug (2026-09-18)

`scripts/masking_mechanism.py`, `datasets/results/masking-mechanism.json`. Closes the open item
"explain the non-monotonicity in noise", which survived the move to the 77-client pool and so was
not the finite-pool artefact I had guessed.

The detector scores a cosine, and a cosine is scale free: a mask cannot hurt it by shortening the
difference vector, only by turning it. Adding an isotropic mask `n` to a difference `v` attenuates
the cosine by about `||v|| / sqrt(||v||^2 + ||n||^2)`, which depends on `v`'s own length -- and the
two classes differ in exactly that length. A member round's difference carries the target's update
at 1/size; a non-member round's carries only which outsiders happened to be drawn. Averaging over
rounds removes the sampling term from both, so the member class keeps a length and the null class
loses one, and the same mask deflates the null further.

Measured on the C++ clients, AOSP the target, five redraws per cell:

| rounds | attenuation, member | attenuation, null | unmasked AUC | AUC at a 1x mask |
|---|---|---|---|---|
| 1 | 0.632 | 0.629 | 0.607 (sd 0.020) | 0.597 |
| 10 | 0.653 | 0.627 | 0.791 (sd 0.028) | 0.797 |
| 50 | 0.725 | 0.627 | 0.941 (sd 0.008) | 0.959 |
| 200 | 0.844 | 0.627 | 0.991 (sd 0.003) | 0.999 |

The null's attenuation is constant at 0.627 and the member's climbs to 0.844: the gap is the whole
effect, and it appears only where averaging has done its work. At one round there is no gap and no
rise. A per-draw model -- attenuate each draw by its own length and add the mask's projection on
the reference direction -- predicts every cell within 0.01, including the rise at 200 rounds
(0.998 predicted against 0.999 measured) and the collapse at a 16x mask (0.954 against 0.959).

**What it means for the defence claim.** Masking is not merely a delay against this attacker; below
some size it is worse than nothing, because it removes the null draws whose difference vector was
too short to point anywhere reliably -- the very draws that produced the attacker's false alarms.
A defence evaluated against a scale-free statistic must be reported this way, and the finding
generalises past this study: a cosine detector is the wrong thing to calibrate a noise budget
against.

Two things this does not say. The updates replayed are one round's, so `rounds` measures the
arithmetic of averaging and not a source's persistence across a moving global model. And the mask
here is per round with no composition, so it is not a differential privacy guarantee, whose
accounting would grow the noise with the rounds rather than hold it fixed.

Incidental, and worth keeping: the first pass of this script read one draw per cell and reported a
0.05 swing between RNG streams as an effect. Repeats were added before any number here was written
down.

**Read by a statistic that is not scale free** (same draws, the difference's projection on the
reference direction rather than its cosine):

| rounds | mask | cosine AUC | projection AUC |
|---|---|---|---|
| 50 | none | 0.939 | 0.963 |
| 50 | 1x | 0.959 | 0.968 |
| 50 | 4x | 0.951 | 0.952 |
| 50 | 16x | 0.800 | 0.800 |
| 200 | none | 0.992 | 1.000 |
| 200 | 1x | 0.999 | 1.000 |
| 200 | 4x | 0.999 | 1.000 |
| 200 | 16x | 0.959 | 0.959 |

Two things follow. The projection is the **stronger** attacker wherever the two differ, by 0.024 at
50 rounds and 0.008 at 200, so normalising threw information away: the leakage this study reports
with a cosine is a lower bound on what the same observations allow. And under the stronger
statistic the curve is monotone at 200 rounds and nearly so at 50 (a 0.005 rise at a 1x mask,
about one standard deviation), which places most of the anomaly in the statistic rather than in
the defence. The honest statement is therefore narrower than the one above: masking degrades this
attacker monotonically once the attacker stops discarding the difference's length, and the
striking non-monotonicity belongs to the scale-free reading of it.

This applies to the difference statistic the defence curve scores. The aggregate attack's detector
is a different quantity -- the cosine between one round's aggregate and a reference direction,
where no subtraction has removed the outsiders -- and whether it too leaves information on the
table is not measured here.

### Two neighbours, checked against the abstracts' own pages (2026-09-18)

`# research(2026-09)`. Both verified on arxiv.org/abs rather than from recall.

**Shi, Zhang, Jin, Xiao, Vorobeychik, Yeoh, Zhang, Hou and Lou, "From Efficiency to Leakage:
Privacy Backdoor in Federated Language Model Fine-Tuning" (arXiv:2606.20553, 18 June 2026).** A
*malicious* server injects a backdoor into the PEFT adapters it distributes, and reconstructs 59%
to 79% of fine-tuning samples with high semantic fidelity, on BERT, GPT-2, Qwen2 and Llama-3.2.

This is a far stronger result under a far stronger adversary, and the contrast is worth stating
rather than hiding. Their server deviates from the protocol; mine does not. RQ2's claim is that a
server which follows the protocol exactly, and which every participant has already agreed to trust
that far, still learns which organization contributed. An organization can defend against Shi et
al.'s adversary by checking the model it is sent; it cannot defend against a passive one by any
means available to it inside the protocol. The two bound different deployments.

(The abstract as fetched was elided mid-sentence, so nothing from it is quoted in the report until
the full text is read.)

**Ghaleb, "Fingerprinting AI Coding Agents on GitHub" (arXiv:2601.17406, 24 January 2026), MSR
'26.** 41 features over commits, PR structure and code characteristics, 33,580 pull requests from
five agents, 97.2% F1 at identifying which agent submitted a pull request.

Three differences, and the third is the one that matters. The unit is an individual agent, not an
organization. The observable is the public artifact -- the commit and the pull request -- where
mine is the model update. And the artifact Ghaleb reads is public by construction, while the one
this study reads is hidden by construction: federated learning exists so that the update is the
only thing that leaves the organization. An attribution result on public artifacts says something
about anonymity; the same result on updates says something about whether the architecture keeps
its promise. It is also evidence that MSR reads this kind of work: the venue this study is aimed
at published the agent version of it a year earlier.

A finding of Ghaleb's bears on RQ1 directly: for agents, the commit-message conventions carried
more of the signal than the code changes, the reverse of what human authorship studies report. If
an organization's fingerprint likewise sits in the review text rather than in the code, RQ1's
contrast is measuring a different surface than its framing claims. The separability probe can
decide it, and does below.

### The permutation test was comparing two different statistics (2026-09-18)

Correcting the entry above. The project-level permutation read its observed value from the
detector run made earlier at eight reference splits and 600 draws, while every relabeling was
scored at two splits and 150. The true grouping is one of the 84 arrangements, so under a matched
comparison it scores against itself and the p-value cannot fall below 1/84; the mismatch let it
score below its own recomputation, and at 128 examples per client the test printed **p = 0.000**,
which an enumeration of 84 arrangements cannot produce. That impossible value is what exposed it.

Both sides now go through one call, and the enumeration is checked to contain the true grouping
before a p-value is reported at all.

| within C++, project-level permutation | as recorded | corrected |
|---|---|---|
| AOSP, 64 examples per client | 0.012 | 0.012 |
| Qt, 64 examples per client | 0.060 | **0.238** |

AOSP's result stands. **Qt's does not**: the 0.060 that read as near-significant was an artefact
of the two settings, and Qt's true grouping is unremarkable among relabelings of the same nine
projects at this training length. The claim that survives is the narrower one -- one of the two
organizations is detectable beyond its projects, not both.

### How long each client trains decides which altitude leaks (2026-09-18, job 149519)

`*-c128.json`. The robustness item asked what several local-training lengths do. Doubling the
examples per client from 64 to 128, which repartitions into 51 clients rather than 77 and 34 C++
clients rather than 43:

| within C++ | 64 per client | 128 per client |
|---|---|---|
| organization, nearest class | 0.395 | 0.765 (majority 0.735) |
| organization, beyond project | 0.395, p 0.75 | 0.676, p 0.19 |
| project | 0.442 | 0.373 |
| content | 0.909 | 0.784 |
| AOSP detector, rounds of 16, two clients | AUC 0.858, TPR 0.364 | AUC 0.940, **TPR 0.588** |
| Qt detector, rounds of 8, two clients | AUC 0.859, TPR 0.456 | AUC 0.980, **TPR 0.910** |
| AOSP project-level permutation | p 0.012 | p 0.012 |
| Qt project-level permutation | p 0.238 | **p 0.018** (median of 4 seeds; 0.012 to 0.036) |

Organization rises at every altitude and by every instrument; project and content fall. Qt, which
was unremarkable among relabelings at 64, is at or within two of the most extreme of all 84
arrangements at 128 depending on the round-draw seed, where AOSP is the most extreme at every
seed. The detector's low-false-positive numbers move furthest: at one false alarm in a
hundred, a round holding two Qt clients goes from 46% caught to 91%.

**What this is and is not.** The nearest-class figure of 0.765 against a 0.735 majority is one
client's difference on 34 and carries nothing by itself; the beyond-project test that controls for
the codebase is 0.676 at p 0.19, still not significant. The classifier has not become able to name
an owner. What moved decisively is the detector, at the operating point an attacker occupies, and
the permutation over groupings. The two sets also differ in size and composition, so this is not a
controlled doubling -- it is two points, consistent across six measurements.

The reading that fits all of it: more local training moves a client's update further from the
shared start and further toward its own data's particulars, and organization-level habits are a
slower, finer signal than the language a client writes. **Leakage is therefore not a fixed
property of the corpus. It is a function of a knob the deployment sets**, and a deployment that
trains longer locally, which is what one does to get more out of federated fine-tuning, leaks
more about who its participants are. That is a sharper claim than RQ2 was framed to make, and it
is the one worth registering.

The obvious next question is whether it keeps rising, which needs a third length.

### The organization is in the code conventions; the words are the codebase's (2026-09-18)

`datasets/results/separability.json`, `separability-code.json`. Prompted by Ghaleb's finding that
for AI coding agents the commit-message conventions carry more than the code changes. For
organizations the answer is the other way round, and it is the sharpest thing the probe has said.

OpenStack against Qt, Python only so content is held fixed, against every pair among each
organization's four largest Python projects that clears the probe's size floor:

| baseline | reviewers' words | code convention shapes |
|---|---|---|
| **cross-organization** | **0.849** [0.769, 0.867] | **0.771** [0.665, 0.814] |
| starlingx/test vs starlingx/config | 0.828 | 0.639 |
| starlingx/test vs openstack/swift | 0.872 | 0.557 |
| starlingx/test vs openstack/neutron | 0.892 | 0.696 |
| starlingx/config vs openstack/swift | 0.816 | 0.630 |
| starlingx/config vs openstack/neutron | 0.760 | 0.575 |
| openstack/swift vs openstack/neutron | 0.789 | 0.596 |
| pyside-setup vs qt/qtbase | 0.892 | refused, too few |

Read as the reviewers' words, the cross-organization figure sits **inside** the within-organization
range and three of seven baselines beat it: two projects of one organization are as far apart in
review vocabulary as two organizations, so that surface carries the codebase, not the owner. Read
as code convention shapes -- snake_case against camelCase, brace placement, f-strings, scope
operators, counted as shapes rather than as identifiers so the probe cannot read project
vocabulary back in -- the cross-organization figure **exceeds all six** usable baselines, and its
lower bound of 0.665 sits above five of the six point estimates.

That is the fingerprint's location, and it is where a fingerprint should be: a coding standard is
written once for an organization and applied across its projects, while review vocabulary follows
the subject matter, which is the project's. It also inverts Ghaleb's result for agents, which is
worth saying plainly -- an agent is one artefact-producing process, so its habits show in the
artefacts it emits around the code; an organization is a rule applied to many people, so its
habits show in the code's conventions.

**The limitation, stated rather than buried.** All six usable code-shape baselines are OpenStack's.
Every Qt pair fell below the probe's per-label floor on Python, because Qt writes little of it.
The within-organization calibration for the code reading therefore rests on one organization, and
the claim is only as good as the assumption that Qt's projects are no further apart than
OpenStack's -- which the latency measurement suggests is false in the other direction, since Qt's
per-project spread was the wider one. A Qt-side baseline needs a suffix Qt carries, which is what
the C++ corpus is for.

**Incidental, and it matters for anything quoting the old file.** The recorded figures predated the
2026-09-18 refreeze: the raw reading moves from 0.837 on 4,048 changes to 0.841 on 4,031. Both
files are rewritten from the current corpus.

**And a defect the code reading exposed.** A refused estimate was still getting an interval.
`separability` returns NaN below a per-label floor, but `accuracy_interval` went on resampling, and
a few draws happened to clear the floor while the rest were dropped -- so the surviving interval
was selected on passing the very check the estimate failed, and printed as `[0.562, 0.857]` beside
a NaN. It now returns NaN for the interval too, and the probe prints the refusal as a refusal.

### The defence curve was pooled over content, and for Qt that made it meaningless (2026-09-18)

On the 77-client set, Qt's curve came back **below chance at every noise level** -- AUC 0.394 at
50 rounds with no mask at all, standard deviation 0.046 over splits. A detector that is reliably
worse than a coin is either inverted or measuring something else. Measured directly, with half of
Qt's clients as the reference and the other half held out:

| mean cosine with Qt's reference direction | |
|---|---|
| Qt's own held-out clients | +0.4516 |
| outsiders, AOSP and OpenStack together | +0.4540 |
| gap | **-0.0024** |

and against the mean of all 39 Qt clients, AOSP's thirteen sit at +0.4897 against OpenStack's
+0.4640, with Qt's own at +0.5095 -- a figure inflated by each client's own contribution to the
mean it is scored against, which is exactly what the split removes.

So Qt's reference direction is a **C++ direction**. AOSP's clients are all C++, Qt's are 30 of 39,
and a round of outsiders containing an AOSP client scores as high as a round containing a Qt one.
The gap is zero and the AUC is noise about it, which happened to fall below 0.5.

This is not a defect in the curve; it is the content confound the per-client attribution already
found, reappearing in the second threat model, and the earlier pooled AOSP figures should be read
with it in mind: AOSP's curve worked because AOSP is one language and most of its outsiders are
not, which is a property of the mixture rather than of AOSP. `--content` now restricts every
client to one content type, as the aggregate attack already did, and the C++-only curve is
running. The pooled Qt cells are not worth the hours and the run was stopped.

The general statement, which belongs in the paper: **a reference direction built from an
organization that writes mostly one language is that language's direction**, and any defence
evaluated against it measures whether noise hides a language rather than whether it hides a member.
Holding content fixed is not a refinement of these attacks; it is a precondition for them meaning
anything.

### The fingerprint is in the part that gets shared, not the part kept back (2026-09-18)

`scripts/subspace_split.py`, `datasets/results/subspace-split.json`. The defence curve says noise is
the wrong lever. The literature's other lever is structural: SDFLoRA (Shen, Lu, Wan and Chen,
arXiv:2601.11219, verified on its abstract page) decouples each client's adapter into a shared
component that "participates in subspace alignment and aggregation across clients" and a private
one that "remains local and uncommunicated", and puts the DP noise only on the aggregated shared
update, which "avoids perturbations to local directions".

That defence removes the attacker's access to everything outside the shared subspace, so it closes
this leak exactly when the organization lives in the residual. Whether it does is a measurement.
Splitting the 43 C++ clients' sketched updates at the top-k principal subspace of all of them, the
friendliest approximation of an alignment step, organization beyond project:

| | whole update | shared subspace | residual |
|---|---|---|---|
| rank 1 (27.7% of energy) | 0.419 | 0.302 | 0.837 |
| rank 2 (30.4%) | 0.419 | 0.465 | 0.814 |
| **rank 4 (34.7%)** | 0.419 | **0.791** | 0.512 |
| rank 8 (42.5%) | 0.419 | 0.767 | 0.535 |
| rank 16 (57.0%) | 0.419 | 0.721 | 0.605 |

and the detector on the shared part at rank 4 reads AUC 0.750 for AOSP and 0.834 for Qt, against
0.704 and 0.613 on the whole update.

Two things follow, and the second was not expected.

**A structural split of this kind does not close the leak.** From rank 2 up, the organization is
legible in precisely the component that is aggregated and transmitted. Rank 1 is the direction
every client shares, which discriminates nothing; the organization enters at the second, third and
fourth components, which are still far inside anything an alignment step would call shared.

**Projecting onto the shared subspace makes the attacker better**, from 0.419 on the whole update
to 0.791 at rank 4. The residual is mostly nuisance that was hiding the signal. A defence that
hands the server a subspace-aligned update is handing it a cleaner one.

**The check that changed the numbers.** Fitting the basis on all 43 clients and then holding one
out scores that client against a subspace its own update helped define. Refitting without the
scored client each time moves the shared figure from 0.884 to 0.791 and the residual at rank 4
from 0.116 to 0.512 -- a large move, and in the direction that reveals the mechanism: with the
target in the basis, its own signature is absorbed into the top-k and its residual is stripped of
exactly what identifies it. The refitted figures are the ones reported. The detector keeps the
all-client basis, which is correct there, since a server holds every update.

### Registered before the run: what FedSA-LoRA's shared matrix should leak (2026-09-18)

Jobs 149581 and 149582. FedSA-LoRA (Guo, Zeng, Wang, Fan, Wang and Qu, **ICLR 2025**,
arXiv:2410.01463, read from the PDF) shares **only the A matrices** with the server and keeps B
local, on the finding that "A matrices are responsible for learning general knowledge, while B
matrices focus on capturing client-specific knowledge", evidenced by A matrices being "more
similar across clients than the B matrices". It is a privacy argument by construction, and the
paper is the basis for FedSA-rsLoRA and FedSA-VeRA.

"More similar on average" is not "carries no identity", and the gap between those two is the shape
of everything this study has found today: organization was invisible to a classifier and visible
to a targeted detector, and the signal sat in the *shared* subspace rather than the idiosyncratic
residual. So the prediction, written before the jobs land:

**A alone will identify the organization at well above chance, and the A-only figure will not be
far below the product's.** If instead A is at chance and B carries it, FedSA-LoRA's rationale holds
and the study has found the defence it was missing. Either answer is worth the queue time; saying
which was expected beforehand is what makes it worth anything.

Each factor is compared as the movement from what the server broadcast: B starts at zero, so B is
its own movement, and A starts at a shared random initialization, so the A geometry subtracts it.
Both are therefore "how far this client moved the matrix the server sent it", which is what an
honest-but-curious server observes.

### Cutting by module role does not remove the source either (2026-09-18)

`scripts/module_split.py`, `datasets/results/module-split.json`. PFAdapter (Liu, Yang, Wang, Yang,
Hao, Zhang, Liu and Zhou, IEEE TCCN, arXiv:2607.12111, 13 July 2026, read from the PDF) cuts the
adapter by the role of the projection: "query and key projections are assigned to global
synchronization ... while value and output projections remain localized", and transmits only the
global-shared set, halving traffic. The geometry already stores a cosine matrix per adapted module,
and a mean of positive semi-definite matrices is positive semi-definite, so any subset of the 196
modules is scored directly as the Gram matrix of unit vectors.

43 C++ clients, detector AUC with the reference split over projects:

| cut | modules | attribution | AOSP | Qt |
|---|---|---|---|---|
| **PFAdapter transmits (q, k)** | 56 | 0.395 | **0.702** | **0.583** |
| PFAdapter keeps local (v, o) | 56 | 0.395 | 0.754 | 0.669 |
| attention, all four | 112 | 0.395 | 0.735 | 0.628 |
| MLP, all three | 84 | 0.395 | 0.732 | 0.627 |
| everything | 196 | 0.395 | 0.735 | 0.628 |
| q_proj alone | 28 | 0.442 | 0.700 | 0.589 |
| v_proj alone | 28 | 0.372 | 0.755 | 0.657 |

**The source is redundantly encoded across module roles.** What PFAdapter transmits carries almost
the whole signal: AOSP at 0.702 against 0.735 for the complete update. The set it keeps local is
slightly the more identifying of the two, at 0.754, but withholding it removes almost nothing from
what the server already has. Every single projection type, alone, detects AOSP between 0.700 and
0.755.

The attribution column reading 0.395 for five different cuts is a coincidence of count, not a bug:
the matrices differ (mean off-diagonal cosine 0.229 for q and k against 0.334 for v and o, largest
entrywise difference 0.152), and the q,k cut gets a *different* pair of clients right than the full
update does. The nearest-class rule is simply coarse at 43 clients, which is the classification
operating point this study already reports as uninformative. The detector is what separates the
cuts.

### Three cuts tested, and what the family has in common (2026-09-18)

Every personalized federated adapter design in the reading splits the adapter into a transmitted
part and a part kept local. They differ only in where the cut falls.

| design | cuts by | transmitted | does the transmitted part still identify the organization? |
|---|---|---|---|
| SDFLoRA (arXiv:2601.11219) | subspace | the aligned shared subspace | **depends on the rank; not established.** See the corrections entry |
| PFAdapter (IEEE TCCN) | module role | q and k projections | **yes**: AOSP 0.702 against 0.735 for everything |
| FedSA-LoRA (ICLR 2025) | LoRA factor | A only | jobs 149581 and 149582 |
| SecureGate (ACL 2026) | sanitization | the "secure" adapter | not measured here |
| FedDPA (NeurIPS 2024), FDLoRA | adapter instance | the global adapter | not measured here |

Two of the three cuts tested leave the source where the server can read it, and one of them hands
the server a cleaner copy than the raw update. The reason is the same in both cases: the
organizational signal is not a localized idiosyncrasy that a partition can quarantine, it is
diffuse and redundant, present in every module role and concentrated in the very directions that
alignment identifies as common.

**This is the gap the study can speak to, and it is not a seventh splitting scheme.** None of these
papers measures whether its transmitted part identifies its source; PFAdapter says so in its own
words -- "FL keeps raw samples on device, but it does not by itself guarantee resistance to update
inversion, gradient leakage, or membership inference. The privacy scope of PFAdapter is therefore
limited to decentralized training without centralized raw-data pooling". What is missing from the
field is not another cut but an evaluation that tells a deployment whether the cut it chose
defends against source attribution. That evaluation is what this apparatus is.

### The factor that looks safest is the one that leaks most (2026-09-18, jobs 149581 and 149582)

The registered prediction, written before the jobs landed, was that A alone would identify the
organization well above chance and "not be far below the product's" figure. That was right in
direction and too conservative in size. **A alone is the most identifying of the three.**

43 C++ clients, reference split over projects, rounds of 4, each factor compared as the movement
from what the server broadcast (B starts at zero; the A geometry subtracts the shared
initialization):

| what the server sees | mean cross-client cosine | AOSP AUC | AOSP TPR@1% | Qt AUC | Qt TPR@1% | AOSP p | Qt p |
|---|---|---|---|---|---|---|---|
| **A only** (FedSA-LoRA transmits) | **0.481** | **0.804** | 0.147 | **0.734** | 0.264 | 0.012 | **0.012** |
| B only (FedSA-LoRA keeps local) | 0.255 | 0.734 | 0.092 | 0.618 | 0.135 | 0.012 | 0.238 |
| the product B A | 0.259 | 0.738 | 0.092 | 0.619 | 0.133 | 0.012 | 0.238 |

(with two of its clients in the round, A gives AOSP 0.946 at TPR 0.579 and Qt 0.799 at 0.429.)

**FedSA-LoRA's empirical finding replicates here exactly.** Guo, Zeng, Wang, Fan, Wang and Qu
(ICLR 2025, arXiv:2410.01463) share only A on the basis that "A matrices are responsible for
learning general knowledge, while B matrices focus on capturing client-specific knowledge",
evidenced by A matrices being "more similar across clients than the B matrices". In this corpus A
matrices sit at a mean cross-client cosine of 0.481 against B's 0.255. They are, as the paper says,
much more alike.

**And the factor that is more alike is the one that identifies its source better.** Qt is the case
that makes it unambiguous: from the full update and from B alone, Qt's true grouping of projects is
unremarkable among relabelings at p = 0.238, and from A alone it is the most extreme of all 84
arrangements at the 1/84 floor. Its true-positive rate at one false alarm in a hundred doubles,
0.133 to 0.264. Transmitting A alone is not merely no better than transmitting the whole update
for this attacker; it is **worse**, because the product's B component partly buries the signal that
A carries cleanly.

**Why, and it is the same reason as everything else today.** A high mean cosine means a large
component every client shares, and a shared component discriminates nothing -- it is the rank-1
direction the subspace split showed contributes least. Identity rides in the deviations *around*
that common component, and A's deviations are the better organized by source. Average similarity
and identifiability are simply different quantities, and the first is no evidence about the second.

**What this does and does not say about that paper.** FedSA-LoRA's own argument is about knowledge
decomposition and aggregation quality, not a formal privacy guarantee, and nothing here contradicts
its accuracy results. What it bears on is the reading the surrounding literature places on that
split -- keeping B local as though the client-specific part had been withheld, and applying DP to
the shared A as the remaining exposure. For source attribution that reading is backwards. One
model, one corpus, 43 clients of one language, one training length; the direction is consistent
across three instruments but the magnitude is not a general constant.

**The third instance of one principle today.** An AUC can be high while no member is caught at a
usable false-positive rate (Carlini et al.). A classifier can be at chance while a targeted
detector is not. And two matrices can differ greatly in average similarity while the more similar
one identifies better. Every one of these is the same mistake: reading an average-case statistic as
though it bounded the worst case. A privacy claim has to be made at the operating point an
adversary occupies, and none of these designs is evaluated there.

### The whole family, and which rows are measured (2026-09-18)

`# research(2026-09)`. Every paper below was read from its PDF, not from an abstract. The table
distinguishes what this apparatus has measured from what it has only reasoned about, because the
difference is the whole point of keeping it.

| design | where it cuts | what the server receives | does the received part identify the organization? |
|---|---|---|---|
| FedSA-LoRA (Guo et al., **ICLR 2025**, 2410.01463) | the LoRA factor | A only | **measured: yes, and best of all three readings.** AOSP 0.804, Qt 0.734, both at the 1/84 permutation floor |
| PFAdapter (Liu et al., **IEEE TCCN**, 2607.12111) | the projection's role | q and k | **measured: yes.** AOSP 0.702 against 0.735 for the whole update |
| SDFLoRA (Shen et al., 2601.11219) | the subspace | the aligned shared component | **measured: depends on the rank, not established.** See the corrections entry |
| SecureGate (Shaaban and Elmahallawy, **ACL 2026**, 2602.13529) | sanitization | the "secure" adapter | **partly measured.** See below |
| FDLoRA (Lu et al., 2406.07925) | the adapter instance | the global module only | not measured; needs new training |
| FedDPA (Yang et al., **NeurIPS 2024**, 2403.19211) | the adapter instance | the global adapter | not measured; needs new training |
| FedAMoLE (Zhang et al., **WWW 2026**, 2411.19128) | the architecture | experts plus an assignment | not measured, and see below |

**SecureGate, partly measured already.** Its secure adapter "learns sanitized, globally shareable
representations" while a revealing adapter holds "sensitive, organization-specific knowledge"
behind a token gate, and its reported gains are against PII extraction: a leakage floor of 4.20%,
a 17.07x reduction in extraction recall for unauthorized requests. Its sanitization baselines are
data scrubbing and masking. **This study's corpus is already scrubbed at that level.**
`sphragis/corpus/scrub.py` replaces every Gerrit account identity with a salted pseudonym, nulls
name, email, username, display name and avatar fields, and rewrites emails found in free text,
before any record reaches disk. Every RQ2 number in this log was measured on that corpus. So
identity scrubbing of the kind SecureGate uses as its baseline does not touch this signal, which is
unsurprising once located: the fingerprint is in code conventions and review habits, not in who
wrote them. What is not measured is SecureGate's learned secure adapter, which is a different
object from a scrubber.

**FedAMoLE deserves a row of its own, and a warning.** It assigns "architecturally heterogeneous
models" per client through "a reverse selection-based expert assignment strategy to tailor model
architectures for each client based on data distributions". The assignment is therefore a function
of the client's data, and the server necessarily observes it. That is a side channel that owes
nothing to the weights: before examining a single parameter, a server learns a summary of each
client's data distribution by construction. Nothing here measures it, and the point is not that
FedAMoLE is worse than the others -- it is that a taxonomy of "what part of the adapter is
transmitted" does not cover a design whose *structure* is data-dependent.

**Where this leaves the contribution.** Three cuts measured, three leaking, one of them leaking
more than sending everything. Two cuts unmeasured and reachable with new training runs. One design
whose leak would not be a cut at all. What the field is missing is not a seventh partition; it is
an evaluation that tells a deployment whether the partition it chose resists source attribution,
and a statistic to evaluate it with that is not an average.

### Gap-K% has numbers, and it is the smallest gap of the three (2026-09-18, job 149555)

`datasets/results/contamination-openstack-6mo-with_context-gapk.json`. Closes the open item that
Gap-K% had an implementation and no measurement, the saved results having kept scores rather than
per-token statistics. OpenStack, six months each side, hunks with three context lines, 1,971
eligible post-cutoff examples against 2,502 pre-cutoff.

| statistic | post-cutoff | pre-cutoff | gap |
|---|---|---|---|
| Min-K% | -7.4463 | -7.3167 | -0.1296 |
| Min-K%++ | -1.7955 | -1.7208 | -0.0746 |
| **Gap-K%** (arXiv:2601.19936) | -1.8168 | -1.7610 | **-0.0558** |
| guided completion (null instrument) | 0.0113 | 0.0078 | +0.0035 |

The pre-cutoff side scores higher on every membership statistic, which is the direction
contamination would produce, and the size is what decides whether that means anything. Gap-K%
exists to be robust to distribution shift -- it reads the distance between the top-1 token's
log-probability and the observed token's, smoothed over windows, so the general difficulty of a
passage divides out. It returns the **smallest** separation of the three, 57% of Min-K%'s and 75%
of Min-K%++'s.

That is the reading the earlier entry already reached by a different route: the separation these
statistics show is consistent with the two windows being differently distributed rather than
differently memorized, by Meeus et al.'s criterion (SoK, SaTML 2025). A shift-robust statistic
shrinking the gap is what that hypothesis predicts. Contamination protection continues to rest
where it always did, on the post-cutoff window postdating the checkpoint's publication --
2024-10-01 against 2024-09-17, recorded in the run.

Guided completion sits at 0.011 and 0.008, at its floor, as registered.

**A defect this run exposed, fixed for the next one.** The window-cost diagnostic computed
`gap_k_windows` for every sequence and then sliced `[:1]`, so a field named `windows` reported the
**first example** while reading as though it had certified the corpus. What it actually says is
that one post-cutoff example lost none of its 61 windows and one pre-cutoff example lost none of
its 37. The aggregate now sums positions, undefined positions, windows and dropped windows over a
whole side and counts the examples that lost anything or scored nothing. The scores above are
unaffected, since `gap_k_percent` was computed correctly per example and aggregated correctly; only
the diagnostic was truncated. The true corpus-wide cost needs the next battery run, and is not
worth two hours of GPU on its own -- it rides along with the next one.

### Novelty check: nobody is evaluating this (2026-09-18)

`# research(2026-09)`. Searched for a benchmark or evaluation that asks whether the part a
federated adapter scheme transmits identifies the client or organization that produced it. Nothing
matches.

What exists nearby, and why none of it covers the question:

- The **FlowerTune LLM Leaderboard** is described as a first-of-its-kind public cross-domain
  benchmark suite for federated fine-tuning of LLMs. It scores utility across domains. A scheme
  that leaks its participants perfectly can top it.
- Membership-inference work in federated learning (FedMIA, CVPR 2025; the ACM Computing Surveys
  treatment, 10.1145/3704633) asks whether a *data point* was in training. The question here is
  whose update this is, which is a different target with a different exchangeable unit.
- The personalization literature measures what its split does for accuracy and communication cost,
  and states privacy as an architectural property rather than measuring it.

So the gap is not that the field lacks another partition scheme. It is that a deployment choosing
among FedSA-LoRA, PFAdapter, SDFLoRA, FDLoRA, FedDPA, SecureGate and FedAMoLE has no way to ask
which of them resists source attribution, and the three measured so far do not.

**A correction to the table above.** FedAMoLE is published at **The Web Conference 2026**
(doi 10.1145/3774904.3792147), not an unrefereed preprint as the arXiv identifier alone suggested.
Its data-dependent expert assignment is therefore a refereed design, which strengthens rather than
weakens the point that a structure chosen from a client's data distribution is a channel no
weight-space partition covers.

### Qt is not a coherent organization in update space, and that is the finding (2026-09-18)

Correcting the entry above, which said the pooled defence curve read Qt below chance because the
reference direction was really a language direction, and that holding content fixed would settle
it. It did not. Restricted to the 43 C++ clients, Qt's curve still read 0.364 at 10 rounds.

I then supposed the cause was a split mismatch: the aggregate attack splits its reference over
projects while the defence curve split randomly over clients, and mirroring the split would fix it.
**That was also wrong, and the test refuted it.** With the project split Qt reads 0.323, slightly
*worse*. The change is kept anyway, because splitting over projects is the principled choice and
its sibling detector already did it -- a guard fixed on one path and not on the path beside it.

The direct measurement had the answer, within C++:

| mean pairwise cosine | |
|---|---|
| AOSP with AOSP | 0.2872 |
| AOSP with Qt | 0.2589 |
| **Qt with Qt** | **0.2534** |

**Qt's C++ clients are less like each other than they are like AOSP's.** Held-out minus outsider
alignment with the organization's own reference is +0.045 for AOSP and -0.008 for Qt, the same sign
in all six splits. Splitting over projects makes Qt worse because within-Qt coherence is almost
entirely a project effect (same project 0.2597, other project 0.2523), and a project split removes
exactly that.

So the below-chance reading is not a defect to be repaired. It says a round holding a Qt client
looks *less* like Qt's other projects than a round of AOSP clients does. AOSP's three projects are
Android platform C++ under one style guide and one build system; Qt's six span a framework, an IDE
and a QML runtime. At 64 examples a client, "Qt" is a name over a federation of unlike codebases
and "AOSP" is a house.

**This is the same fact every other instrument has been reporting.** Qt's project-level permutation
is p = 0.238 from the product and from B, against AOSP's 0.012; Qt only reaches the 1/84 floor from
A alone, or at 128 examples a client. A near-zero quantity is exactly where two statistics can
disagree in sign, and the aggregate detector's +0.613 for Qt rests on a gap of 0.004 on a base of
0.69. The honest reading is that Qt's organizational signal at this training length is not reliably
present, not that one instrument is broken.

**It also answers the question the RQ1 sharpening was circling: what makes an organization one?**
An organization is a detectable unit exactly when its projects resemble each other more than they
resemble an outsider's. That is a measurable property, it differs between the two organizations in
this study, and it is not something a researcher gets to assume by drawing a box around a GitHub
org. Norm is not the cue: AOSP's updates average 2.16 and Qt's 2.11 with standard deviations of
0.04 and 0.03, and rescaling every update to unit norm leaves the detector unchanged (0.613 and
0.699 against 0.613 and 0.704).

Qt's defence-curve cells are therefore withheld from interpretation at 64 examples a client, and
the curve is worth rerunning against Qt only at a training length where Qt is coherent.

### Registered before computing: organizational coherence across training lengths (2026-09-18)

If an organization is detectable exactly when its projects resemble each other more than they
resemble an outsider's, that property is one number per organization:

    coherence(O) = mean cosine between clients of O on DIFFERENT projects
                 - mean cosine between clients of O and clients of any other organization

Same-project pairs are excluded from the first term on purpose, since a project effect is not an
organization effect. At 64 examples a client, within C++, AOSP's is positive and Qt's is not (Qt's
cross-project pairs sit at 0.2523 against Qt-AOSP pairs at 0.2589).

**Prediction, written before the 128-example vectors are read:** AOSP positive at both lengths; Qt
negative at 64 and **positive at 128**, because Qt's project-level permutation moved from p = 0.238
to the 1/84 floor between those lengths and coherence is the quantity that permutation should be
tracking. If Qt stays negative at 128, then coherence is not what the permutation reads and the
explanation above is incomplete a third time.

**Outcome: the prediction failed on the part that mattered.** Within C++, with 95% intervals from
resampling each organization's projects:

| coherence | 64 per client | 128 per client |
|---|---|---|
| AOSP (3 projects) | +0.0234 [+0.0173, +0.0311] | +0.0235 [+0.0079, +0.0298] |
| Qt (6 projects) | -0.0067 [-0.0147, +0.0030] | **-0.0005** [-0.0040, +0.0039] |

AOSP is positive at both lengths, as predicted, and Qt moved toward zero, as predicted. But Qt did
**not** become positive: at 128 examples it sits at -0.0005 with an interval centred on zero, while
its project-level permutation reached the 1/84 floor at that same length. By the criterion written
above, pairwise coherence is not what the permutation reads, and the explanation is incomplete a
third time. (Qt's interval at 64 also covers zero, so "Qt is incoherent" was already stronger than
the data at that length; "Qt is not detectably coherent" is what it supports.)

### Composition, not coherence, was the confound worth testing, and length survives it (2026-09-18)

Two explanations for Qt's permutation moving from p = 0.238 at 64 examples a client to the 1/84 floor
at 128 were tested and failed: pairwise coherence (Qt stays at -0.0005 at 128) and, post hoc,
centred centroid coherence (Qt is positive at both lengths and *lower* at 128, +0.109 to +0.047).
Rather than generate a third, this tests the confound the training-length entry already named: the
two sets differ in composition as well as length, because packing at 128 leaves fewer clients per
project (AOSP's system-core falls from three clients to one, frameworks-av from four to two).

Five random subsamples of the **64-example** clients, each drawn to the **128-example** set's exact
per-project counts (34 C++ clients), then the same project-level permutation:

| | AOSP p | Qt p |
|---|---|---|
| 64 per client, full set | 0.012 | 0.238 |
| 64 per client, 128's composition, draw 0 | 0.012 | 0.095 |
| draw 1 | 0.012 | 0.155 |
| draw 2 | 0.012 | 0.071 |
| draw 3 | 0.012 | 0.298 |
| draw 4 | 0.012 | 0.131 |
| **128 per client** | 0.012 | **0.012** |

Composition accounts for part of the movement -- matching it takes Qt from 0.238 to a median of
0.131 -- but with 64-example updates Qt reaches the floor in none of five draws, and with
128-example updates it does. **The longer training, not the repacking, is what makes Qt's grouping
the most extreme of all 84.** AOSP is at the floor in every draw at both lengths.

The limit on this, stated: the 128-example side is one packing, since a second needs another
training run; the 64-example side is five. The comparison is five draws that never reach the floor
against one that does, which is strong but not symmetric. Job 149598, at 256 examples a client on
the same committed source list, is the next point, and a second 128 packing would make it
symmetric.

That leaves the training-length claim standing for both organizations, and leaves the *mechanism*
for Qt open: whatever longer training adds, it is not pairwise or centroid coherence. Tamura and
Tsugawa (arXiv:2609.19864, submitted 17 September 2026) offer a candidate worth measuring rather
than asserting: across GitHub, identifier-naming diversity fell around 2023-2024 as LLM tools
spread, yet "repositories whose owners are closer in the collaboration network remain more similar
in naming style even among recent, more homogeneous cohorts", in five of six languages. If
organizational coherence in update space tracks collaboration density, AOSP's platform team and
Qt's looser federation of framework, IDE and runtime would differ exactly as they do. The built
examples do not carry reviewer identities, but the raw snapshots carry stable salted pseudonyms, so
reviewer overlap across an organization's projects is measurable without identifying anyone.

That also answers a threat to the code-convention finding. If naming styles are converging under
LLM tools, an organization's convention fingerprint might be eroding. Tamura and Tsugawa find that
aggregate convergence and network-local variation coexist, and an organization is a network-local
cluster by construction. (A second hit, Kupari, Giacaman and Terragni, ICSME 2025, arXiv:2601.09832,
was summarized elsewhere as finding code-style adherence stable over twelve months; its abstract
page does not state that, so it is not cited for it.)

### Registered before computing: collaboration overlap across an organization's projects

From the raw change records, which carry salted pseudonyms for each change's owner, submitter and
attention set: for each C++ project in the committed source list, the set of accounts that appear
on its changes, and for each organization the mean Jaccard overlap between the account sets of its
*different* projects. No account is identified; only set sizes and intersections are read.

**Prediction:** AOSP's three projects (art, frameworks/av, system/core) share more of their people
than Qt's six do, pairwise on average. If Qt's projects overlap as much as AOSP's or more, then
collaboration density is not what separates a coherent organization from a loose one here, and the
Tamura and Tsugawa mechanism does not transfer to this setting.

**Outcome: refuted, and in the opposite direction.**

| accounts across an organization's C++ projects | changes | mean pairwise Jaccard | range |
|---|---|---|---|
| AOSP: art, frameworks/av, system/core | 4,268 | 0.1286 | 0.1099 to 0.1421 |
| Qt: qt-creator, qtbase, qtdeclarative, qtmultimedia, qtgraphs, qttools | 28,563 | **0.2455** | 0.1400 to 0.4242 |

Qt's projects share nearly twice as much of their people as AOSP's, and Qt's least-overlapping pair
(0.140) matches AOSP's most-overlapping one (0.142). Yet AOSP is the coherent organization in update
space and Qt is not. By the registered criterion, collaboration density is not what separates them
here, and Tamura and Tsugawa's mechanism does not transfer to this setting -- which is no
contradiction of their result, since they measure naming-style similarity across repositories by
owner proximity, and this measures adapter-update similarity across projects by shared people.

The inversion is itself informative. The same engineers moving between qtbase, qtdeclarative and
qt-creator do not make those projects' updates alike, while AOSP's three projects, staffed by
largely different people, produce alike updates. What they share is a codebase: Android platform
C++ under one style configuration, one lint regime and one build system. That points the same way
as the probe did earlier today, where the organization was legible in code convention shapes and not
in the reviewers' words: **the fingerprint is a property of the codebase's conventions and tooling,
not of the people who write to it.** An organization is detectable when it imposes one set of
conventions across its projects, whoever does the work.

That is three mechanism hypotheses for Qt refuted in a row -- pairwise coherence, centroid
coherence, collaboration overlap -- and it is where generating a fourth would start to be fitting
noise. What stands measured: AOSP is coherent at both lengths; Qt is not detectably coherent at
either; longer local training makes Qt's grouping detectable anyway, and that survives matching
composition. The mechanism for the last of those stays open, and the shared-conventions reading
above is a hypothesis to test with the convention-shape probe on C++, not a finding.

(Account sets are taken from each change's owner, submitter and attention set, so bots that sit in
attention sets count as accounts; small projects such as qtgraphs, with 62 accounts, make individual
Jaccard values noisy. Neither changes the direction, which holds for every AOSP pair against Qt's
mean.)

### 256 examples a client is not a third point on this corpus (2026-09-18, job 149598)

At 256 examples, AOSP's system/core (204 usable examples) cannot fill one client, and the job
refused to drop the source silently. Run anyway, AOSP would fall to two projects and four clients,
and the project-level permutation from 84 arrangements to 28, with a floor of 1/28 rather than 1/84.
That would be a different and weaker test on a thinner set, not a further point on the same curve,
so it is not run. Long local training needs larger per-project corpora, which is what the
ten-project AOSP rebuild is for.

Instead, job 149607 draws a **second packing at 128**, the same initialization and training order
with a different assignment of examples to clients (`--packing-seed`, which defaults to `--seed`
so every earlier run reproduces as it ran). The 64-example side of the length comparison has five
draws and the 128-example side one; this makes it two, from runs that differ only in packing.

### RQ1's contrast is still unclaimed by its nearest neighbours (2026-09-18)

`# research(2026-09)`. Checked on their abstract pages: Kumar, Lones, Maarek and Zantout,
"Fine-Tuning Models for Automated Code Review Feedback" (arXiv:2605.12610, May 2026), fine-tune Code
Llama on feedback distilled from a proprietary model, for student Java code. Begolli, Aksoy and
Neider, "Fine-Tuning Multilingual Language Models for Code Review: An Empirical Study on Industrial
C# Projects" (arXiv:2507.19271), pool public benchmarks with industrial repositories into one
training corpus. Neither trains per project or per organization, and neither evaluates an adapter
on another organization's data. The matched-against-mismatched contrast RQ1 registers, which asks
whether an adaptation is specific to the organization it came from rather than to the task, does
not appear in either.

### Registered before computing: do AOSP's projects share conventions that Qt's do not?

The one mechanism for organizational coherence still standing is shared code conventions. It
predicts something the convention-shape probe can measure without any adapter: within an
organization, how separable its C++ projects are from one another when read only as convention
shapes. Separability near 0.5 means the projects write alike; high separability means each project
has its own conventions.

**Prediction:** AOSP's C++ projects are less separable from one another by convention shapes than
Qt's C++ projects are. If Qt's projects are as alike as AOSP's or more, shared conventions do not
explain the coherence gap either, and the mechanism is open with no candidate left.

**Outcome of the shared-conventions prediction: refuted, in the opposite direction again.** Code
convention shapes, `.cpp` only:

| within-organization project pairs | separability |
|---|---|
| AOSP frameworks/av vs system/core (the only pair that clears the size floor) | **0.806** [0.687, 0.872] |
| Qt, six pairs among qtbase, qt-creator, qtdeclarative, qtmultimedia | 0.534 to 0.608 |
| AOSP against Qt, pooled | 0.679 [0.614, 0.723] |

AOSP's two measurable projects differ in their conventions *more* than any pair of Qt's do, so
shared conventions do not explain AOSP's coherence either. That is the fourth candidate refuted
(pairwise coherence, centroid coherence, collaboration overlap, shared conventions), and, as
committed above, the mechanism for why AOSP is a coherent organization in update space and Qt is
not stays open with no candidate left. (AOSP has one scorable pair against Qt's six, which makes
this a weaker test than the others; it is reported because it was registered.)

### Corrections from an adversarial review of today's code (2026-09-18)

Three independent reviews reran today's analyses against the committed data. Findings that change
what this log says, stated here before any is fixed:

**1. The subspace-split headline was overstated, and part of it was wrong.** "0.791 against 0.419
on the whole update" is withdrawn as stated.
- The whole-update 0.419 is a broken baseline, *below chance*: the project-level null has mean
  0.509, and 25 of 30 Qt clients are predicted as AOSP. The rise to 0.791 is against a class-bias
  failure, not a fair reference, so "projecting nearly doubles the attacker" is withdrawn.
- Rank 4 was chosen after looking. Under the exact project-level null (84 relabelings), rank 4's
  shared part is p = 0.036, rank 8's 0.024, but the maximum over the reported ranks is p = 0.071.
- Where the signal sits depends on k. At rank 1 the residual (the part a split keeps local) is the
  strongest cell, 0.837 at the 1/84 floor; at ranks 4 and 8 it is the shared part; at rank 16
  neither is significant (0.119). And rank 1's shared row is degenerate, since every rank-1
  projected cosine is exactly 1.0.
- So "the fingerprint is in the part that gets shared, not the part kept back" is **not
  established**. The defence table's SDFLoRA row changes from "leaks, more than the whole update"
  to "depends on the rank of the shared subspace; not established either way". Two of the three
  cuts, not three, are measured as leaking, pending the review of the factor geometry.

**2. Qt's permutation p is seed-dependent at the draws the test uses.** Rerun over four seeds:

| Qt, project-level permutation | seeds 0 / 1 / 2 / 3 |
|---|---|
| 64 examples, product | 0.238 / 0.262 / 0.167 / 0.333 |
| B factor only | 0.238 / 0.274 / 0.167 / 0.333 |
| 128 examples, product | 0.012 / 0.012 / 0.024 / 0.036 |
| A factor only | 0.012 / 0.024 / 0.012 / 0.012 |

AOSP stays at 1/84 on every seed and geometry. The direction of every Qt comparison survives every
seed tried: 128 examples below 0.04, 64 examples above 0.16, A-only below 0.03, B-only above 0.16.
But the repeated phrase "Qt reaches the 1/84 floor" was true for seed 0 only and is replaced by the
ranges above. The composition-matched test earlier today used seed 0 throughout; its conclusion
(length, not repacking) still holds against these ranges, since no composition-matched draw fell
below 0.071.

**3. A stale figure.** The entry "The organization is there, at the operating point an attacker
actually has" still quotes Qt at p = 0.071. The corrected value is 0.238 at seed 0, and 0.17 to 0.33
across seeds.

Still open from the same review, and not yet fixed: the permutation's tests are text searches that
every mutant tried survives; the enumeration counts arrangements rather than distinct groupings;
the overwrite guard checks at job start while scripts write hours later, so two concurrent jobs
with one output can still replace each other; three further outputs are unguarded or misnamed.

**The factor-geometry review: the A-only result survives, and two statements about it change.**
The third review attacked "A alone identifies the organization better than B or the product" with
every mechanical explanation it could construct, and none held:
- label-shuffled and project-level nulls sit at chance, with the truth ranked first of 84;
- norms are not the cue: a Gram matrix built from A's norms with a common cosine scores *below*
  chance (0.46 and 0.41), and cosines alone reproduce the result (0.791 and 0.733);
- the large shared component *suppresses* the signal rather than creating it: removing the global
  mean raises A's AUC to 0.842 and 0.866;
- equal-weighting the modules keeps A ahead (0.794 and 0.708 against B's 0.735 and 0.618), and A
  matches or beats B for AOSP in every module kind;
- leaving each project out in turn, A beats B for both organizations in all nine runs;
- training order does not explain it (Mantel p 0.26 on A, 0.46 on B), and each client's LoRA state
  is restored and checked, with a fresh optimizer and reseeded generator, before it trains.

Two things change in how it is stated.
1. **The product and B are one geometry, not two.** Their off-diagonal cosines correlate at 0.9998
   (largest difference 0.006), because A barely moves from its near-orthogonal initialization, so
   B A is close to B A0. "A beats B *and* the product" is one comparison.
2. **The claim belongs to the membership detector.** FedAttr's paired-difference statistic runs
   backwards for Qt on both factors (its own clients -0.061 against a stranger's +0.014 on A), and
   on the A geometry the q and k projections alone put Qt below chance. The result is stated for
   the detector that scores a round's aggregate against a reference direction, and not for every
   statistic in the study.

(With content held to C++ there are two organizations, so the AOSP and Qt permutations enumerate
the same groupings as complements. They are two statistics over one null, not two independent
tests.)

### The attack results regenerated, with every seed's p (2026-09-18)

The four committed aggregate-attack results were rerun from a clean tree with the permutation now
in `aggregate.project_permutation`: distinct groupings, the truth checked to reproduce itself, four
round-draw seeds each. Provenance now names the commit that produced them and would list any
uncommitted code; these list none.

| project-level permutation, 84 groupings | AOSP | Qt median | Qt range over 4 seeds |
|---|---|---|---|
| 64 examples, product | 1/84 on every seed | 0.250 | 0.167 to 0.333 |
| 128 examples, product | 1/84 on every seed | 0.018 | 0.012 to 0.036 |
| 64 examples, A only | 1/84 on every seed | 0.012 | 0.012 to 0.024 |
| 64 examples, B only | 1/84 on every seed | 0.256 | 0.167 to 0.333 |

These match the review's independent reruns seed for seed. Every Qt comparison the log draws
survives every seed: 128 examples and A-only never exceed 0.036; 64 examples and B-only never fall
below 0.167. These ranges, not any single seed, are the figures of record.

### The subspace split, redone: it depends on the rank and on the instrument (2026-09-18)

`scripts/subspace_split.py`, rewritten after the review. Every attribution cell now carries its
exact project-level p over the 84 groupings, accuracy is reported beside balanced accuracy, the
basis for each scored client is refitted without its *whole project* (the stricter refit the
review proposed), the degenerate rank-1 shared cell is not scored, and the best cell over ranks is
judged against the null's best over the same ranks.

Attribution, nearest class over other projects (majority rate 0.698):

| | shared | residual (what a split keeps local) |
|---|---|---|
| whole update | 0.419 (p 0.786), balanced 0.583 (p 0.310) | |
| rank 1 (27.7% of energy) | degenerate | **0.837 (p 0.012)**, balanced 0.883 |
| rank 2 | 0.419 (p 0.560) | 0.837 (p 0.024) |
| rank 4 | 0.767 (p 0.048) | 0.721 (p 0.036) |
| rank 8 | 0.767 (p 0.036) | 0.721 (p 0.024) |
| rank 16 | 0.698 (p 0.179) | 0.651 (p 0.095) |
| **best over ranks, family-wise** | 0.767, **p 0.095** | 0.837, **p 0.024** |

The aggregate detector, on the same halves (AOSP, Qt; the whole update reads 0.704, 0.613):

| | shared | residual |
|---|---|---|
| rank 1 | 0.476, 0.486 | 0.643, 0.758 |
| rank 2 | 0.614, 0.668 | 0.448, 0.668 |
| rank 4 | 0.750, 0.834 | 0.282, 0.247 |
| rank 8 | 0.644, 0.757 | 0.412, 0.314 |
| rank 16 | 0.624, 0.708 | 0.435, 0.341 |

**The two instruments disagree about where the organization sits.** Corrected for the search
over ranks, the classifier finds it in the residual (p 0.024) and not in the shared part (p 0.095).
The detector reads it in the shared part from rank 2 up and runs *inverted* on the residual from
rank 4 up, which is information too, in the wrong direction for that detector. Removing the common
directions first sharpens the classifier, which fits the factor review's finding that removing the
global mean raises identifiability.

So the SDFLoRA question stays **not established**, now for a stated reason: neither "a subspace
split protects" nor "it leaks" holds across the ranks and instruments tested, and this data has now
been searched over both. Searching it further for the combination that comes out significant would
be the error the review caught.

**Registered for the ten-project AOSP rebuild, before its adapters exist.** Its seven new projects
are data this analysis has not seen. On clients trained from them beside Qt's, two tests, fixed now:
1. the aggregate detector on the **shared** half at **rank 4**, with the project-level permutation
   over four seeds: "the split leaks" if both organizations' median p is at or below 0.05;
2. the held-out classifier on the **residual** at **rank 1**: "the kept-back part identifies" if its
   project-level p is at or below 0.05.
Neither result will be reinterpreted at another rank or with another instrument.

### The probe's intervals could not contain their own estimates, and now do (2026-09-18)

The review found the raw probe reading 0.841 with an interval of [0.814, 0.836]. The cause was the
procedure, not the interval formula: each bootstrap resample *refitted* the classifier on changes
drawn with replacement, about 63% of them unique, so every refit trained on less and scored lower,
and the resampled accuracies sat below the estimate by more than their own spread. A bias-corrected
(BCa) interval was tried first and made it worse -- [0.839, 0.839] -- because any interval built
from quantiles of the draws cannot contain an estimate that every draw falls below.

`accuracy_interval` now fits the classifier once and resamples changes over the predictions it
made on them. That measures how much the accuracy moves with which changes were observed, and not
with a smaller training set; what it leaves out is the classifier's own refit instability, so it is
the narrower reading, and is labelled `out_of_fold_percentile` in every result. `separability`
itself is unchanged: against the previous code on the real corpus, three cases at two seeds each,
every output is identical to the last digit.

| | estimate | old interval | new interval |
|---|---|---|---|
| raw, reviewers' words | 0.841 | [0.814, 0.836] | [0.831, 0.851] |
| matched .py, reviewers' words | 0.849 | [0.769, 0.867] | [0.807, 0.890] |
| matched .py, code shapes | 0.771 | [0.665, 0.814] | [0.689, 0.817] |

**The code-convention finding survives**, slightly strengthened: the cross-organization code-shape
reading's lower bound rises to 0.689, still above five of the six within-OpenStack baselines (0.557
to 0.696). Point estimates quoted anywhere in this log are unaffected; intervals quoted before this
entry were computed the old way. The drift readings earlier today were not committed as files, so
their intervals are not regenerated; their accuracies stand.

**For AJ, noticed in passing and not changed:** `scripts/separability_over_time.py` reads every built
month, which includes 2025-09 and 2025-10 from the dev window, where `scripts/separability.py`
confines itself to pilot and train. It reads corpus text only and no held-out predictions, so
nothing is spent, but the two probes follow different rules.

### A second review, of the fixes (2026-09-19)

A fresh context reviewed the previous day's corrections without being told why they were written.
Nothing blocking; every committed result it regenerated reproduces from HEAD, and it confirmed the
factor geometry computes A minus the broadcast initial A and records that file's hash. Its serious
finding was about the tests rather than the code: **24 of 59 mutants survived the suite**, among
them the probe fix's own regression test, which could not fail on a mutant that restored the very
behaviour it was written for, because its fixture scored 1.0 and only a label on the result told
the versions apart.

Fixed here:

- **The probe test now counts fits.** One fit per fold, whatever the number of resamples, which
  the restored-refit mutant fails. The fixture now scores about 0.55 so an interval has room on
  both sides, and a second test holds the estimate and the interval to the same predictions.
- **`accuracy_interval` cross-validates once.** It had fitted every fold twice, once for the
  estimate and once for the resampling rows.
- **A rank that spans the held-out clients is no longer scored.** The bases are fitted without a
  whole project, so the largest project sets the limit; past it the shared half is the whole space
  and the residual is numerical noise (largest entry 4e-15 on 34 clients at rank 32) that
  normalises into unit vectors and scores like an ordinary cell. The committed result is
  unaffected, since its smallest held-out fit holds 37 rows, but the registered test on the
  ten-project rebuild would have met it at the default ranks.
- **A third organization is refused** by the subspace null instead of being folded into the
  second, which would have put the truth outside its own null and cost the p-value its floor.
- **`energy_in_shared` is renamed `energy_in_global_shared`**, since it describes the global basis
  the detector uses and not the held-out bases the attribution uses.

Still open from that review, and recorded rather than fixed: a SIGKILLed job leaves a zero-byte
claim that cannot be told from a live one, which a Slurm requeue would refuse; several load-bearing
behaviours have no test (the permutation's split over projects, the family-wise step, the
contamination partial's claimed name, calibration claiming every condition before training).

### Repack the same clients and the detector survives; the classifier does not (2026-09-19)

`*-c128-p2.json`. The 128-example run said leakage is a function of how long each client trains.
It was one packing, so who shared a client with whom was decided once, and the reading rested on
two points that differ in size as well as in length. This is the same 51 clients, the same
training seed, the same 128 examples each, repacked with packing seed 2: the only thing that moves
is which examples group into which client.

| within C++ | 64 | 128, packing 1 | 128, packing 2 |
|---|---|---|---|
| organization, nearest class | 0.395 (majority 0.698) | 0.765 (majority 0.735), p 0.034 | **0.559**, p 0.455 |
| organization, beyond project | 0.395, p 0.75 | 0.676, p 0.19 | **0.529**, p 0.55 |
| project, all clients | 0.442 | 0.373 | 0.412 |
| content, all clients | 0.909 | 0.784 | 0.961 |
| AOSP detector, rounds of 16, two clients | AUC 0.858, TPR 0.364 | AUC 0.940, TPR 0.588 | AUC 0.887, **TPR 0.419** |
| Qt detector, rounds of 8, two clients | AUC 0.859, TPR 0.456 | AUC 0.980, TPR 0.910 | AUC 0.962, **TPR 0.813** |
| AOSP project permutation, median of 4 seeds | 0.012 | 0.012 | 0.012 |
| Qt project permutation, median of 4 seeds | 0.250 | 0.018 | 0.042 |

**The length effect replicates where it was claimed, and only there.** Every detector cell at 128
beats its 64 counterpart under both packings: AOSP 0.364 to 0.588 and 0.419, Qt 0.456 to 0.910 and
0.813. AOSP's permutation sits at the floor in all twelve seed-by-packing cells, and Qt's, which
could not be told from a relabeling at 64, is below 0.05 at the median under both packings.

**The classifier rows were a packing artefact.** Nearest-class attribution within C++ went 0.395
to 0.765 and read as the classifier waking up; repacked it is 0.559, below its own 0.735 majority,
at p 0.455. The beyond-project control moves with it, 0.676 to 0.529. Nothing about the
organizations or the training changed between those two numbers, so the difference is which
examples happened to share a client, and the earlier caveat that 0.765 was one client's difference
on 34 was the right instinct: the resolution was never there.

**What this settles for RQ2.** The registrable claim is the detector's, not the classifier's: at
one false alarm in a hundred, a round holding two Qt clients is caught 81 to 91 percent of the
time when clients train on 128 examples against 46 percent at 64, and the ordering holds under a
repacking that halves the classifier's accuracy. That is the same operating-point distinction the
rest of RQ2 rests on, arriving here from a control rather than from an argument.

The third length, which asks whether the detector keeps climbing, is still the open item. It now
needs two packings to be worth running.

### The interval's own false-positive rate, measured rather than quoted (2026-09-19)

`datasets/results/interval-calibration.json`, `scripts/interval_calibration.py`. Section 7 of the
report states the pairs cluster bootstrap's false-positive rate under a true null. That figure has
been living in a comment in `measure/stats.py` since it was measured, with no artifact behind it,
which is the one thing a registered report cannot do with a number a reviewer will check.

It is now measured by the script, against the same `cluster_bootstrap` the gate calls, with the
dev window's own change sizes and the matched adapter arm's accuracy: 1,500 trials a point, 2,000
resamples, two-sided exclusion of zero against a nominal 5%.

**The rate the null is drawn at decides the answer, and the first run used the wrong one.** At the
base arm's 0.048 the interval excludes zero 2.9% of the time at 19 clusters, which reads as
comfortably conservative. The gate does not contrast base arms; it contrasts two adapter arms near
0.32, where the same interval returns 5.7%. A binary outcome's variance collapses at the extremes,
so calibration measured at an accuracy the study never operates at answers a question nobody asked.
The script now takes its rate from the matched adapter arm and sweeps the dependence explicitly.

### The field states source-hiding as a property and still does not measure it (2026-09-19)

`# research(2026-09)`. A sweep of this year's federated-LoRA privacy work, read for whether anyone
has now measured what RQ2 measures.

- **Fed-DiffLoRA** (IEEE Trans. Image Processing 2026) splits a client's adapter into orthogonal
  content and style subspaces and fuses the style half "while suppressing client-identifiable
  information". It is the first design in this family to name source-hiding as a goal, and it
  offers no attack against which the suppression is shown. Text-to-image, so the apparatus here
  cannot run it; it is the citation for why the measurement is missing.
- **AS-LoRA** (arXiv:2605.05769) picks A or B per layer and per round from a curvature score. Since
  A alone is the best identifier in our factor readings, a cut that moves between rounds is the
  case where leakage is not a property of the design at all.
- **Rethinking LoRA for Privacy-Preserving Federated Learning** (ICLR 2026) is about utility under
  differential privacy: gradient coupling, noise amplification, sharpness. No identifiability
  measurement, which is the pattern.
- **LoRA as Oracle** (arXiv:2601.11207) reads as source attribution in search summaries and is not:
  it audits a received model for backdoors from the geometry of a low-rank update. Recorded because
  the summary is misleading and the title will come up again.

Nothing measures whether a transmitted adapter identifies its source at an operating point an
attacker would use. The claim RQ2 registers is still unoccupied.

### Nine sources read in full, and what reading them changed (2026-09-19)

`# research(2026-09)`. The Stage 1 report carried ten `\needcite` badges because the intake
staged every entry as a search-level characterization. All but one are now cited: each source was
read against arXiv, the ACM Digital Library, the ACL Anthology or Springer, and four of the claims
built on them were wrong in a way a reviewer would have caught.

- **The conditional branch moves from rank 64 to rank 256.** Biderman et al. (TMLR 2024) tested
  ranks 16, 64 and 256 only, on Llama-2-7B with alpha = 2r, and found code instruction tuning
  ordered by rank from the first epoch: HumanEval 0.358, 0.417, 0.498, against 0.497 for full
  fine-tuning. Their recommendation is a rank of 256 "since ranks 16-64 tend not to suffice for
  code tasks". The branch existed to separate a null from insufficient capacity, and at rank 64 it
  would not have. In continued pretraining no rank closes the gap, which is a second reason this
  study's instruction-tuning setting is the one the paper speaks to.
- **`LORA` stays at rank 32, and its comment no longer claims performance flattens there.** The
  gate reads a difference between two adapters at the same rank, and the positive controls show
  this rank adapts. What changes is the justification: rank 32 is a compromise the branch covers,
  not a point the evidence identifies as sufficient.
- **Code2LoRA shows repository conventions by example, not by measurement.** Its quantitative
  results are exact match on assertion completion; conventions appear in an appendix of
  qualitative cases. The background section now says premise where it said finding.
- **Project acceptance history is secondary.** In the 66,329-interaction study the project's own
  acceptance ratio contributes 0.012 accuracy in the ablation, against 0.065 for the developer's
  history and 0.074 for the IDE version. The claim was true and the emphasis was not.
- **The 92.6% authorship figure is a ten-author closed set**, and at 636 authors the same setting
  gives 27.9%. On coursework, 0.2% over 690 authors sits just above its own 0.14% chance baseline;
  only the 812-author open-assignment set is strictly below chance.
- **Gold and Krinke's finding is scoped.** Their Gerrit dataset needed no ethics application under
  their local rules, on accessible data with no profiling, and they note review data carries higher
  risk than version control data because reviewers express opinions about work and its authors.
  The ethics section now carries both halves.
- **The COLING contamination paper does not cover Min-K%++.** It benchmarks five detectors,
  including its own Local Order Quiz, and its finding this study relies on is that contamination
  introduced by instruction fine-tuning escaped every method but one. Min-K%++ belongs to this
  study's battery.
- **CodeReviewer is a model name.** The ESEC/FSE 2022 paper is "Automating Code Review Activities
  by Large-Scale Pre-training"; the other title is the arXiv v1. The task shape and the exact-match
  metric are confirmed, and the paper reports BLEU beside EM.
- **The LLM-as-judge exclusion rests on the right paper.** arXiv:2604.24525 is about judging review
  comments, not about exact match on generated code, which is exactly what that paragraph claims:
  0.44 to 0.62 agreement on 2,604 industrial comments across three frontier models, EASE 2026.

The one badge left is the window assignment rule in section 5, which is a decision rather than a
citation.

### The two probes now differ on purpose, and the seal is enforced where months are read (2026-09-19)

`scripts/separability_over_time.py`. The review noted that this probe reads every built month,
dev included, where `separability.py` stops at train, and left it. The difference is right and was
undocumented: this one asks whether the corpus moves over time, and a drift reading that stops two
months early cannot see the end of its own series. It reads corpus text and no held-out
predictions, so the dev window is described rather than spent. The docstring now says that instead
of implying both probes follow one rule.

What was missing is a guard. The months came from whatever `datasets/gerrit/<org>/examples` holds,
so a test month fetched after acceptance would walk into a descriptive analysis by being on disk.
`by_month` now refuses any month at or past the sealed window and says so, and both readings record
`sealed_from`. Rerun on the committed drift case, the numbers are unchanged: OpenStack 0.552,
Qt 0.510, the same early and late slices.

### Cluster size carries no information about the contrast, measured (2026-09-19)

`scripts/cluster_informativeness.py`, `datasets/results/cluster-informativeness.json`. The
registration binds the gate to the pooled estimand, and one of its two reasons is Kahan et al.'s
condition: pooling over examples is wrong when a change's size carries information about its
outcome. The report quoted a correlation for that, and nothing on disk produced it.

Measured over the three registered seeds on the development window, per organization, between a
change's example count and its own matched-minus-mismatched difference:

| | r, median of 3 seeds | range | pooled minus change-averaged |
|---|---|---|---|
| OpenStack, 235 changes, largest 45 | -0.026 | -0.041 to -0.018 | -0.0142 (-0.0220 to -0.0087) |
| Qt, 462 changes, largest 25 | -0.006 | -0.020 to +0.013 | -0.0024 (-0.0076 to +0.0047) |

Both are indistinguishable from zero and change sign across seeds on Qt, which is the reading the
registration needs: the extra weight pooling gives a large change does not favour either arm. The
figures in the report are now these, with their spans, rather than a single draw.

The pilot is the contrast worth recording beside it: at 18 changes OpenStack's correlation is
-0.214 and the two estimands differ by 0.035, half the effect. Small cluster counts are where this
choice bites, which is why the condition is checked on the window the gate will read.

### FDLoRA and FedDPA read in full: what the adapter-instance cut actually transmits (2026-09-19)

`# research(2026-09)`. The two designs whose cut this apparatus has not measured, read from their
PDFs rather than from their abstracts, to settle what a measurement would have to implement. Four
load-bearing quotes were re-checked against each PDF's own text layer.

**Both send both factors, and nothing else.** FedDPA states it outright: "only the parameters of
the global adapter (LoRA) are transmitted to the server for aggregation", and its figure labels the
client-to-server arrows A and B separately, so it is not a merged product. FDLoRA uploads its
global module's parameters the same way. Neither transmits the thing that makes the design
personal: FDLoRA's fusion weights and FedDPA's instance-wise gate are both computed on the client,
the gate at inference time from locally sampled examples. So there is no gate, coefficient, mask or
statistic on the wire in either, and the attack surface is exactly what this apparatus already
reads.

**Neither claims the transmitted part hides its source, and neither runs an attack.** FedDPA states
the assumption instead: "the framework operates under the assumption that all clients are trusted
and legally entitled to access and utilize data stored on them, and the whole process does not
suffer from any attacks." FDLoRA lists "data privacy protection" among its advantages and supports
it with nothing; its own justification for withholding the personalized module is that it
"[ensures] that LLM adapts to local data", which is an adaptation argument, not a privacy one.

**The two cuts differ in whether the withheld tensor was ever separate.** FedDPA's local adapter
trains on top of a frozen global one and is never communicated, so the uploaded tensor is what is
left after the local adapter absorbs the residual. FDLoRA's boundary closes and reopens: its global
module is seeded by "the average of all clients' [personalized modules]", and every H rounds the
personalized module is overwritten by the uploaded one, so on a sync round the withheld adapter and
the transmitted adapter are the same numbers. FDLoRA also names the knob that decides how much
local signal the transmitted tensor carries: more inner steps give "more attention to local
knowledge", default three. That is the same shape as this study's own finding that leakage is a
function of how long a client trains locally.

**Recorded and not resolved:** FDLoRA's prose says stage two updates the personalized module while
its pseudocode updates the global one, and the paper does not say where the average of the
personalized modules is computed. Both matter for what an implementation would send, so a
measurement registers its reading rather than inferring one.

**What a measurement needs**, and why it is more than a new flag: clients that train two adapters
per round under two schedules, uploads recorded as A and B separately so factor-level and
product-level attacks both run, and a sweep over FDLoRA's inner steps and sync period, whose
endpoints are the sharpest test of whether its boundary exists in the numbers at all. The
attribution question is already answerable here, since every client carries one source.

### An adversarial review of today's measurement code, and what it changed (2026-09-19)

An independent reviewer was given the three pieces of code whose numbers reach the report and told
to reproduce rather than read: the interval calibration, the informativeness reading, and the
harvest that asserts every quoted figure. It ran its own implementations and its own mutants. What
it found, and what each one cost:

- **A sentence in section 7 was false.** "The median-seed rule reaches 0.122 two-sided where the
  crossed interval holds nominal" is true of the median-seed half only: at the seed effect that
  produces 0.122, sigma_b 0.02, the crossed interval is 0.073, not nominal. The honest cell is
  0.01, where the two are 0.062 and 0.046, and that is the regime this study's measured seed effect
  puts the gate in. Both cells are now stated and both are harvested.
- **The harvest could not fail on three of its own claims.** The prose check was a substring test,
  so the literal `0.0` for the Jaccard 0.8 leakage rate matched inside `0.075` and the claim was
  vacuous on both sides. A JSON boolean resolved as a number, so `false` verified the exactly-zero
  bound the verdict hangs on. And that bound passed at +0.00003, which flips the verdict while
  still printing +0.0000. Matching is now bounded and counted per claim, booleans are refused, the
  bound is asserted exactly, and the verdict word itself is asserted. Each was reproduced as a
  mutant and each now fails.
- **The calibration's decimals were one draw each.** At 1,500 trials the standard error is about
  0.6 points, the size of the differences between adjacent ladder points, and the reviewer's
  independent recomputation at fifteen times the budget found the true curve declines monotonically
  where our draws wobbled. The budget is now 6,000 and the report states the range and the regime
  rather than a sequence.
- **The gate's own error rate was asserted rather than measured.** The artifact recorded the
  two-sided rate while the gate reads one side; the report said these "roughly halve". The
  one-sided rate is now counted in the same loop.
- **Zero correlation was also the value for "no variance".** `cluster_informativeness` returned 0.0
  whenever a side did not vary, which is exactly the value that reads as maximal support for the
  registered estimand. It returns NaN, and the summary refuses a run that contributes no row, a
  duplicate run, or a seed count other than the one the report claims.
- **Stale ladder rows could survive a merge** under a provenance block describing only the last
  process. A row measured under another window, arm, rate or budget is dropped with a line saying
  what it was.

**Recorded, not changed:** OpenStack's pooled contrast is exactly 13/565 at all three seeds. The
underlying runs genuinely differ, with 44 to 47 per-example flips between seeds and distinct losses
and adapter norms, but the net change is identical in both arms all three times. Qt shows no such
lockstep. Under near-independence that is on the order of one in a thousand, and it is the reason
OpenStack's seed component carries no spread. It deserves a sentence before a reviewer asks.

### The Qt result was the null's size, not Qt (2026-09-19, jobs 149957, 150085)

`*-cpp-rebuilt-c128.json`. The ten-project AOSP rebuild gives seven C++ projects that can fill a
client at 128 examples, against Qt's six, so the project-level permutation enumerates
C(13, 7) = 1,716 distinct groupings where every earlier run had 84. Same training length, same
initialization, a larger and better-balanced project set.

| within C++, 128 examples a client | 3 AOSP projects, 84 groupings | 7 AOSP projects, 1,716 groupings |
|---|---|---|
| AOSP detector, rounds of 16, two clients | AUC 0.940, TPR 0.588 | **AUC 0.972, TPR 0.787** |
| Qt detector, rounds of 16, two clients | not measured at 16 | AUC 0.928, TPR 0.564 |
| AOSP project permutation | p 0.012 at the floor, all seeds | **p 0.0006 at the floor, all seeds** |
| Qt project permutation | p 0.018 median (0.012 to 0.036) | **p 0.070 median (0.028 to 0.143)** |
| organization within C++, nearest class | 0.765 against a 0.735 majority | 0.646 against a 0.521 majority, p 0.030 |
| organization beyond project, C++ | 0.676, p 0.19 | 0.583, p 0.30 |

**AOSP's result strengthens by a factor of twenty in the null it survives.** A p of 0.0006 is the
floor of 1,716 groupings: no relabeling of which projects belong to which organization scores as
well as the true one, at any of four round-draw seeds. The detector reads 0.787 at one false alarm
in a hundred.

**Qt's does not survive.** At 84 groupings and this training length Qt sat near the floor, and the
second packing had already softened that to a median 0.042. With thirteen projects and a null
twenty times larger it is 0.070, and two of four seeds are above 0.10. The earlier reading came
from the small null and the three-project AOSP set it was built against, not from Qt.

**What this costs and what it buys.** The claim that both organizations become detectable at 128
examples a client is withdrawn. What replaces it is stronger where it holds: one organization is
detectable from projects other than the target's, at the floor of a null that enumerates every way
its projects could have been relabelled, and the detectability rises with local training length.
The asymmetry between the two organizations is now the finding rather than an inconvenience, and it
is the same asymmetry the RQ1 side sees, where AOSP behaves as one unit and Qt does not.

The classifier is unchanged by the rebuild: 0.583 beyond project at p 0.30, with classes nearly
balanced at 23 against 25, so the operating-point distinction that the rest of RQ2 rests on holds.

### The third length: at 256 examples a client the classifier stops failing (2026-09-19, jobs 149971, 150086)

`*-cpp-256-c256.json`. Thirty-six clients, thirty-two of them C++, over five AOSP projects and four
of Qt's: the largest set this corpus supports at this size, since a project needs 256 usable
examples after deduplication to fill even one client.

| within C++ | 64 | 128, 3 AOSP projects | 128, 7 AOSP projects | 256, 5 AOSP projects |
|---|---|---|---|---|
| organization, nearest class | 0.395 (maj 0.698) | 0.765 (maj 0.735) | 0.646 (maj 0.521) | **1.000 (maj 0.531)**, p 0.0001 |
| organization, beyond project | 0.395, p 0.75 | 0.676, p 0.19 | 0.583, p 0.30 | **1.000, p 0.0079** at the floor |
| AOSP detector, rounds of 16, two clients | AUC 0.858, TPR 0.364 | AUC 0.940, TPR 0.588 | AUC 0.972, TPR 0.787 | **AUC 0.995, TPR 0.985** |
| Qt detector, rounds of 8, two clients | AUC 0.859, TPR 0.456 | AUC 0.980, TPR 0.910 | AUC 0.855, TPR 0.281 | **AUC 0.972, TPR 0.763** |
| AOSP project permutation, median of 4 seeds | 0.012 (floor) | 0.012 (floor) | 0.0006 (floor) | 0.016 (floor 0.008) |
| Qt project permutation, median of 4 seeds | 0.250 | 0.018 | 0.070 | 0.016 (floor 0.008) |

**The control that had held the classifier back stops holding.** Beyond-project attribution splits
the attacker's reference over projects, so an organization must be recognised from projects other
than the target's own. It read 0.395 at 64 examples, 0.583 at 128 on the rebuilt set, and it reads
1.000 at 256, at the floor of its null. Every earlier statement that organization is not a class a
classifier can assign was true of the training lengths it was measured at, and is false here.

**The detector saturates.** AOSP catches 98.5% of rounds holding two of its clients at one false
alarm in a hundred, at an achieved false-positive rate of zero. There is no headroom left in this
instrument at this length, which is itself the result: the question "how much does a deployment
leak" has an answer that depends on a knob the deployment sets, and at the top of the range
measured here the answer is nearly everything the attacker asked for.

**What this is not.** Only the largest projects survive at 256 examples a client, so composition
moves with length and this is a third point on a ladder rather than a controlled doubling. The null
is 126 groupings against the rebuild's 1,716, so a permutation at the floor here is weaker evidence
than the same statement there. And both organizations' permutations sit at 0.016 median, which is
below 0.05 but not at the floor at every seed.

**The registrable claim is now about the knob rather than the organization.** Leakage is a function
of how long each client trains locally: at 64 examples neither instrument identifies an
organization beyond its projects, at 128 one of the two does under a detector, and at 256 both do
under a detector and a classifier does as well. A deployment that trains longer locally, which is
what one does to get more out of federated fine-tuning, leaks more about who its participants are.

### The registered subspace tests pass, and the cut is what makes the source readable (2026-09-19, job 150123)

`datasets/results/subspace-split-cpp-rebuilt-c128.json`. Two tests were registered for the
ten-project rebuild before it existed: the detector on the shared half at rank 4, and the
classifier on the residual at rank 1. Both are now run on 48 C++ clients over thirteen projects,
with the attribution basis fitted without the scored client's whole project and the null the same
1,716 groupings the detector uses.

| | accuracy, held out by project | p, corrected over the rank sweep |
|---|---|---|
| the whole update | 0.542 | 0.39 |
| shared half, best cell (rank 4) | **0.917** | **0.0012** |
| residual, best cell (rank 1) | **1.000** | **0.0006**, the floor |

Per rank, the shared half identifies at 2 and 4 and falls to chance from 8 upward, while the
residual identifies at every rank to 16 and fades by 32. The detector reverses the roles: on the
shared half it reaches AUC 0.974 with 77% of two-client rounds caught at one false alarm in a
hundred at rank 2, and on the residual it runs inverted, AUC 0.274, as it did on the smaller
corpus. So each half has its instrument, and the study reads each half with the one that works.

**The finding is the cut itself.** The intact update identifies nothing here: 0.542 against a 0.521
majority at p 0.39. Split it along the subspace SDFLoRA aligns across clients, and both pieces
identify the organization, the transmitted piece at 0.917. Removing the directions that clients
share does not remove what distinguishes them; it removes what dilutes the distinction. A defence
that keeps the residual local therefore transmits a half that is more readable than the whole it
came from, which is the opposite of the property it is offered for.

This supersedes the "not established, rank- and instrument-dependent" reading from the nine-project
corpus, where the baseline was below chance and the rank was chosen after the fact. Here the ranks
were registered, the sweep is corrected for, and the baseline is the whole update measured the same
way.

### A alone is still the leak, and on the rebuilt corpus it is the only reading that finds Qt (2026-09-19, jobs 150133, 150134)

`aggregate-attack-cpp-rebuilt-c128-{a,b}.json`. FedSA-LoRA shares A and keeps B local, and its
reason is that A is the more similar factor across clients. The same 48 C++ clients over thirteen
projects, read three ways, at rounds of sixteen holding two of the target's clients:

| reading | AOSP AUC | AOSP TPR at 1% | AOSP p | Qt AUC | Qt TPR at 1% | Qt p |
|---|---|---|---|---|---|---|
| the update, B A | 0.972 | 0.787 | 0.0006 | 0.928 | 0.564 | 0.100 |
| **A alone**, transmitted | **0.983** | **0.839** | **0.0006** | **0.986** | **0.863** | **0.023** |
| B alone, kept local | 0.970 | 0.773 | 0.0006 | 0.923 | 0.541 | 0.106 |

**A beats the product and B on every cell**, which is what the nine-project corpus said and what an
adversarial review could not break there. The new thing is Qt: the product leaves it unremarkable
among relabelings (0.100) and so does B (0.106), while A finds it at 0.023. The organization this
apparatus could not identify from its whole update is identifiable from the half the design
transmits.

That is now the second design whose transmitted half is more readable than the whole update it came
from, after SDFLoRA's shared subspace. The pattern in both is the same: the part chosen for being
common across clients is the part where what is not common stands out.

### The subspace cut at the third length: still a leak, no longer a revelation (2026-09-19, job 150124)

`subspace-split-cpp-256-c256.json`. The same registered split at 256 examples a client, on 32 C++
clients over nine projects, against 126 groupings.

| | 128 examples, 48 clients, 1,716 groupings | 256 examples, 32 clients, 126 groupings |
|---|---|---|
| the whole update | 0.542, p 0.39 | **0.906, p 0.040** |
| shared half, best cell | 0.917, family-wise p 0.0012 | 0.906, family-wise p 0.032 |
| residual, best cell | 1.000, family-wise p 0.0006 | 1.000, family-wise p 0.008, the floor |

Both halves still identify the organization, and the residual is still perfect at rank 1. What
changes is what they are being compared against: at 128 examples the intact update identified
nothing, so cutting it was what made the source readable; at 256 the intact update identifies too.

**So the cut's effect is regime-dependent, and the sharper claim belongs to the shorter length.**
At the training length where a whole update hides its source, splitting it along the subspace
clients share exposes that source. At the length where everything is exposed, the split neither
helps nor hides. A defence evaluated only at long local training would therefore report that its
cut costs nothing, and be right about the wrong regime.

### Masking still helps this detector, in both organizations, and one row says why the first attempt missed it (2026-09-19)

`defence-curve-cpp-rebuilt-c128-mid.json`. The masking result was measured on the nine-project
corpus at 64 examples a client. This repeats it on the rebuilt corpus at 128, content held to C++,
48 clients, at rounds 10, 30 and 50 with six mask sizes.

Read as distance from chance, which is what an attacker who knows the sign obtains, five of the six
rows peak at a 1x mask rather than at no mask at all:

| | none | 0.5x | 1x | 2x | 4x | 8x |
|---|---|---|---|---|---|---|
| AOSP, 10 rounds | 0.379 | 0.383 | **0.385** | 0.377 | 0.342 | 0.263 |
| AOSP, 30 rounds | 0.466 | 0.471 | **0.475** | 0.474 | 0.457 | 0.391 |
| AOSP, 50 rounds | 0.484 | 0.487 | **0.489** | 0.488 | 0.477 | 0.430 |
| Qt, 10 rounds | **0.127** | 0.127 | 0.124 | 0.115 | 0.091 | 0.053 |
| Qt, 30 rounds | 0.216 | 0.222 | **0.225** | 0.218 | 0.186 | 0.129 |
| Qt, 50 rounds | 0.266 | 0.280 | **0.290** | 0.290 | 0.258 | 0.185 |

**At the operating point the gain is larger than the area suggests.** AOSP's true-positive rate at
one false alarm in a hundred goes 0.312 to 0.352 at 10 rounds, 0.637 to 0.711 at 30, and 0.794 to
0.885 at 50, all peaking at a 1x mask. The AUC moves by five thousandths over the same cells. A
defence tuned on the area would call a nine-point gain in catch rate a rounding error, which is the
Carlini point arriving from the defender's side.

**Qt's detector runs inverted here**, AUC 0.21 to 0.37 across its rows, so a round holding two of
its clients looks less like Qt's reference direction than a round without them. That is usable
signal once the rule is flipped, and the TPR column reads near zero for Qt only because it assumes
the unflipped rule. The inversion is not the content-pooling artefact recorded earlier: content is
held fixed here. It fits the rest of the picture, where AOSP's projects share a direction and Qt's
do not.

**Why the first attempt looked like a null.** The script's default rounds are 1, 10 and 100. On
this corpus AOSP's detector reaches 0.997 with 94% of rounds caught by 100 rounds, so a mask has no
room to raise it, and at 1 round averaging has not yet opened the gap the effect depends on. The
informative band is in between, which is why this run uses 10, 30 and 50. A defence evaluation that
takes the defaults would report no effect and be wrong about the regime rather than about the
mechanism.

### The two items the reviews left open, closed (2026-09-21)

The three adversarial reviews on 2026-09-18 and 2026-09-19 each ended with a list recorded rather
than fixed. Re-reading those lists against the code, most had been overtaken: the permutation now
has behavioural tests rather than text searches, the enumeration counts distinct groupings, the
overwrite guard claims exclusively at job start, and the contamination battery's partial output is
claimed under its own name. Two were still standing, and both are closed here.

**The family-wise step had no test, and it carries a quoted number.** Choosing the rank after
seeing the table is a search, so `subspace_split.py` compares the family's best cell to the null's
best cell over the same ranks, taken per relabeling. That was ten lines inside `main()`, unreachable
from a test, and it produces the 0.0012 that the report gives for SDFLoRA's shared half. It is now
`max_over_ranks(cells, null_size)`, and five mutants that the previous suite would have accepted
each fail: taking the minimum over relabelings rather than the maximum, scoring the best cell
against its own null instead of the family's, pooling every cell's null draws into one maximum,
counting degenerate cells as part of the family, and comparing strictly so the truth no longer ties
itself and the p-value loses its floor. All six committed family-wise values re-derive from the
extracted function unchanged, the rebuilt corpus's 0.001166 among them.

**A killed job's claim could not be told from a live one.** `claim_result` creates the result path
exclusively and fills it later, and an EXIT trap releases it if the job never writes. Bash runs that
trap even when a fatal signal takes it, so a cancel or a time limit releases cleanly; SIGKILL does
not, and the empty file it strands is byte for byte what a running job's fresh claim looks like. A
requeue of that same job then refused its own leftover. The claim now records the job that made it
in a `.claim` beside the result, and an empty claim is treated as abandoned only on evidence: its
owner is this job, which a requeue keeps, or Slurm no longer lists that job. A claim with a result
in it is never reclaimed, nor is an empty one whose owner cannot be read or whose owner is still
queued, since that is exactly what a live claim looks like. Eight tests hold those cases, four of
them driving a real job to its claim and killing the process group.

**What an independent review of the claim found, and it was worse than the bug it fixed.** A
reviewer given the claim functions and told to reproduce rather than read built the input matrix
and broke the fix twice, both times by remembering that TIGRIS and SPORC are two Slurm
installations over one `$HOME`. `squeue` answers for the cluster it runs on, so a foreign job id
comes back unknown, which the fix read as "gone" and reclaimed: a job genuinely running on the
other cluster loses its claim, and whichever finishes last overwrites the other's measurement with
no error and no OVERWRITE. Worse, the two clusters run independent id counters, so a job whose own
id happens to equal the record's took the shorter path and reclaimed without asking Slurm at all.
The owner is now a cluster and an id, both branches require the cluster to match, and a claim from
the other cluster is refused with a message naming it rather than guessed at. A record from before
the cluster was written names nobody and is refused too.

Two smaller things from the same review, both fixed: sourcing the environment twice reset the
claims list, so a claim made before the second source outlived the exit that should have released
it; and a script that sets its own `trap ... EXIT` silently replaces the release, which no job does
today and none may, so the suite now refuses one that does.

**What an independent review of that extraction added.** A reviewer given the function and the
committed results, and told to reproduce rather than read, confirmed the six values and killed
eleven mutants of its own, including the five above. It found one the suite could not see: the
`null_size` argument was unconstrained, so a cell carrying fewer relabelings than the caller
claimed truncated the family silently and read as more significant, while one carrying more raised
an IndexError. The k-th entry has to mean the same relabeling in every cell for the maximum to be
a correction at all, so the function now refuses a family whose cells disagree with the declared
null, and refuses an empty null rather than returning a NaN p that `f"{p:.3f}"` prints as "nan".
The same review confirmed the relabelings do line up: `main()` builds the null once, before the
rank loop, and passes that one list into every cell.

**Recorded, not changed:** `balanced_accuracy` is reported beside `accuracy` at every rank and is
not family-wise corrected, because `scored()` keeps only the accuracy column of the null. Nothing
quotes a family-wise p for it, so no number is wrong, but the asymmetry should be either closed or
stated before the report goes out.

One thing found on the way and left alone: a `trap 'exit 143' TERM` added to carry a cancel to the
release turned out to be redundant, since bash already runs the EXIT trap on a fatal signal. It was
removed rather than kept as insurance, and the test written for it now states the behaviour bash
actually has.

### The adapter-instance cut transmits the source, and its own schedule sharpens it (2026-09-21, jobs 150272 and 150904)

`*-dual-cpp-rebuilt-c128-t2.json`. FedDPA's iterative variant on the rebuilt corpus, 128 examples
a client, two rounds: each client holds a global adapter that is communicated and a local adapter
that never is. What the server receives is the global half, and that is what the two instruments
read here. Neither FedDPA nor FDLoRA attacks the half that leaves; FedDPA states the assumption
instead, that "all clients are trusted ... and the whole process does not suffer from any attacks".

**The detector loses a little and keeps everything that matters.** Against the single-adapter run
on the same corpus at the same client size:

| | single AUC | transmitted AUC | single TPR at 1% FPR | transmitted TPR |
|---|---|---|---|---|
| AOSP, rounds of 16, 2 clients | 0.972 | 0.965 | 0.787 | 0.732 |
| AOSP, rounds of 8, 2 clients | 0.963 | 0.944 | 0.716 | 0.600 |
| AOSP, rounds of 4, 2 clients | 0.966 | 0.952 | 0.698 | 0.624 |
| Qt, rounds of 16, 2 clients | 0.928 | 0.907 | 0.564 | 0.493 |

The project-level permutation is unmoved: AOSP sits at the floor of 1,716 groupings at every seed
in both runs (p = 0.0006), and Qt is found by neither, at a median of 0.070 both times. Withholding
a personal adapter costs the attacker five to twelve points of catch rate and no significance.

**The classifier reads the transmitted half better than the whole single adapter.** Nearest-class
attribution beyond the target's own projects goes from 0.583 at p 0.30 on the single adapter to
0.750 at p 0.036 on the transmitted half, over the same 1,716 relabelings. That is the opposite of
what the cut is for, and the reason is the cut's own schedule: the iterative variant trains the
global adapter once per round, so at two rounds the communicated adapter has had two passes over
the client's data against the single-adapter run's one. Leakage is a function of how long each
client trains locally, which this study measured before touching FedDPA, and the personalization
schedule buys its personalization with exactly that. The comparison is therefore not cut against
no cut; it is a cut that costs the detector a little while handing the classifier the extra local
training it needs.

What this cannot say yet is what the withheld half holds, which is the comparison the papers never
make. That geometry is job 162578.

### The nearest neighbour now shares our venue and our retired word, for a different unit (2026-09-21)

`# research(2026-09)`. Two papers by the same author attribute *agents* from pull requests, and
both are close enough to RQ1 that a reviewer will raise them.

**Fingerprinting AI Coding Agents on GitHub** (Ghaleb, MSR '26, arXiv:2601.17406) reads 33,580 PRs
from Codex, Copilot, Devin, Cursor and Claude Code, builds 41 features over commit messages, PR
structure and code characteristics, and reports 97.2% macro F1 from XGBoost for "identifying the
submitting agent". **AgenTag** (arXiv:2608.00966) extends the same unit to an open-world setting,
"a multimodal, learned, open-world problem rather than a closed-set classification task", at
weighted F1 0.96 and AUC 0.84 for unseen agents.

**What they are not.** The unit is the tool that wrote the change, never the organization that
reviewed it. The first paper's only use of our unit is as a threat: "We do not test generalizability
to private repositories or other platforms (e.g., GitLab), where agent behavior may differ due to
stricter style guides or organizational practices." Organizational practice is the confound they
cannot control, which is the thing this study measures. Their evidence is hand-built features over
commit and PR metadata read by a tree ensemble, not what a model learns from review content, and
neither paper reports a true-positive rate at a fixed false-positive rate: macro precision, recall
and F1 in the first, F1 and AUC in the second. The first reports Claude Code at 57% recall with 82%
precision and does not say what the operating point costs, which is the reading this study refuses
to leave out.

**What this changes here.** Nothing in the design, three things in how it is written.

1. The related-work section gains these two as the nearest neighbours in our own venue, and the
   contrast is stated as unit, evidence and operating point rather than as novelty by absence.
2. Retiring "fingerprint" was decided on other grounds and is now also forced: in MSR '26 the word
   names agent attribution, and using it for organizational house style would collide with a paper
   the same readers have just seen.
3. AgenTag's future work names the closest anyone comes to our question and leaves it open:
   "quantify how repository-specific instruction files (e.g., CLAUDE.md, AGENTS.md) affect
   behavioral fingerprints and attribution accuracy." Project-level configuration as a modifier of
   attribution is one step from organizational convention as the thing attributed, and nobody has
   taken it.

### What FedDPA withholds is the half that identifies best (2026-09-21, job 162578)

`*-dual-cpp-rebuilt-c128-t2-local.json`. The same run, the same clients, the same two rounds: the
local adapter each client keeps, read by the two instruments that read the global one it sends.
Both halves had two training passes over the same 128 examples, so this comparison carries none of
the length confound that separates the dual run from the single-adapter one. Neither FedDPA nor
FDLoRA makes it.

**The classifier finds the organization perfectly in the half that never leaves.** Nearest-class
attribution beyond the target's own projects, within C++:

| | accuracy | p over 1,716 relabelings |
|---|---|---|
| single adapter | 0.583 | 0.302 |
| the half FedDPA transmits | 0.750 | 0.036 |
| the half FedDPA withholds | **1.000** | **0.0006**, the floor |

**The detector swaps which organization it can see.** On the transmitted half AOSP sits at the
permutation floor and Qt does not; on the withheld half that reverses.

| rounds of 16, two of its clients | transmitted AUC / TPR | withheld AUC / TPR |
|---|---|---|
| AOSP | 0.965 / 0.732 | 0.659 / 0.094 |
| Qt | 0.907 / 0.493 | 0.822 / 0.329 |

| project permutation, median p | transmitted | withheld |
|---|---|---|
| AOSP | 0.0006 (floor) | 0.115 |
| Qt | 0.070 | 0.009 |

**What this says about the defence.** The adapter-instance cut is the only one of the four splits
read here that holds back more than it sends. It is still not source-hiding: the transmitted half
alone puts AOSP at the floor of 1,716 groupings at every seed and catches 73% of two-client rounds
at one false alarm in a hundred. But the half kept local is where the organization is most legible,
1.000 against 0.750, and for Qt it is the only half a detector can read at all. A cut that leaves
the source readable in the part it transmits is the pattern for the other three; this one leaves
the source *more* readable in the part it does not, which is a different statement and a better one
for the defence.

The mechanism is the schedule rather than the instance. The local adapter trains alongside the
frozen global one, so it fits what the global adapter has not already explained, and what is left
over is the client's own. That is exactly the quantity personalization is for, and it is the
quantity an attacker wants. The defence works here because the two coincide, not because the
instance boundary hides anything.

### Registered before computing: how we read FDLoRA's algorithm, and the step it does not account for (2026-09-21)

`# research(2026-09)`. FDLoRA (Lu et al., arXiv:2406.07925) is the remaining half of the
adapter-instance cut. Its paper leaves three things a measurement must decide, so the reading is
registered here, before any of it is implemented or run.

**The paper's own claim.** "The personalized LoRA primarily focuses on acquiring knowledge from
local data and remains uninvolved in the federated learning process. The global LoRA aggregates
knowledge from diverse clients." Privacy is asserted twice and measured nowhere: the framing
paragraph credits FL with "mitigating privacy risks associated with transmitting private data to
central servers", and the conclusion lists "data privacy protection" among FDLoRA's benefits.

**1. The personalized module does leave the client, once, and the paper does not say so.**
Algorithm 1 line 7 initializes the shared module from the personalized ones:
`theta_s(0) <- (1/N) sum_i theta_p(i)`, described as "the average of all clients theta_p(i) is used
to initialize the global LoRA parameters theta_s for the subsequent federated learning process".
An average over N clients is computed where all N are visible, so either the personalized modules
are uploaded once or their average is, and in both cases the tensor the design promises never
participates is the seed of the one that does. The paper states no mechanism, secure aggregation or
otherwise, and its "remains uninvolved" sentence is written as though line 7 were not there. **Our
reading:** the server receives each client's personalized module at initialization, because that is
what the pseudocode's placement outside the per-client loop describes and it is the only reading
that needs no unstated machinery. This is registered as a *finding about the design*, not a
modelling convenience: our attacks read the personalized modules at round 0 as transmitted, and the
measurement reports what that one round costs.

**2. Prose and pseudocode disagree about what the inner loop trains.** The prose says each client
"updates its personalized LoRA module parameters (Algorithm 1, line 12)"; line 12 reads
`theta_s(i)(t) <- InnerOpt(theta_s(i)(t), D(i), K)`, which updates the global module. **Our
reading: the pseudocode.** The outer step at line 17 differences the global module against the
dispatched one, so an inner loop that trained the personalized module would send a zero update and
the federation would learn nothing. The prose is the error.

**3. The sync test is written so that it fires when it should not.** Line 9 is
`is_sync <- t % H` and line 14 overwrites the personalized module with the locally optimized global
one. As written, `t % H` is true whenever `t` is *not* a multiple of `H`, so the personalized module
would be overwritten on every round except the periodic one. **Our reading: `t % H == 0`**, the
only reading consistent with "update the personalized LoRA module every H communication rounds" in
the comment directly above it, and with calling `H` the asynchronous update frequency.

**What this predicts, registered before it is run.** Our FedDPA result says the leak lives in the
residue the local adapter fits once the frozen global one has explained what it can. FDLoRA's
boundary closes and reopens: every `H` rounds the personalized module is overwritten by the global
one, so the residue is destroyed and rebuilt rather than accumulated. If the residue account is
right, then as `H` falls the two halves should converge and the transmitted half should carry more
of the source, with `H = 1` the endpoint where the withheld half is a copy of what was just sent.
`K`, the inner steps, moves it the other way, by the paper's own words: "Increasing K will give
more attention to local knowledge", default 3. A sweep whose endpoints are `H = 1` against large
`H`, and `K = 1` against `K` well above 3, is therefore a test between the residue account and an
account where the instance boundary itself does the work. We predict the residue account: the
withheld half's advantage should shrink monotonically with `H`.

### Registered before computing: the placebo gate, and how it will be read (2026-09-21)

The registered gate compares an adapter trained on one organization against one trained on
another, on the first organization's held-out refinements. It cannot say on its own that the
*organization* is what the adapters learned. A project, a codebase family or a document type would
all produce the same reading, and this study's own separability probe already reports that two
projects inside one organization separate as well as two organizations do (0.828 and 0.872 within,
against 0.849 across, on matched Python).

**The control.** One organization's projects are split into two halves and each half is written as
its own pseudo-organization, then the registered runner is pointed at the result with nothing else
changed: same windows, same equalisation, same three seeds, same fp32, same crossed interval. The
split is deterministic and unseeded, projects sorted by training-window example count and each
assigned to whichever side is smaller so far, so the partition is a function of the corpus and
cannot be reshuffled until the control behaves. Built: Qt 38 and 36 projects at 4,539 and 4,538
train examples, evaluating on 601 and 415; OpenStack 122 and 124 projects at 2,346 each,
evaluating on 338 and 264.

**How it will be read, fixed here rather than after the numbers land.** The placebo arms evaluate
on roughly half the examples the real arms do, so a placebo interval that covers zero is weaker
evidence than a real interval that excludes it, and "the placebo did not pass" is not by itself the
result. The comparison is between *point estimates at comparable precision*: we report the placebo
contrast beside the cross-organization contrast with both intervals, and read

- a placebo point estimate of the same order as the cross-organization one as evidence that the
  gate responds to any project boundary and the organization is not the unit, whatever either
  interval does;
- a placebo near zero with the cross-organization contrast clearly above it as evidence that the
  organizational boundary is carrying the effect;
- a placebo interval so wide that both readings sit inside it as an underpowered control, reported
  as such, with the pooled dev size the reason.

**What it does not settle.** Within one organization both halves write the same languages, so the
placebo removes the language difference along with the organizational one. It therefore separates
"organization" from "any project boundary" and says nothing about language. The language confound
needs a cross-organization pair that shares a language, which the current two do not: the Qt dev
window holds eight Python examples against OpenStack's 243, so a matched arm is not evaluable on
this pair at all.

### AOSP stopped reviewing in public, and it takes the language-matched arm with it (2026-09-21)

`# research(2026-09)`. The gate compares an OpenStack adapter against a Qt one. The two
organizations barely share a file type, so an adapter that learned nothing but the language would
beat the other's on its own held-out data and pass. The window reports now carry the histogram the
variables table promised: OpenStack's training window is 2,054 Python and 1,184 reStructuredText
of 4,327; Qt's is 4,211 C++, 1,427 qdoc and 807 headers of 8,442. In the dev window Qt holds
**eight** Python examples against OpenStack's 243, so the matched arm cannot be evaluated on this
pair at all.

AOSP was the obvious fix, already fetched, C++ like Qt. It is not available, and the reason is
upstream rather than ours.

| months | merged changes fetched per month | examples built |
|---|---|---|
| 2024-11 to 2025-03 | 684 to 964 | 207 to 450 |
| 2025-04 to 2025-10 | 26 to 63 | 0 to 6 |

`aosp-main` became read-only on **2025-03-27** and all Android development moved to Google's
internal branches; external contributions still reach the public Gerrit, which is the residue the
later months show. The cliff in our own corpus sits exactly there, and it was measured before the
cause was looked up.

**What it costs, in three places.**

1. **RQ1 cannot use AOSP as a third arm.** Its dev window holds three examples. No widening of the
   project list fixes a review process that stopped: the ten projects fetched average one to six
   examples a month since April 2025.
2. **RQ2's AOSP results rest entirely on the period before the change.** They were built with
   `--window all` and are unaffected as measurements, but the organization they describe no longer
   reviews in public at that volume, and the study should say so rather than let a reader assume
   the corpus could be extended.
3. **The sealed test window would be nearly empty for AOSP**, so this is permanent, not a gap to
   wait out.

**What is still available.** Probed directly, three public Gerrit hosts answer and are busy in the
dev window's own months, each returning a full page of merged changes with more behind it where
AOSP returned 43 for the month: Chromium (`chromium/src` dominant, C++), Fuchsia (C++) and Go. Of
the three, Chromium is the natural language-matched partner for Qt on size and language. Whether a
review culture yields *anchored* inline comments at a usable rate is a separate question that only
a build answers, and AOSP's own drop counts are the warning: 15,880 author comments and 7,305
unanchored hunks against 5,133 usable examples. Adding a host is also a corpus-scope decision that
touches the ethics determination, so it is recorded here rather than taken.

### Chromium yields reviewer comments at Qt's rate, and the two live organizations are stable to the seal (2026-09-21)

`# research(2026-09)`. Two measurements, both taken because the AOSP collapse made them necessary.

**The yield a candidate host actually delivers.** Volume is not the quantity the corpus is built
from: AOSP produced 5,133 usable examples while dropping 15,880 author comments and 7,305
unanchored hunks. What matters is inline comments that carry a line anchor and were written by
somebody other than the change owner. Measured directly against the Gerrit API over 2025-10,
nothing written into the corpus:

| host | changes read | anchored comments | by a reviewer | per change |
|---|---|---|---|---|
| chromium-review, `chromium/src` | 25 | 33 | 17 | 0.7 |
| codereview.qt-project.org | 10 | 8 | 5 | 0.5 |

Chromium's review culture yields at least as well as Qt's, and `chromium/src` alone merges
hundreds of changes a month, so a Chromium arm is not volume-limited. Two caveats stand. The
design refuses an organization of one project, since it could not then be told apart from that
project, so a Chromium arm means several of its projects rather than `chromium/src` alone. And
adding a host is a corpus-scope decision that touches the ethics determination, which the report
says is initiated through the institution rather than asserted by the plan.

**The two organizations the study already has are not going the way AOSP went.** Merged changes a
month, from the fetch records, through the last month before the seal:

| | 2024-10 to 2025-03 | 2025-04 to 2025-10 |
|---|---|---|
| Qt | 3,681 to 4,923 | 3,321 to 4,814 |
| OpenStack | 1,175 to 2,480 | 1,746 to 2,771 |

Neither trends down, and neither shows anything like AOSP's ninety-five percent drop. This is a
check on the *sealed* window that does not spend it: the test window cannot be inspected, so the
best available evidence that it will carry data is that change volume is steady right up to its
boundary. It bounds volume only. Whether the content of those months resembles the training
window is a separate question, and the drift probes are what answer it.

### The placebo fires, and rank 256 does not rescue the null arm (2026-09-22, jobs 164557, 164558, 164573)

Registered before these ran: a placebo point estimate of the same order as the cross-organization
one is evidence that the gate responds to any project boundary and the organization is not the
unit, whatever either interval does. It is.

| boundary | contrast | interval |
|---|---|---|
| Qt against OpenStack, three seeds (registered) | +0.0316 | [+0.0089, +0.0567] |
| **Qt's own projects, split in half** | **+0.030** | **[+0.002, +0.060]** |
| the other half of that split | -0.005 | [-0.041, +0.030] |
| OpenStack against Qt, three seeds (registered) | +0.0230 | [+0.0000, +0.0457] |
| **OpenStack's own projects, split in half** | **+0.026** | [-0.021, +0.068] |
| the other half of that split | +0.004 | [-0.040, +0.048] |

A boundary with no organizational meaning reproduces the organizational effect, on both
organizations, at the same magnitude. The outcome-neutral tests pass in every arm, so this is the
apparatus working rather than failing: positive control, manipulation and leakage all hold on each
pseudo-organization.

**Rank 256 answers the capacity objection, and the answer is no.** OpenStack's arm goes from
+0.0230 at the registered rank to +0.007 [-0.019, +0.031]; Qt's holds at +0.033 [+0.005, +0.061]
against +0.0316. Insufficient adapter capacity is not why OpenStack returns no gain, which is the
one legitimate reason to have reached for a larger rank.

**What these are not.** All three ran at a single seed. `SEEDS=1,2,3` in `--export` is parsed by
Slurm as `SEEDS=1` plus two further export items, so the seed list never reached the runner, and
the result files record `seeds: [1]`. Seeds 2 and 3 for all three are queued as their own jobs
(165607 to 165612), and the registered crossed interval needs them before any of this is quotable
as a three-seed reading. The reading rule keys on point estimates and those are already
unambiguous, but the intervals above are single-seed cluster bootstraps, not the registered
crossed ones.

**Triangulation, which is why this is credible now rather than after the reruns.** Three
instruments with no mechanism in common agree. The placebo puts an arbitrary project split at the
organizational effect's magnitude. The separability probe reads two projects inside one
organization as separable as two organizations, 0.828 and 0.872 within against 0.849 across. The
project contrast has qt-creator's adapter beating qtbase's on qt-creator's own refinements by
+0.079, about four times the organizational effect. Style is learnable and it attaches to
codebases; an organization is a bundle of codebases whose coherence varies.

### The placebo at three seeds: it fires on Qt and not on OpenStack (2026-09-22, jobs 165607 to 165611)

The single-seed reading entered above was reported as firing on both organizations. At the
registered three seeds it does not, and the entry above stands as written rather than being
edited, with this as its correction.

Registered rule throughout: pooled estimand, crossed interval, three seeds. Generated with
`scripts/reading.py --registered --markdown` rather than transcribed.

| run | estimand | rule | arm | estimate | interval |
|---|---|---|---|---|---|
| rq1-placebo-qt-seeds.json | pooled | crossed | qt-a | +0.0305 | [+0.0037, +0.0565] |
| rq1-placebo-qt-seeds.json | pooled | crossed | qt-b | +0.0045 | [-0.0350, +0.0438] |
| rq1-placebo-openstack-seeds.json | pooled | crossed | openstack-a | +0.0099 | [-0.0273, +0.0463] |
| rq1-placebo-openstack-seeds.json | pooled | crossed | openstack-b | +0.0013 | [-0.0375, +0.0402] |
| rq1-qtfull-fp32-seeds.json | pooled | crossed | openstack | +0.0230 | [+0.0000, +0.0457] |
| rq1-qtfull-fp32-seeds.json | pooled | crossed | qt | +0.0316 | [+0.0089, +0.0567] |

**Qt's placebo and Qt's organizational effect are indistinguishable.** An arbitrary half of Qt's
own projects reads +0.0305 [+0.0037, +0.0565] where Qt against OpenStack reads +0.0316 [+0.0089,
+0.0567]. Same point estimate to a thousandth, near-coincident intervals, both excluding zero. The
rule registered before the run says a placebo of the same order as the cross-organization contrast
is evidence that the gate responds to any project boundary. For Qt it does.

**OpenStack's placebo does not reproduce.** +0.0099 covering zero, against an organizational
contrast of +0.0230 whose lower bound is exactly 0.0000. Both arms of the placebo cover zero and
the gate verdict is a fail.

**So the two organizations fail the unit assumption differently**, which fits everything else
measured. Qt's projects are separate codebases under one name, so any split of them behaves like
the real boundary. OpenStack's projects are independent enough that splitting them yields nothing,
and its own organizational contrast only just clears zero to begin with. Neither supports the
organization as the unit; only Qt supports "any project boundary would do".

**How the single-seed claim survived long enough to be reported.** The per-seed pooled contrasts
on openstack-a were +0.0265, -0.0166 and +0.0199, a standard deviation of 0.019 on an effect of
0.01. One seed of that is noise reported as signal, which is what the three-seed rule exists to
prevent and why it is registered.

**Recorded about the reporting rather than the result.** The correction above was itself stated
wrongly twice before this entry, both times by reading a truncated console tail and attributing a
rule to numbers whose position in the output was guessed: the change-averaged median-seed figures
were quoted as though they were the registered ones. The artifacts were on disk throughout.
`scripts/reading.py` exists because of it, printing estimand, rule, arm and run beside every
figure and refusing a filter that matches no cell, since empty output reads as no effect rather
than no such cell.

### Rank 256 at three seeds: capacity is not why the null arm is null (2026-09-22, jobs 164573, 165609, 165612)

The conditional analysis pre-commits to rerunning both adapters at rank 256 when an arm returns no
gain, on the argument that LoRA learns less than full fine-tuning and a null at rank 32 is equally
consistent with insufficient capacity. That branch is now executed, at the registered three seeds.
Read with `scripts/reading.py --registered --markdown`.

| run | estimand | rule | arm | estimate | interval |
|---|---|---|---|---|---|
| rq1-r256-seeds.json | pooled | crossed | openstack | +0.0136 | [-0.0100, +0.0360] |
| rq1-r256-seeds.json | pooled | crossed | qt | +0.0309 | [+0.0068, +0.0567] |
| rq1-qtfull-fp32-seeds.json | pooled | crossed | openstack | +0.0230 | [+0.0000, +0.0457] |
| rq1-qtfull-fp32-seeds.json | pooled | crossed | qt | +0.0316 | [+0.0089, +0.0567] |

Eight times the adapter capacity moves Qt by seven ten-thousandths and moves OpenStack the wrong
way, from a lower bound of exactly zero to one below it. The capacity explanation does not hold,
and the branch closes on evidence rather than on argument.

**The seed spread says the same thing from another angle.** At rank 256, Qt's per-seed pooled
contrasts are +0.0326, +0.0284 and +0.0316, a standard deviation of 0.0018; OpenStack's are
+0.0071, +0.0159 and +0.0177, a standard deviation of 0.0046 on an effect of 0.014. Qt is stable
across seeds at both ranks and OpenStack is not, which is the same asymmetry the placebo found by
a different route.

**Where this leaves the decision tree.** The registered branch fires only when *neither* interval
excludes zero, and the dev reading is mixed, so on the letter of the protocol this rerun was not
required. It was run anyway because the capacity objection applies per arm rather than to the
gate, and a reviewer asking whether OpenStack's null is a capacity artefact deserves a measurement
rather than a reading of the tree. Making the branch per-arm is a protocol amendment that is
legitimate while the test window is sealed and indefensible afterwards; it is recorded here as
open rather than taken.

**Three hardening results now point the same way.** The placebo says an arbitrary project boundary
does what the organizational one does, in Qt. Rank 256 says OpenStack's null is not capacity. The
language histogram says the two organizations barely share a file type and the pair cannot supply
a matched arm. None of them is about the apparatus; all three are about the unit.

### Chromium scoped: five C++ projects clear the rule, and no split of them meets the criteria (2026-09-22)

`datasets/results/host-scoping-chromium.json`, written by `scripts/host_scoping.py` from the raw
counts committed beside it; about 1,050 requests at one a second, nothing written into the corpus.
The selection rule (`host-scoping-chromium/selection-rule.txt`) was written at 21:58 EDT, before any
per-project yield was read: C++ the dominant file type among what sampled human changes touch, and
at least 256 projected examples over the train and dev months (2024-11 to 2025-10, cutoff
2024-11-01). Projected examples are human changes times reviewer-anchored comments per sampled
change times 0.42, which is Qt's 0.219 examples per fetched change over those months (its
examples and fetch records on disk) against its 0.5 comments a change of 2026-09-21. The 0.5
comes from ten changes, so the projections give orders of magnitude and nothing finer.

| project | merged a month | human a month | sampled | reviewer comments a change | C++ share of files | projected train examples |
|---|---|---|---|---|---|---|
| chromium/src | 18,509 | 9,780 | 40 | 1.35 | 0.557 | 55,453 |
| v8/v8 | 818 | 504 | 40 | 1.18 | 0.791 | 2,387 |
| angle/angle | 190 | 140 | 40 | 2.02 | 0.78 | 1,145 |
| chromiumos/platform2 | 247 | 217 | 40 | 0.47 | 0.778 | 467 |
| crashpad/crashpad | 8 | 8 | 4 | 11.0 | 0.933 | 425 |
| openscreen | 4 | 4 | 5 | 0.0 | 0.833 | 0 |

The other eight candidates are not C++ and the rule stops there: devtools-frontend (TypeScript),
catapult and depot_tools (Python), luci-go (Go and TypeScript), crosvm (Rust), and the ChromiumOS
C trees (kernel, ec, depthcharge). A two-week tally of the rest of the host found only small C++
projects (libyuv, libchrome, breakpad, under 30 human changes a month each). chromium/src's row
averages three months (2024-11, 2025-04, 2025-10) counted in sub-month ranges; the others count
all twelve. Human here means an owner that is no service account or roller, which is scoping only:
the pipeline filters no owners, and a roller's change yields nothing once the author filter and the
anchor requirement have run. 47% of chromium/src's merged changes are a roller's.

**The rule admits five projects and the split criteria admit none.** Under `placebo_corpus.py`'s
greedy rule, chromium/src takes a half alone (1 project, 100% of its half's 55,453) and the other
four make 4,424 with v8 at 54%. Without chromium/src, v8 takes a half alone and the other half is
three projects at 2,037, short of OpenStack's smaller half (2,163, from its frozen train split).
No subset of the five passes, and admitting the C trees does not rescue it: the kernel would hold
74% of its half. chromium/src projects twenty-three times the next largest, so every set containing it
fails on share, and every set without it fails on volume. A qualifying Chromium arm needs
chromium/src cut below the project, by directory or component, and that is a design decision
rather than a scoping result.

**chromium/src cannot be fetched by month as the CLI stands.** The host serves at most 10,000
results for one query and then drops `_more_changes` instead of refusing: 2024-11 ended at exactly
10,000, the last updated on the 13th, and a probe past it returns `Cannot go beyond page 100`. A
fetch would have written a month missing its first half and reported success. `fetch_changes` now
asks for anything the query matches at or before the oldest second it received, reading past every
change it was already served at that second, and raises on one it was not (checked live: v8/v8
2025-10 passes, chromium/src 2024-11 raises; the same-second case is tested offline). Collecting chromium/src needs
sub-month queries merged into one snapshot, which waits on the directory decision.

**The collection would be the largest the study has made.** From the same table: the five projects
merge about 237,000 changes over the window, one comments request each, and about 169,000 diff
requests follow from their reviewer comments, about 410,000 requests or nearly five days at one a
second. Without chromium/src it is about 28,000, under eight hours. The design's pacing docstring
treats 70,000 as a full corpus.

**robots.txt, read the same day.** chromium-review serves `User-Agent: * / Disallow: /`, as
android-review does; codereview.qt-project.org serves `Disallow: /` with `Crawl-Delay: 3`, and
review.opendev.org `Crawl-delay: 2`. The pipeline paces all four at one request a second. What this
means for collection is recorded in the re-registration entry of 2026-09-22, not here.

**One matching trap for later analyses.** Chromium writes C++ as `.cc` and Qt as `.cpp`; they share
only `.h`. The separability probe and `--content` restrict by suffix, so a Chromium-Qt C++ cell
would match headers alone unless suffixes are mapped to a language first.

Chromium is added to `GERRIT` and `scripts/fetch_chromium.sh` is the resumable driver. It takes the
project set explicitly and refuses to resume a month fetched under a different set. No collection
has started: there is no qualifying project set, and chromium/src cannot be fetched by month. All 1,050 scoping requests were answered, from a workstation.

### RQ1 re-registered around granularity, and Chromium added as a third organization (2026-09-22)

**Decided by AJ.** Of the two framings the hardening results left open, keep the organization and
report three controls against it, or ask at what boundary adaptation transfers, AJ chose the
second, and added Chromium. This entry records the decision and the design; it adds no figure.

**Why the second framing uses every measurement.** The placebo, rank 256 and the language
histogram are each an embarrassment to an organization-level hypothesis and each a datum for a
granularity one. A single-level test cannot tell a project effect from an organization effect,
because an organization's projects are where its adapter's training data comes from. So the
contrast is decomposed: each organization is split in half by the placebo's own rule, and on a
refinement from one half three adapters are scored on the identical example, its own half's, its
sibling half's, and a foreign organization's halves. Own minus sibling is H1; sibling minus
foreign is H2. Neither adapter in H2 has seen the evaluated projects.

**Why Chromium.** H2 is only an organizational test when the two organizations write the same
language, and OpenStack and Qt do not. Qt and Chromium do, so H2 is confirmatory on that pair
alone, with a supplementary estimand on C++ hunks because "both C++" is measured, not assumed. Human-subjects
review is not specific to Chromium: it was deferred for every host on 2026-09-14 and is still due
before submission. **A terms-of-use question found the same day is host-specific and stops
collection.** robots.txt reads `Disallow: /` for all agents on chromium-review, android-review and
codereview.qt-project.org (fetched 2026-09-23); review.opendev.org allows access with
`Crawl-delay: 2`. Google's Terms of Service (effective 2026-07-30) prohibit "using automated means
to access content from any of our services in violation of the machine-readable instructions on
our web pages (for example, robots.txt files that disallow crawling, training, or other
activities)", which reaches the AOSP corpus already collected as well as a Chromium one. No host is
fetched again until AJ decides; nothing collected is deleted. The pipeline's pacing, one request a
second to every host, is also faster than the two and three seconds OpenDev and Qt ask for.

**An independent adversarial review of the first draft returned eleven findings, four blocking,
and all were taken.** The ones that changed the design:

- The pilot predicts the decomposition is underpowered at half-organization size, and a
  non-significant cell cannot carry "an organization adds nothing". So a smallest effect of
  interest is registered (0.01 exact match, the seed-effect bound, fixed before any contrast), and
  a cell is supported, absent (inside the band, two one-sided tests) or inconclusive. Only absence
  supports a negative reading.
- Half-organization adapters train near the 1,800-example setting where the seed effect measured
  0.013, above the 0.01 at which three seeds stop holding nominal, so the registered seed rule
  itself calls for five seeds unless the effect at the half size is bounded below 0.01.
- The pooled estimand would let a difference in how well each half's data teaches survive the
  symmetric design; the halves are now weighted equally and resampled within themselves.
- Chromium could collapse the split: if `chromium/src` holds most of its examples, one half is one
  project. The split now has to qualify (three projects a half, no project over half its half,
  each half at least OpenStack's smaller half), by 2026-10-23, or H2 has no confirmatory cell and
  is reported as exploratory.
- Bonferroni was replaced by Holm, which dominates it, and the family-wise level stated in the
  draft was wrong: two tests at one-sided 0.0125 hold 0.025, not 0.05.
- The two hypotheses share the sibling adapter with opposite signs, so they are bootstrapped on
  the same draws and the share of draws in each reading is reported.

**The rank branch is amended in the same change.** It fired only when neither organization
excluded zero, so a per-arm capacity artefact was never checked, and "if rank 256 also produces no
gain" left open a second look that could turn a fail into a pass. Rank 256 now runs on the whole
grid, is a supplementary analysis in ICH E9(R1)'s sense (a different estimand, "given lower
priority"), never changes a verdict, and a negative reading needs absence at both ranks.

**Recorded rather than taken:** `crossed_bootstrap` draws one seed index for every adapter in a
contrast although the adapters are independent training runs. That was already true of the
registered gate; it is to be measured against the coverage simulation before Stage 1.

Design of record: `docs/superpowers/specs/2026-09-22-granularity-redesign.md`. The gate as code:
`sphragis/experiment/decomposition.py`, beside the unchanged `walk.gate`.

### The seed effect at the half size is above 0.01, so the decomposition runs at five seeds (2026-09-23)

The registered seed rule says three seeds up to a seed main effect of 0.01 and five above it. It
was measured at 0.000 on the full organizations and 0.013 at about 1,800 examples; the
re-registration trains on halves, so the rule has to be read at the half size. The three-seed
placebo runs already are that measurement: OpenStack's halves trained on 2,157 and 2,159 examples,
at the size every half-adapter will train on, and Qt's on 4,205 and 4,206. Read with
`scripts/seed_effect.py` over each placebo's three single-seed runs.

| placebo | sigma_b | one-sided 95% upper bound | artifact |
|---|---|---|---|
| OpenStack halves | 0.0106 | 0.086 | `seed-effect-placebo-openstack.json` |
| Qt halves | 0.0148 | 0.084 | `seed-effect-placebo-qt.json` |

Both point estimates are above 0.01, so **five seeds**, by the rule fixed before either run. The
upper bounds are wide because two contrasts of three seeds each give two degrees of freedom, which
is itself the reason not to rest a three-seed decision on them. This is the case the spec named:
three seeds only if the effect at the half size is bounded below 0.01, and it is not.

### Sibling-half leakage is below the registered threshold on the dev window (2026-09-23)

H2 would credit the organization with shared boilerplate if a sibling half's training data
already held near-copies of the evaluated half's refinements. `scripts/sibling_leakage.py`
assigns halves exactly as the placebo does and measures, for each half's dev window, the share of
examples whose closest training example reaches a Jaccard threshold: from its own half's train
window, its sibling's, and the foreign organization's. Generated from
`datasets/results/sibling-leakage.json`.

| evaluated half | dev examples | own J>=0.7 | sibling J>=0.7 | foreign J>=0.7 | own J>=0.5 | sibling J>=0.5 | foreign J>=0.5 |
|---|---|---|---|---|---|---|---|
| openstack-a | 300 | 0.0033 | 0.0000 | 0.0033 | 0.0133 | 0.0000 | 0.0033 |
| openstack-b | 251 | 0.0159 | 0.0080 | 0.0000 | 0.0239 | 0.0159 | 0.0000 |
| qt-a | 568 | 0.0018 | 0.0018 | 0.0000 | 0.0070 | 0.0035 | 0.0000 |
| qt-b | 372 | 0.0081 | 0.0000 | 0.0000 | 0.0296 | 0.0054 | 0.0000 |

Every sibling rate is at or below the same half's own rate, and all are below the registered 2%
at Jaccard 0.7, so shared boilerplate is not what a sibling adapter would be credited with on the
dev window. The check is registered for the test window and runs again there.

### The gate's stratified interval holds nominal at both Holm levels, in H2's regime (2026-09-23)

`scripts/crossed_coverage.py --strata 2` simulates the interval the decomposition gate reads: two
equally weighted strata, changes resampled within each, five seeds, 4,000 trials a cell (Monte
Carlo error about 0.002 at the 0.0125 level). One-sided false-positive rate, the lower bound above
zero under a true null, beside the unstratified crossed interval on the same runs. Generated from
`stratified-coverage-0.975.json` and `stratified-coverage-0.95.json`.

| level | nominal | sigma_b | stratified | crossed |
|---|---|---|---|---|
| 0.975 | 0.0125 | 0.0 | 0.0105 | 0.0100 |
| 0.975 | 0.0125 | 0.005 | 0.0112 | 0.0110 |
| 0.975 | 0.0125 | 0.01 | 0.0145 | 0.0150 |
| 0.975 | 0.0125 | 0.02 | 0.0132 | 0.0130 |
| 0.95 | 0.025 | 0.0 | 0.0222 | 0.0208 |
| 0.95 | 0.025 | 0.005 | 0.0230 | 0.0222 |
| 0.95 | 0.025 | 0.01 | 0.0238 | 0.0230 |
| 0.95 | 0.025 | 0.02 | 0.0260 | 0.0257 |

Within Monte Carlo error of nominal at both levels up to a seed effect of 0.02. **These runs are
H2's regime** (equal halves, the same treatment arm in both), as the re-review pointed out. H1's
second half swaps treatment and control, so an adapter-level seed shift enters the two halves
with opposite signs; that regime (`--flip-second`) is measured next, before the report states a
coverage for H1.

### At the test window's size each H1 cell detects about three exact-match points, and absence is out of reach (2026-09-23)

`scripts/decomposition_sensitivity.py` simulates one H1 cell as the gate reads it: the placebo's
own-against-sibling contrast on each half as the variance model, the test window's projected
changes split between the halves in their dev-window proportion, five seeds at each
organization's half-size seed effect, and the stratified crossed interval. Marginal power
0.928 per cell, so three independent cells pass together about four
times in five. Generated from `decomposition-sensitivity.json` (100 trials a step,
1000 resamples).

| cell | planned changes (halves) | sigma_b | level | detectable effect | null reads absent |
|---|---|---|---|---|---|
| openstack | 814 + 995 | 0.0106 | 0.975 | +0.0273 | 0.000 |
| openstack | 814 + 995 | 0.0106 | 0.95 | +0.0257 | 0.000 |
| qt | 1784 + 1497 | 0.0148 | 0.975 | +0.0283 | 0.000 |
| qt | 1784 + 1497 | 0.0148 | 0.95 | +0.0235 | 0.000 |

**The detectable effect is two to three times the smallest effect of interest, and no null study
in the simulation read absent**: the interval at the test window's size is wider than the
(-0.01, +0.01) band, so a cell can pass or be inconclusive and cannot be absent. The spec named
this case and committed to stating it before the test rather than discovering it after. Every
pre-committed negative reading ("an organization adds nothing", "no transferable style") rests on
absence, so as registered those readings could never be reached. What replaces them is the next
entry. One caveat on the variance model: Qt's placebo halves trained on about 4,200 examples each,
twice the half size every adapter will train at, so Qt's row describes a better-trained adapter's
variance than the design will have.

**H1's own regime holds nominal.** The coverage runs with the second half's arms swapped
(`--flip-second`), as H1 swaps them, so a seed shift enters the halves with opposite signs.
Generated from `stratified-coverage-h1-0.975.json` and `stratified-coverage-h1-0.95.json`.

| level | nominal | sigma_b | stratified |
|---|---|---|---|
| 0.975 | 0.0125 | 0.0 | 0.0155 |
| 0.975 | 0.0125 | 0.005 | 0.0100 |
| 0.975 | 0.0125 | 0.01 | 0.0120 |
| 0.975 | 0.0125 | 0.02 | 0.0107 |
| 0.95 | 0.025 | 0.0 | 0.0257 |
| 0.95 | 0.025 | 0.005 | 0.0245 |
| 0.95 | 0.025 | 0.01 | 0.0205 |
| 0.95 | 0.025 | 0.02 | 0.0213 |

Within Monte Carlo error of nominal at both levels, in both regimes now measured.

### Pooling does not make absence reachable either (2026-09-23)

The registered pooled estimate is sharper than any one cell, so it was the natural home for the
negative readings. Simulated the same way, H1 pooled over openstack, qt with
every half one equally weighted stratum (planned changes [814, 995, 1784, 1497]): a true
null reads absent 0.040 of the time at 97.5% and
0.145 at 95% (`decomposition-sensitivity-pooled.json`). Chromium
would add a third organization and narrow the interval by roughly a further fifth, which does not
change the picture. At this test window, 0.01 is not a resolvable equivalence bound.

**Proposed, not yet registered:** anchor the negative readings to the design's own resolution.
A cell is *bounded* when its interval's upper bound lies below the effect the sensitivity
analysis says that cell detects, a number fixed now, before any test data (Lakens, Scheel and
Isager 2018 list "the effect the study was designed to detect" among the justifications for an
equivalence bound). The substantive SESOI stays reported beside it. A null then reads "no effect as
large as this design was built to detect", which is what the design can actually support.

### A data audit: nearly half of Qt's organizational effect was its lint bot (2026-09-23)

Stage 1's labels were audited before anything else is built on them, in three parts.

**Are the labels real?** A stratified sample of 120 examples (15 per organization, window and placebo
half, one per change, `scripts/label_audit_sample.py`, seed 20260923) was labelled against a
five-class rubric drawn from the refinement-noise literature (Too Noisy To Learn, MSR 2025;
"Rethinking Training Data for Generating Code Review Comments", arXiv 2607.25851): valid, comment
not actionable, rewrite unrelated to the comment, partial, or context-dependent (the right rewrite
needs information the prompt does not carry). **Model-assisted labels, disclosed as such**; a blind
subset goes to AJ for a human agreement figure before any of this is quoted. 92 of 120 valid, 77%
(Wilson 95% [68, 83]); OpenStack 42 of 60 and Qt 50 of 60. The dominant non-valid class is
context-dependent (16), then partial (7), unrelated (4), not actionable (1). CodeReviewer-derived
data measured about 64% valid under a comparable rubric, so the pipeline's existing filters earn
their keep. Because every adapter is scored on the identical examples, noise in held-out labels can
dilute a contrast and cannot manufacture one; noise that differs between training halves is what
the equally weighted two-half design cancels to first order.

**Does the pipeline treat the organizations alike?** Two filters do not.

- *Automated reviewers.* Qt's Sanity Bot posts templated inline comments ("Hint: Trailing
  whitespace", "Hint: Leading tabs", "Hint: WS-only change", "Hint: Flow control keywords must be
  followed by a single space", about 900 of them), which the build keeps as reviewer comments.
  8.1% of Qt's built examples rest on automated comments alone, and none of OpenStack's. A bot
  enforces written rules, the opposite of what the study measures, and it runs on one organization.
- *Rebases.* The label is the hunk that changed from patch set n to n+1, and the build never reads
  the diff's `due_to_rebase` flag. Where the successor revision's `kind` is a rebase or a message
  edit with no code change, the label can only be what the rebase swept in: 0.34% of OpenStack's
  kept examples and 1.65% of Qt's. How many reworked successors also carry rebase edits the REST
  snapshots cannot say; the git route can, and measures it.
- The acknowledgement list misses Gerrit's one-click "Acknowledged" (13 and 6 examples rest on it
  alone).

**What the bot did to the pilot.** Every registered dev-window reading re-read with the examples
whose comments are all automated removed (`scripts/bot_sensitivity.py`, fixed prefix list, same
rows, same crossed interval and pooled estimand):

| run | cell | registered | automated-only removed | share removed |
|---|---|---|---|---|
| organization, rank 32 | openstack | +0.0230 [+0.0000, +0.0458] | +0.0230 [+0.0000, +0.0458] | 0.000 |
| organization, rank 32 | qt | +0.0316 [+0.0089, +0.0562] | +0.0173 [-0.0022, +0.0373] | 0.049 |
| organization, rank 256 | openstack | +0.0136 [-0.0097, +0.0361] | +0.0136 [-0.0097, +0.0361] | 0.000 |
| organization, rank 256 | qt | +0.0309 [+0.0067, +0.0565] | +0.0170 [-0.0044, +0.0384] | 0.049 |
| Qt split in half | qt-a | +0.0305 [+0.0046, +0.0573] | +0.0288 [+0.0019, +0.0555] | 0.044 |
| Qt split in half | qt-b | +0.0045 [-0.0349, +0.0432] | +0.0038 [-0.0363, +0.0429] | 0.056 |
| OpenStack split in half | openstack-a | +0.0099 [-0.0287, +0.0467] | +0.0099 [-0.0287, +0.0467] | 0.000 |
| OpenStack split in half | openstack-b | +0.0013 [-0.0364, +0.0397] | +0.0013 [-0.0364, +0.0397] | 0.000 |

Verdicts: organization, rank 32: mixed → fail; organization, rank 256: mixed → fail; Qt split in half: mixed → mixed; OpenStack split in half: fail → fail.

**Qt's organizational contrast falls from +0.0316 to +0.0173 and no longer excludes zero, at both
ranks, from removing 4.9% of its held-out examples.** An adapter trained on Qt had learned Qt's bot,
which an OpenStack adapter never saw. The placebo barely moves (+0.0305 to +0.0288), because the bot
runs across all of Qt and both halves learned it. So once the artifact is out, **the within-Qt
half-split effect is larger than Qt's cross-organization effect**, which is the granularity
hypothesis read directly off the data. OpenStack has no bot and does not move.

**Consequences, all taken before any test data exists:** automated-reviewer comments are excluded
from the corpus by a registered rule (account type where the host exposes it, the prefix list
otherwise, both reported), successors that are not reworks are dropped, "Acknowledged" joins the
acknowledgement list, and every pilot figure quoted in the Stage 1 report is re-read on the cleaned
corpus. The registered dev-window verdict under the organization gate was already superseded; this
says the part of it that looked strongest was partly an instrument artefact.

### Corpus v2: the audit's rules, applied at the source and to what was already built (2026-09-23)

The fixes are in the builder, so every corpus collected from here on is clean at collection, and in
a new `refine` stage that brings corpora built before them to the same rules from their own
examples and raw snapshots, with nothing refetched.

**Bots are recognised from their source, not from a guessed list.** Identity first: Gerrit tags
service accounts `SERVICE_USER`, and the scrub used to replace every account object with its
pseudonym alone, discarding that tag at ingestion; it now keeps it. Where a host does not expose
the tag, and for corpora collected before, the fallback is each bot's own message templates:
`sphragis/corpus/automated/qt-sanity-bot.json` holds all 101 complaints
`git-hooks/sanitize-commit` posts, extracted from qt/qtrepotools at `309df8d5eefb` by
`scripts/extract_bot_templates.py` (spelling complaints pinned to the exact shape the code writes,
so a reviewer's "a -> b?" is not taken for the bot); Qt's QUIP-23 integration posts one fixed
message on files carrying a `Qt-Security` header; and flake8 output is posted inline on
pyside/pyside-setup. The earlier prefix scan had missed the last two and most of the spelling
complaints. Recognised in Qt's built examples: Qt Sanity Bot 1,347, flake8 lint output 338, Qt QUIP-23 security review integration 54; in OpenStack's, none. Templates match whole
comments. Every recognised string was inspected, and a repeated-text queue of the rest, which a new
bot would surface in, holds only reviewer language.

| organization | built | bot comments removed | examples resting only on bots | rebase-only successor | acknowledgement only | refined |
|---|---|---|---|---|---|---|
| openstack | 5,959 | 0 | 0 | 20 | 13 | 5,926 |
| qt | 11,448 | 1,672 | 1,240 | 189 | 6 | 10,013 |

Refrozen as corpus v2 (the v1 manifests stay in git history, and every earlier result cites them):

| organization | pilot | train | dev |
|---|---|---|---|
| openstack | 600 | 4,304 | 565 |
| qt | 1,134 | 7,456 | 897 |

**Re-read under the registry** rather than the prefix list: Qt's organizational contrast is
+0.0182 [-0.0022, +0.0388] with
0.055 of its held-out examples removed, the same reading as before.

**What else changed with it.** Everything that trains or measures now reads refined examples
through one loader (`sphragis/corpus/load.py`) that refuses a refinement made from other examples
or under other rules; nine scripts had read the built files directly, and a fix that reached only
the CLI would have left them on the old data. A placebo's halves are derived corpora carrying the
rules version they were cut under. The rules version is a digest of the registries and the
acknowledgement list, so it cannot fall behind them. `make data-audit` re-runs this audit on any
corpus and writes `data-audit-<org>.json`. The scrub treats a chained address as one.

**Not yet done:** the pilot adapters were trained on v1, including the bot examples; retraining on
v2 waits on Qt's answer about access, since training is where the Qt data is used again. RQ2's
project corpora are rebuilt from v2 by `project_corpora.py` before its next run.

**Leakage re-measured on v2** (`sibling-leakage.json`, `window-report-*.json` regenerated). OpenStack
train into dev at Jaccard 0.7 is 0.0106, unchanged. Sibling-half rates at Jaccard 0.7:

| evaluated half | own | sibling | foreign |
|---|---|---|---|
| openstack-a | 0.0030 | 0.0060 | qt 0.0000 |
| openstack-b | 0.0137 | 0.0046 | qt 0.0000 |
| qt-a | 0.0055 | 0.0018 | openstack 0.0000 |
| qt-b | 0.0030 | 0.0000 | openstack 0.0000 |

All below 2%. openstack-a's sibling rate exceeds its own half's, so the threshold, not an
ordering between the own half and the sibling, is the property to rely on.

### Correction: every successor is a rework, and corpus v2 is refrozen under the corrected rules (2026-09-23)

**Retracted: the rebase-only-successor finding** in the two entries above (0.34% of OpenStack's
kept examples and 1.65% of Qt's in the audit, 20 and 189 in the v2 table). It was an artifact of
the audit's own lookup, which keyed a change's revisions on its Change-Id. A cherry-pick carries
the Change-Id to another branch, so a stable-branch copy that was only rebased lent its kind to
the original. Resolved per change (by number, else by Change-Id, project and creation time
together), every OpenStack successor and 11,444 of Qt's 11,448 are reworks; the other 4 are
changes that cannot be told apart and are kept and counted. The rule stays as a guard, and now
removes nothing. A review of the v2 branch caught it. Rebase edits carried inside a
reworked successor remain the open question the audit entry names.

**Two more corrections from that review.** A comment is automated only when every paragraph of it
matches a template, since the Sanity Bot joins its complaints for one line with a blank line;
and the Sanity Bot's templates are the union over every version of its hook in effect across the
corpus span, not the current version alone.

| organization | built | bot comments removed | examples resting only on bots | acknowledgement only | refined |
|---|---|---|---|---|---|
| openstack | 5,959 | 0 | 0 | 13 | 5,946 |
| qt | 11,448 | 1,747 | 1,304 | 6 | 10,138 |

Recognised in Qt: Qt Sanity Bot 1,355, flake8 lint output 338, Qt QUIP-23 security review
integration 54 (`data-audit-qt.json`).

Refrozen as corpus v2 under these rules; the manifests of the entry above are replaced, and each
manifest now records the build and label rule digests, which `verify` checks:

| organization | pilot | train | dev |
|---|---|---|---|
| openstack | 600 | 4,322 | 565 |
| qt | 1,146 | 7,563 | 897 |

**Re-read.** Qt's organizational contrast without automated examples is +0.0171
[-0.0034, +0.0374] (5.7% of its held-out examples removed), against +0.0316 [+0.0089, +0.0562]
as registered; at rank 256, +0.0164 [-0.0051, +0.0384]. Qt's firing placebo half, qt-a, is
+0.0288 [+0.0019, +0.0555] without them. The finding of the audit entry stands: nearly half of
Qt's organizational effect was its lint bot, and the half-split survives
(`bot-sensitivity-*.json`). OpenStack train into dev at Jaccard 0.7 is 1.06%, unchanged
(`window-report-openstack.json`).

**Leakage re-measured** (`sibling-leakage.json`), Jaccard 0.7:

| evaluated half | own | sibling | foreign |
|---|---|---|---|
| openstack-a | 0.0031 | 0.0031 | qt 0.0000 |
| openstack-b | 0.0132 | 0.0088 | qt 0.0000 |
| qt-a | 0.0020 | 0.0020 | openstack 0.0000 |
| qt-b | 0.0079 | 0.0000 | openstack 0.0000 |

All below 2%. The v2 entry's remark that openstack-a's sibling rate exceeded its
own was read from the refinement that dropped the 20 examples in error, and does not survive:
the two are equal. The threshold remains the property to rely on, not the ordering.

**AOSP audited the same way** before RQ2 trains on it (`data-audit-aosp.json`): 5,133 built,
no comment matching a registered bot, every successor a rework, and a repeated-text queue holding
only reviewer language ("typo" on 25 changes, "2024" on 24). 5,130 refined.

**The build and the label rules are now separate stages with separate digests.** Review of the
fixes found that the build applied the bot templates and the successor rule that `refine` applies
too, and that a change to the build's own rules moved one rules version while `refine` re-ran
only the label rules: a refreeze would have certified the old build's output, a new bot template
would have meant refetching every organization, and `refine`, which only removes, could not undo a
build that had removed too much. The build now keeps only what needs the network or the comment's
author (the author, service-account and acknowledgement filters, well-posedness, the scrub) and
records the build rules each month was built under; every label rule is `refine`'s, and a change
to one is a re-refine from disk. The loader refuses a month built under other build rules, and
each manifest records both digests for `verify`.

The months on disk were built before the build recorded anything, so they were accepted once
without a rebuild (`python -m sphragis.corpus stamp`, limited to the 54 snapshots listed in
`sphragis/corpus/stamped-months.json` and to the build rules named there), on this evidence:
every month's drop profile carries exactly the drop reasons of the build that added the
well-posedness filter, and that filter and the fetchers are unchanged since; that build applied
neither the bot templates nor the successor rule, so `refine` applies both to what it kept, and
its acknowledgement list lacks only "Acknowledged", which `refine` adds; the build's other later
additions are the service-account filter, which the raw snapshots give nothing to act on (they hold no account
tags), and the change number, which names a change and removes none; and the scrub's one change,
chained addresses, is reapplied by `refine` as a sweep of domains left behind a pseudonym (one
AOSP comment). One month differs: Qt's 2024-10 was built before prompt context was added, and its
582 examples carry none. They are the same examples with the same text; context is in no prompt;
the month is the pilot window, which no contrast trains or evaluates on; the contamination
battery refuses a row without context and the label-audit sample draws from train and dev. The
stamp records the count in that month's build record.

**Files handed to a job by path now fail closed.** Project, client, contamination and planted
corpora are cut with a record of the rules, their hash and the sources they came from beside each
file, and every job that reads one (the client runs, the project and calibration contrasts, the
contamination battery, the pilot replay and the prompt probe) refuses a file without a matching
record, or one whose source has since moved or gone stale; `--legacy-corpus` (`LEGACY_CORPUS=1`
in the sbatch scripts) reads an older one to reproduce an earlier result, and the output records
it. Every corpus root's files are now ignored by an
allowlist, manifests and seals excepted, so a later stage's text cannot be committed by omission.

**Open.** The committed contamination results carry the scored half of each example's rewrite as
`reference` text, so code from the corpus is in git; it predates these rules and stays until the
battery can be re-read from example ids and hashes, which it must before the repository is public.

### Label audit v2: two blind model raters agree at kappa 0.56, and validity does not differ between the units a contrast compares (2026-09-23)

The first audit's context-dependent class folded two questions together: whether the request can
be understood from what the example shows, and whether the rewrite uses names the example does not
show. A clear request answered with a project's own constant is a valid label that no prompt alone
reproduces, which is house-style knowledge an adapter can learn, not label noise. Rubric version 2
asks the two separately (`scripts/label_audit/rubric.md`).

A fresh sample of 384 refined v2 examples, 48 per organization, window and placebo half, one per
change (`label_audit_sample.py`, seed 20260923), was blinded under neutral ids in a seeded order
with the organization, project, window and half removed (`label_audit_blind.py`). Two model raters
labelled every item alone, blind to each other: rater A on Claude Opus 5.5, rater B on Claude
Sonnet 5 (`scripts/label_audit/rater-prompt.md`). The rubric's worked example is item-001, which is
excluded from every figure. A first run of rater B split its batches across copies of itself and
was lost before it was committed; it is not reported, and the run below was one rater under an
added rule against delegation.

Over 383 items (`label-audit-v2.json`): the label agrees on 86.4% of items, Cohen's kappa 0.557
[0.454, 0.652]; the outside-names question on 94.8%, kappa 0.825 [0.745, 0.894]. The main
disagreement is one boundary: 19 items rater A read as partial and rater B as valid, against 5 the
other way. For comparison, two human raters reached 0.758 on 383 comment-generation pairs
(arXiv 2607.25851) and 0.56 independently on a nine-label taxonomy (arXiv 2604.23667).

Validity does not differ between the units a contrast compares. Share valid, 95% Wilson interval:

| rater | openstack | qt | openstack-a | openstack-b | qt-a | qt-b |
|---|---|---|---|---|---|---|
| A | 0.827 [0.767, 0.874] | 0.797 [0.734, 0.848] | 0.802 | 0.853 | 0.802 | 0.792 |
| B | 0.848 [0.790, 0.892] | 0.839 [0.780, 0.884] | 0.854 | 0.842 | 0.875 | 0.802 |

Every interval overlaps every other, so label noise is no route by which an organization or a half
would read as having a style it does not have. A human's blind check of rater A is in progress;
the human-to-model kappa is what decides how far these model labels can be relied on.

### The audit's kappa is the prevalence paradox: AC1 0.853, and the raters part mostly on whether an item is valid (2026-09-23)

Rater A calls 311 of 383 items valid and rater B 323, so both raters' marginals sit on one label
and chance agreement under kappa is high. That depresses kappa at high raw agreement (Feinstein and
Cicchetti 1990), which is why kappa is reported beside raw agreement and a paradox-resistant
coefficient rather than alone (James, LREC 2026, arXiv 2603.06865). Gwet's AC1 takes chance
agreement over the whole five-label scale (Gwet 2008). Same items, same bootstrap seed, so the
kappa intervals reproduce byte for byte (`label-audit-v2.json`, regenerated):

| question | raw | kappa | AC1 |
|---|---|---|---|
| label | 0.864 | 0.557 [0.454, 0.652] | 0.853 [0.813, 0.890] |
| outside names | 0.948 | 0.825 [0.745, 0.894] | 0.926 [0.890, 0.956] |

Specific agreement per label, 2 n_kk / (n_k by A + n_k by B), says where the raters part:

| label | A | B | specific agreement |
|---|---|---|---|
| valid | 311 | 323 | 0.937 |
| non_actionable | 6 | 9 | 0.800 |
| unrelated_rewrite | 10 | 11 | 0.476 |
| partial | 42 | 20 | 0.484 |
| context_dependent | 14 | 20 | 0.471 |

Most disagreements are valid against some other label, the partial-against-valid boundary
above most of all, and the non-valid labels rarely trade among themselves. Collapsed to the
question the corpus depends on, valid or not, the raters reach kappa 0.635 [0.525, 0.730] and AC1
0.854 [0.804, 0.897] (`valid_vs_rest` in the artifact). The human check is read with all three
coefficients, the collapse and specific agreement, and the Stage 1 report quotes AC1 beside kappa.
Model raters with a human check are a design MSR has seen: Ahmed et al. (MSR 2025,
arXiv 2408.05534) found model-model agreement predicts human-model agreement and used it to
decide whether a task suits model raters; their further step, choosing items by model
confidence, is not used here.

### Both corpus v2 manifests reproduce from the refined examples (2026-09-23)

The build and refine rule digests make a month built or refined under other code unreadable, but
dedup and split sit under neither, so a change to either would leave the frozen windows intact and
`verify` clean: the failure that let Qt verify at 10,695 while the pipeline yielded 10,692.
`verify --reproduce` reruns dedup and split in memory from the refined examples and compares every
window byte for byte with its frozen split file, over every window either side names, and the
dedup counts with the manifest. Comparing ids alone would pass a dedup that kept a different copy
of an id, and comparing only the rerun's windows would pass one dropped from the bounds (both
found in review). On the corpus v2 data, OpenStack and Qt both reproduce byte for byte. The check takes minutes on Qt, so it is a flag rather than the default.

### A rebased test reached chromium-review; the suite is now offline and REST refuses unpermitted hosts (2026-09-23)

**What happened.** #35 gave Chromium a REST host. #39's test that an organization without one is
not fetched over REST (`fetch --org chromium`) therefore stopped being refused once #39 was rebased
onto #35, and ran a real fetch. Two local `make verify` runs in #39's worktree each sent at least
one request for `status:merged after:2025-10-01 before:2025-11-01` to
`chromium-review.googlesource.com/changes/`. How many attempts each made, and what came back, was
not recorded and cannot be reconstructed: the retry budget bounds one URL's attempts, not a
run's, and the second run held a connection for about eleven minutes before it was killed. No snapshot from the test
survives: pytest keeps its recent runs' temporary directories, and none holds one (checked). Writing
the guard's own test first then opened one TCP connection to a Google address (no request sent)
and made one DNS lookup. The rebased branch was never pushed, so CI sent nothing. This is a breach
of the collection stop of 2026-09-22, caused by an unfaked network path in a test.

Review of the fix found two more departures from the hosts' terms. `scripts/resume_when_allowed.sh
qt` probes codereview.qt-project.org with curl outside the transport; it was not running (checked:
no process, crontab or tmux session). And the REST transport paced review.opendev.org at the CLI's
1 s default, below the Crawl-delay of 2 its robots.txt asks for; the earlier builds this log
records ran at about 20 and then 5 requests a second (2026-09-15), so the OpenStack corpus was
collected well above the requested rate throughout.

**What changed.** The test suite refuses connections, datagrams and messages to any address but
loopback, and forward and reverse name lookups of remote hosts, from collection onward
(`tests/conftest.py`). It raises an error the transport cannot mistake for a retryable 503, fails
the test that tried even when its code catches the refusal, and fails the run when a refusal is
caught outside any test. Subprocesses and raw `_socket` calls are outside that guard; the suite
passes under `unshare -n`. The REST transport refuses any host not in
`REST_PERMITTED`, before a connection opens, and names the reason each permitted host may be
called: only review.opendev.org, whose robots.txt allows `/changes/`. android-review,
chromium-review and codereview.qt-project.org are refused, which enforces in code the stop that was
until now a decision in this log. `fetch`, `build` and the Chromium scoping script all go through
that transport; the resume script refuses the same three organizations before its first request.
Each permitted host records its crawl delay, and the transport paces by host name, retries
included, never faster than it.

### FDLoRA's implementation, corrected before it ran: what job 183579 would have measured wrong (2026-09-23)

An independent review of PR #36 found the round-0 average (Algorithm 1, line 7) was computed
per source rather than over every client the plan builds, before job 183579 reached its queue
slot. The paper's N is every client the server sees; this repository's `sources` name separate
simulated federations for the attack scripts, and the two are not the same population. Fixed by
flattening `plan` into one list before Stage 1 runs and averaging over all of it, so `theta_s0` is
one value shared across every client regardless of source. Job 183579 was cancelled as invalid
before it produced a result; nothing from it is quoted anywhere.

**Registered before the next job: when the withheld half is read.** `-local` is saved as of a
client's most recent sync, or its Stage 1 value if `--sync-period` never fired. When the final
round is itself a sync round, that value is identical to the transmitted global by construction --
correct and intended only at the deliberate synchronous `--sync-period 1` endpoint, where every
round syncs and the collision is the point. The default job's first choice, `ROUNDS=6
SYNC_PERIOD=3`, hit the same construction by coincidence (6 is a multiple of 3), which would have
made its withheld/transmitted comparison vacuous without saying so. `scripts/fdlora_schedule.py`
now refuses any `rounds`/`sync_period` pair where the last round syncs unless `--allow-final-sync`
states the collision is intended, and the default `SYNC_PERIOD` moves to 5 -- the paper's own
tested value that does not divide 6, so the withheld half is read one round after its last sync
rather than the round it was just overwritten in.

**Recorded as a limitation rather than resolved by a claim the code does not support.** The
docstring previously called training each client's global module independently across rounds,
rather than simulating the paper's real per-round outer aggregation (Nesterov momentum over every
client's change, Algorithm 1 lines 17-18), "the conservative direction... it can only overstate
what a real, further-averaged deployment would leak." That claim does not hold: in the real
algorithm the server can subtract its previous global from what it receives and recover each
client's K-epoch change, T times over, so the paper's own per-client contribution is bounded by
roughly `inner_steps` epochs from a shared seed. This script's saved global module instead carries
roughly `rounds * inner_steps` continuous epochs, uninterrupted by any outer step. A difference in
measured leakage against FedDPA may therefore reflect this length gap rather than the schedule,
and the comparison is not compute-matched. The docstring now calls it what it is -- an upper bound
confounded by training length -- and drops "conservative": overstating leakage is not a safe
direction for a measurement that is looking for leakage, it is a bias toward the hypothesis.
Implementing the real outer step (rounds outer, clients inner, one Nesterov update a round over
every client's change, each round's upload saved) is the fix that removes the confound; it is not
done here.

**Also fixed, found by the same review:** the adapter-shape naming guard in
`sphragis/experiment/cluster-env.sh` classified a selector by testing it as a glob pattern against
a synthetic placeholder name (`a-c0`, `a-c0-local`, `a-c0-p0`), which a selector spelled without
the assumed hyphen (`*local`, `*-local*`) or scoped to an organization prefix (`aosp-*`) could
defeat: `*local` matched none of the placeholders' suffix-based case arms, so it fell through to
the unsuffixed transmitted name while actually selecting only the withheld adapters -- a silent
mislabeling where `main`'s original two-shape version had refused the same pattern outright. The
guard now globs the real client directories the selector resolves to and classifies each match by
its own suffix, so the result depends on what is actually on disk rather than on the selector's
spelling.
### The review UIs are closed to crawlers and the git hosts are not: a NoteDb route, checked against the REST corpus (2026-09-23, rerun 2026-09-25)

`# research(2026-09)`. `scripts/notedb_parity.py` fetches every change NoteDb records as submitted
in 2024-11 and 2025-01 on AOSP's `platform/hardware/interfaces`, over git and on every branch,
builds them with the same `build_from_change` as the REST corpus, and compares the two into
`datasets/results/notedb-parity-aosp.json`. The artifact was regenerated on 2026-09-25 from the
branch as reviewed: the enumeration, field, example, rebase and timing figures below are from that
run, and figures from earlier runs are dated where they appear. The first run's (2026-09-23, `main`
only) are superseded.

**Why a second route.** The robots.txt of android-review, chromium-review and
codereview.qt-project.org is `Disallow: /` as read on 2026-09-23, and Google's terms forbid
automated access that violates robots.txt, so REST collection from those three hosts stopped. The
git hosts behind the first two, android.googlesource.com and chromium.googlesource.com, disallow
only some gitiles web views (`+log`, `+blame`, `+archive`, `?format=JSON` and `TEXT`); the git fetch
paths are allowed. Gerrit 3.x keeps the review record in the repository as NoteDb:
`refs/changes/NN/<n>/meta` is a commit chain whose footers carry status, patch sets and votes, and
whose tip tree is a notes map of per-patch-set JSON holding the inline comments with their ranges,
authors and patch sets. `GIT_PERMITTED` lists the git hosts the route may contact: AOSP's is
permitted; Chromium's is not until its maintainers answer.

**Probes.** `ls-remote` on hardware/interfaces lists change 2923712's patch sets and meta ref, and
its fetched notes carry the comment our REST-built example holds. v8/v8 serves meta refs too. A
wildcard `ls-remote` over chromium/src timed out, so Chromium changes are found from its branch
history instead: the one commit fetched from chromium/src (`refs/branch-heads/6099`, committed
2024-10-01, depth 1, no trees) carries `Reviewed-on:
https://chromium-review.googlesource.com/c/chromium/src/+/5901656`. AOSP's commits carry no such
trailer, so every AOSP candidate is mapped to its change through the patch-set refs.

**How a month is enumerated.** The REST query (`status:merged` with a date range) has no branch
filter, so the git route reads every branch too: `refs/heads/*`, and `refs/branch-heads/*` for
Chromium. Each branch's tip is read first, in a throwaway repository; a branch whose tip predates
the month's slack cannot hold a candidate and is not walked (461 of the 516 listed, leaving 55).
Candidates are the walked branches' commits whose committer date falls from `SLACK_DAYS` before the
month to its end. They map to changes by `Reviewed-on:` (Chromium) or by joining their ids against a
`refs/changes/*` listing (AOSP). A change belongs to the month in which NoteDb records its
submission. Commit dates cannot decide that, because AOSP merges the uploaded commit unchanged, so
its date is the upload's. Of the 9,645 `main` changes in the AOSP REST corpus, 191 merged more than
14 days after their final upload and 26 more than 90, so AOSP's slack is 90 days.

**The seal.** A change is collected only if its whole review record predates the test window.
Before anything else about a candidate is fetched, the tip of its meta ref is fetched alone
(depth 1, commit only), and a change last updated at or after 2025-11-01 is dropped and counted
(`touches_test_window`: 1 on this sample). The full record is then fetched by that probed commit
id, never by ref name, and checked again after it is read, so a change updated between the probe
and the fetch, or a rerun over a kept repository, cannot bring window-dated text in. This mirrors
REST, whose month query filters by last update and so never returns a change updated in a sealed
month.

**Diffs are Gerrit's, reproduced.** The REST build cut hunks from Gerrit's `/diff`, whose default
whitespace mode is `IGNORE_LEADING_AND_TRAILING` (JGit's `WS_IGNORE_CHANGE`) and whose algorithm is
JGit's HistogramDiff with a Myers fallback. Git's own histogram diff split one change's hunks
differently from Gerrit on this sample, and no combination of git's options reproduced Gerrit's, so
`sphragis/corpus/gerrit_diff.py` ports JGit's diff and Gerrit's content blocks. It reproduces the
captured review.opendev.org payload block for block. `tests/fixtures/jgit_edits.json` holds 73
cases, 28 through the Myers fallback, and `scripts/jgit_fixtures.py` rederives every one from
JGit 7.8.0 (jar sha256 recorded; `--check` reproduces the file). Three of them are cases where JGit
agrees with the port's Myers bounds handling and not with the idiomatic variant, which the first 70
could not tell apart.

**Successor kind, computed.** REST rows carry each revision's `kind`, and `refine` drops an example
whose next patch set is not a rework. The git route computes `kind` with Gerrit's definitions:
NO_CHANGE and NO_CODE_CHANGE when the trees match over parents with the same trees, and a trivial
rebase when replaying the predecessor onto the successor's parent (`git merge-tree`) reproduces the
successor's tree. A replay the objects cannot support is counted (`kind_unverified`, none on this
sample) and read as REWORK, which keeps the example. Gerrit compares the parents' trees, not their
ids. Before that fix, an earlier rerun the same day read 12 revisions that REST records as
NO_CHANGE (10) or NO_CODE_CHANGE (2) as trivial rebases; that run's artifact was overwritten.
MERGE_FIRST_PARENT_UPDATE is not attempted.

**Parity, from the artifact.**

| | REST | git | both | REST only | git only |
|---|---|---|---|---|---|
| 2024-11 | 113 | 116 | 113 | 0 | 3 |
| 2025-01 | 158 | 152 | 151 | 7 | 1 |

Each difference has one explanation:

- 3 REST-only changes merged in another month and were last updated in this one.
- 4 REST-only changes were merged into temporary branches (`snap-temp-*`, `sparse-*`) that the host
  no longer lists, so no git route can reach them; REST keeps their review records. The artifact
  labels them `branch_not_read`.
- The 4 git-only changes are the converse of the first: REST filed them under their later update's
  month.

Reading `main` alone had missed 63 of the 271 REST changes in this sample (the 2026-09-23 run).

On the 264 changes both hold, every field REST stored and NoteDb can state agrees: 17 change fields,
and 831 revisions' number, creation time, uploader, ref, branch and kind, with owners and submitters
as the same salted pseudonyms. Hashtags agree as a set: REST returns them in hash-set order, which
differs between changes with identical footers. `files`, counted with git's histogram algorithm
because its default Myers counts disagreed with REST, matches REST's `insertions`/`deletions` on 181
changes. Of the other 83, 79 are changes whose own commit is a merge, and 4 differ for a reason the
comparison does not classify.

Examples, 164 on each side with the same ids:

- Against the REST examples as built, 160 are identical. The other 4 each keep a one-click
  "Acknowledged" the build now drops: the REST examples were built before that rule and stamped
  once, and `refine` removes it.
- Against the refined corpora, the ones that train, 163 are identical. The last differs only in one
  @-mention's pseudonym: the REST corpus's came from the older scrub, which hashed part of a
  chained address, and `refine` removes the leftover domain but cannot rehash without the raw text.

**Fields NoteDb cannot supply** (`notedb.GAPS`, carried in every snapshot record):

- the change-level `insertions`/`deletions`, replaced by `files`;
- attention set and comment counts;
- `change_message_id` on comments;
- account tags, so the build's service-user rule cannot fire on git rows; the bot templates in
  `refine` still apply.

Nothing under `sphragis/` or `scripts/` reads any of them. Diffs are stored with the change, so a
NoteDb build makes no request. Two fields exist for the component mapping stage:

- `merged_commit`, the commit on the branch that carries the change;
- `files`, path to `lines_inserted`/`lines_deleted` against its first parent.

Each revision also records `parents`. A git-route month records the digest of the fetch code
(`FETCH_RULES`), and the loader refuses one fetched under other code. An organization's months are
all fetched by one route unless `--allow-mixed-routes` says otherwise, since REST files a change by
its last update and the git route by its submission.

**Rebase edits.** Between patch sets n and n+1 a rebase can sweep upstream edits into the diff that
labels an example. Gerrit marks such edits `due_to_rebase` and `hunks_from_diff` ignores the flag.
The port reproduces Gerrit's attribution: an edit is due to the rebase when it equals one of the
parents' own edits, carried into each patch set's coordinates, where a parent edit the change's own
edits touch is left unattributed. On the sample:

- 60 of the 164 examples sit on a rebase step (the two patch sets have different parents).
- None of their hunks is attributed to the rebase. One overlaps an upstream edit under a looser
  test.
- 27 of 813 edit blocks in these diffs are marked. In the 35 rebased file steps, 38 of the parents'
  53 edits were placed and 15 collided with the author's own.
- Every successor is REWORK, by REST's `kind` and by the git route's.

The build reads the flag on neither route. Dropping a marked hunk only where the git route built a
month would build two organizations under different rules, the asymmetry the data audit exists to
remove, and would change the build rules every month on disk was built and stamped under. On this
sample such a drop would remove nothing (`with_rebase_edit_drop` in the artifact). Whether rebase
edits leave the label is a rule for every organization at once, and the REST snapshots do not hold
the diffs to apply it.

**What the route changes about windows.** Windows are assigned by creation time on both routes, so
a change lands in the same window either way. Only the month a change is filed under differs: 322
of 12,145 merged AOSP changes (2.65%) were last updated in a later month than they merged.
`scripts/censoring.py` models the creation-to-last-update lag and would need the submission time
for an organization fetched this way. Exposures REST did not have, all into a scratch repository
that is deleted with the run:

- The history fetch transfers every walked branch's commits up to its tip, merges after the month
  included; enumeration parses only the commits inside the month's range.
- The seal's probe transfers the newest meta commit of a change updated in the window (its latest
  change message), read for its date and discarded.
- AOSP's ref listing names every change number in the project.

**Found while building it.** Each of these failures was silent or looked like a host problem, and
each is now a test:

- A filtered fetch records its filter as the remote default, so the meta fetch silently arrived
  without note blobs. The route now clears the recorded filter after each fetch.
- A depth fetch into a shallow repository marks a held parent shallow, which cut 107 commits out of
  every later walk of `main` and a rerun's changes from 536 to 430. Marks whose parents are held
  are now removed.
- `git cat-file --batch` reports a missing object as a line rather than an error, and the reader
  had skipped it. It now raises.
- A bare repository reads `HEAD:.mailmap` for a log and `.gitattributes` from HEAD's tree for a
  diff. Reading every branch fetched the one HEAD named, commits only, so every such command failed
  on a lazy fetch. HEAD now names a ref no fetch writes, and no command reads a mailmap.
- A shallow-since fetch over every branch at once was refused by the host when dormant branches
  were included, and probing the tips inside the walked repository left shallow marks a later walk
  tripped on. The tips are now read in a throwaway repository and dormant branches are not walked.

**Requests.** All paced at one a second or the host's crawl delay, whichever is longer; a git fetch
is several HTTP requests and is billed for all of them. A command that fails for a transient reason
(a connection that never opened or dropped, a 5xx server error) is retried twice, after 30 s and
120 s, each try paced and ledgered; a 403 or 429 refusal is never retried. By the local run
ledgers, three of the day's runs needed a retry for blob or history fetches.

- 2026-09-23, first run: android.googlesource.com, 20 fetches and 66 HTTP requests, 3 of them
  entered by hand for one untraced lazy fetch during manual inspection, plus 7 fetches and 21
  requests for one end-to-end run of `fetch --via git` and `build` over 2024-11 (a check, not kept
  as an artifact); chromium.googlesource.com, 1 fetch, 3 requests.
- 2026-09-25, rerun: android.googlesource.com only. The artifact's run made 26 operations and 62
  HTTP requests. Counting the attempts that failed on the bugs above and the diagnostic runs, the
  day's run ledgers (kept locally, not committed: they are per-run scratch output) hold 155
  operations and 411 HTTP requests; the first failed attempt's ledger was
  deleted with its scratch directory before ledgers were kept, and it made one ref listing and one
  refused history fetch.
- Nothing else was contacted by the route. Separately, at about 15:05 EDT on 2026-09-25 a review
  subagent, meaning to print a command, ran `git ls-remote` on an scp-style address naming
  chromium-review.googlesource.com: git read it as ssh, so there was a DNS lookup and one ssh
  connection attempt to port 22, which hung about two minutes and was killed. No git request could
  have been sent (no ssh identity or host key for that host; `~/.ssh/known_hosts` unchanged).
  Review prompts now forbid running git against any non-`file://` URL.

### Registered before the human's figures: a reported slip stays as locked (2026-09-24)

The check page locks the human's label and outside-names answer before it reveals rater A's. A
checker can notice a mis-click only after the reveal, and a correction made after seeing the rater
is no longer blind, so it does not replace the locked answer. The rule, fixed before any human
agreement is computed: every human figure is read on the answers as locked, and each pair a
reported slip touches is also reported without that item (`human_slips` and the `without slips`
pairs). Slips are marked on the page beside the locked answer and saved with the check;
`label_audit_agreement.py` reads them from there, and `--slip ITEM:FIELD` adds one by hand.

### Registered: negative readings are bounded by each cell's detectable effect, and rebase edits get a sensitivity analysis (2026-09-24)

**Negative readings.** The proposal of 2026-09-23 is adopted. A cell reads *bounded* when its
interval's upper bound lies below the effect the sensitivity analysis says that cell detects at
the Holm level it is read at; the gate reads the bounds from `decomposition-sensitivity.json`
(`detectable_effects`) and refuses a confirmatory cell without one at every level. The SESOI band
is reported beside every cell and decides nothing. Justification: Lakens, Scheel and Isager (2018)
list the effect a study was designed to detect among the bases for an equivalence bound, and
Lakens (2022) asks a design to say which effects it is informative about when it cannot resolve
the SESOI. The bound is reachable where absence was not: the regenerated sensitivity artifact
records `null_reads_bounded` per cell and level beside `null_reads_absent`: a true null reads
bounded 0.97 of the time in three of the four H1 cells and 0.94 in Qt's at 95%, against 0.000
absent in all four; every other figure in the artifact reproduced exactly. The bounds are
recomputed at `N` on corpus v2 before the seal opens. A reversed effect now reads bounded, since
H1 is directional.

**Rebase edits.** Kept in the primary label on every organization. The stored REST snapshots
hold neither Gerrit's `due_to_rebase` flag (the build fetches each diff and keeps its hunks) nor
patch-set parents, so a drop could reach no held REST corpus without refetching, which robots.txt
forbids on Qt. The test window is collected fresh, so its collection records the flag per hunk
beside the build output, and H1 and H2 are re-read with flagged hunks removed as a registered
sensitivity analysis. Paixão and Maia (SCAM 2019) find rebasing in 75% of Gerrit reviews and ask
review-mining studies to handle it rather than filter reviews out.
### Registered before the next job: under our reading, FDLoRA's personalized module is always something the server has received (2026-09-24)

A second review of PR #36 ran the script against a fake model stack and found that the saved
`-local` module is never a withheld tensor. This follows from the reading registered on
2026-09-21, not from the code.

**Why.** Under reading 2, the pseudocode's, the personalized module trains only in Stage 1
(Algorithm 1, lines 1 to 6). Stage 2 trains the global module (line 12), and on a sync round line
14 sets the personalized module to the client's own locally optimized global, `theta_s(i)(t)`,
which is the upload that round: line 17 averages its difference from the dispatched global.
After Stage 1, then, a client's personalized module is one of two things:

- its Stage 1 module, which reading 1 already counts as transmitted at initialization (line 7);
- its upload from the most recent sync round.

**What that means for each schedule.**

- `H <= T`: `-local` is the upload of the last sync round. At the default `ROUNDS=6 SYNC_PERIOD=5`
  it is the round-5 upload.
- `H > T`, including the paper's `H = 10` and `H = infinity` at `T = 6`: no sync fires and
  `-local` equals `-p0`.
- `H` dividing `T`, including `H = T`, which the paper tests: `-local` equals the final upload, the
  case the final-sync refusal already excludes.

The 2026-09-23 entry's reason for `SYNC_PERIOD=5` is corrected here. Reading the withheld half "one
round after its last sync" keeps it from equalling the final upload. It does not keep it from being
an upload.

**Consequence, registered before any job.** Read by its pseudocode, FDLoRA's split withholds no
parameters, and "remains uninvolved in the federated learning process" holds for no schedule. The
script records what `-local` equals in its output (`local_equals`), and an attack that reads
`-local` is reported as reading a past transmission. The 2026-09-21 prediction is restated: `H`
changes how many rounds old that copy is when the run ends, not whether it was sent.

The prose reading, where the inner loop trains the personalized module, would make it a withheld
tensor between syncs. It was rejected on 2026-09-21 because the outer step would then receive a
zero update. This finding rests on that choice, and the report says so.

**Implementation choices the registration did not state**, disclosed here before any run:

- `K` is read as epochs. The paper calls `K` inner "steps" (Section 3.4) and "the local update
  epochs K = 3" (Section 4.2).
- Stage 1 trains for the same two epochs as every other adapter here (`TRAINING`).
- Each round restarts AdamW and its warmup and cosine schedule under the same seed, so a client
  sees its data in the same order every round.

**Also fixed, found by the same review.**

- The script read client corpora without the loader, so it would have trained on v1 client corpora
  (which still hold Qt's bot comments) without noticing. It now reads through `derived_file_rows`
  like every other training script and records `legacy_corpus`. `scripts/preflight_pilot.py` had
  the same gap. A test now requires every script that builds training items to read through
  `sphragis.corpus.load`.
- The schedule's refusals (`validate_schedule`), the round-0 average over every client
  (`round0_seed`) and what `-local` equals (`local_provenance`) are pure, tested functions in
  `sphragis/experiment/fdlora.py`. A reintroduced per-source average now fails the suite, and a bad
  `K` or `T` is refused before Stage 1 writes anything.
- Two source specs that map to one client label are refused; before, the second silently replaced
  the first and the round-0 average lost a client.
- `fdlora_schedule.sbatch` tags results with the packing seed, so two draws no longer collide, and
  only `ALLOW_FINAL_SYNC=1` enables the override.

