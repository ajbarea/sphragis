# Sphragis — Active Implementation Log

Rolling notes on in-flight work. See `ROADMAP.md` for the stable plan.

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

## Open bugs & findings

_None active._
