# Sphragis — Roadmap

Sphragis tests whether an organization leaves a learnable fingerprint in the code it reviews,
and what a declared boundary protects once it can. It is the apparatus for the Federated Agents
research direction: RQ1 is a go/no-go gate, RQ2 the leakage study that follows it.

**Design of record:** `docs/superpowers/specs/2026-09-13-gerrit-review-corpus-harness-design.md`
**Deadline that sets the order:** MSR 2027 Registered Reports Stage 1, **2026-11-20**.
The pre-submission checklist lives in the `papers` repo at `org-fingerprint/STAGE1-SKELETON.md`.

---

## Plan A — corpus construction

- [x] `scrub` — salted pseudonyms, identity fields nulled, emails swept from free text.
- [x] `manifest` — per-window counts and content hashes over a shared provenance header.
- [x] `gerrit` — XSSI prefix, cursor paging, `Retry-After`, client errors not retried.
- [x] `examples` — changed hunks, comments anchored by line range, unanchored hunks dropped.
- [x] `dedup` — exact, near-duplicate, repeated-boilerplate.
- [x] `split` — changes assigned whole to windows; the test window sealed, not collected.
- [x] `cli` — stage dispatch.
- [x] **Plan A2 — stage bodies.** Every stage is a command and the pipeline runs end to
  end on real data. `storage` (immutable snapshots), `build` (change to examples, drops
  counted by reason, resumable), `fetchers` (scrubbing at ingestion), `pipeline` (dedup,
  split, freeze), all wired behind `python -m sphragis.corpus`.
- [x] **A real frozen corpus.** Both organizations frozen and verified: OpenStack 5,498
  examples (pilot 606 / train 4,327 / dev 565), Qt 10,695 (1,301 / 8,443 / 951). Each window
  content-hashed beside its drop profile, package versions and the SHA that produced it. The
  test window stays sealed and uncollected.
- [x] **Decide how windows are assigned.** Measured by Lynden-Bell on the changes the corpus
  keeps (`scripts/censoring.py`): creation windows stand. The confound is the dev window
  abutting the collection boundary (39.1% of OpenStack's example-bearing cohort missing
  against 28.8% of Qt's, a 10.33 point differential), not creation assignment. The test
  window is fetched after in-principle acceptance, five months after it closes, where the
  differential is 0.36 points. The decision has survived three revisions of these figures.
- [x] **Test the stationarity assumption rather than assert it** (`scripts/quasi_independence.py`).
  The per-cohort rate comparison is biased by the truncation it is meant to detect: a
  stationary law reproduces most of the apparent trend. Tsai's conditional Kendall tau rejects
  quasi-independence for neither organization (+0.017 and -0.002, both intervals covering
  zero).
- [ ] **Explain OpenStack's misfit.** Still open after three candidates were tested and
  rejected: a latency trend (the conditional Kendall tau clears both organizations), a
  mixture of project families (`openstack/*` alone still rejects at p = 0.035), and Qt being
  the more homogeneous corpus (false -- Qt's per-project F(0) spans 0.512 to 0.758, wider
  than OpenStack's family gap). The recorded bound holds: a recent-cohort refit moves the dev
  figure about seven points toward less censoring and the registered figure stays the
  conservative pooled one.
- [x] **Latency differs by project inside one organization**, which the misfit investigation
  turned up on the way: `qt-creator` settles 76% of its changes in their creation month
  against `qtdeclarative`'s 51%. Corroborates the separability probe from a measurement with
  no mechanism in common with it.
- [x] Registered: the test window is fetched no earlier than three months after its final
  month (`FETCH_HORIZON_MONTHS`).
- [ ] State the dev window's censoring wherever dev numbers appear; they are pre-registration
  estimates, not unbiased previews.

## Plan B — measurement

- [x] `score` — exact match binds the pass rule; normalized EM and edit similarity reported.
- [x] `stats` — pairs cluster bootstrap over changes; `gate_verdict` is the pass rule as code.
- [x] `contamination` — Min-K%++ (base checkpoint), guided completion (Instruct model), time
  partition, each post-versus-pre. Min-K% had been implemented under the Min-K%++ name; fixed.
- [x] **Battery run on real data** (2026-09-15), OpenStack 2024-10 against a 2024-01 control.
  Scored on hunks with context: 161 / 116 examples, Min-K%++ gap -0.058 [-0.200, +0.084],
  guided completion at its floor. Windows inseparable; read as ambiguous, as pre-committed.
- [x] **Control window fixed; the battery is uninformative, and now says why** (job 148092).
  Min-K%++ gap -0.074 [-0.129, -0.018] on 1,971 / 2,503 examples, but a model-less
  bag-of-words classifier separates the same windows at balanced accuracy 0.589 against
  Min-K%++'s AUC of 0.540. By Meeus et al.'s criterion (SoK, SaTML 2025) the gap is
  indistinguishable from drift. Contamination protection rests on the post-cutoff windows
  postdating the checkpoint's release, not on the battery.
- [ ] Report Gap-K% (arXiv:2601.19936, May 2026) beside Min-K%++. It needs only the top-1
  log-probability, already computed on the way to Min-K%++'s statistics; +2.1 to 2.6 AUROC on
  WikiMIA, untested on code. Reported, not registered.
- [ ] Register hunks-with-context as the scored text.
- [x] **Source the checkpoint's cutoff.** The widely repeated "June 2024" for Qwen2.5-Coder is
  a community guess, not a maintainer statement. The technical report states a repository
  *creation* filter of February 2024, which bounds nothing for repositories as old as
  OpenStack and Qt; the content cutoff is unstated. The post-cutoff window does not depend on
  it, starting after the checkpoint was published (research log).
- [x] Registered: Min-K%++ primary, time partition corroborative only.
- [x] Registered: guided completion is reported as a null instrument, at its floor under
  both verbatim and edit-similarity criteria.
- [x] Purity enforced by test: no measurement module may import a GPU stack.

## Plan F — RQ2 positioning

Added 2026-09-17 from a literature pass against the 2026 state of the art.

- [x] **ProjRes (arXiv:2604.21197) read in full.** Exact-membership only, per-client gradients,
  no secure aggregation assumed; AUC 1.000 on short text, 0.807 to 0.819 on long. Its own stated
  limitation is the semantic leakage RQ2 is about.
- [x] **Secure aggregation is not a defence to lean on**: gradient disaggregation
  (arXiv:2106.06089) and client-specific inference under secure aggregation (arXiv:2303.03908).
- [x] **2303.03908 read in full.** PROLIN on MNIST, CIFAR-10 and Fashion-MNIST with a LeNet,
  inferring membership and misbehaviour only; source is not studied. It states the method
  generalises to "any supervised detector model", which gives RQ2 its secure-aggregation
  attack: a source-identity detector plugged into PROLIN's disaggregation.
- [ ] **Frame RQ2 at two altitudes**: does an adapter update reveal its organization, its
  project, or both, and which boundary an organization-level privacy policy actually protects.
- [ ] Two threat models, per-client updates and aggregates over rounds of varying composition,
  since the second has been shown not to be a defence.

## Plan E — calibration

Added 2026-09-16. Every null was ambiguous between "no fingerprint" and "a blind instrument".

- [x] **The contrast is not blind** (job 145092, `marker-1`). A convention planted on every
  refinement of one half returns +0.310 [+0.249, +0.376] and +0.316 [+0.265, +0.367] over 182
  changes each, and the gate passes. RQ1's nulls are about organizations, not the instrument.
  For scale: the planted convention moves the contrast +0.31, the real OpenStack/Qt difference
  +0.021.
- [x] **The halt rule fired on a real run for the first time.** The mismatched arms score
  exactly 0.000, since an adapter trained on annotated refinements can never match an
  unannotated reference, so `non_degeneracy` failed and `apparatus_holds` went false. The
  ceiling condition is degenerate by construction and the apparatus is what says so; the pass
  is evidence the instrument works, not a result.
- [x] **Negative control clean** (148088): two halves differing in nothing return intervals
  covering zero on both sides, and the adapters emit the planted annotation 0.000 of the time.
- [x] **The floor is set by what the adapter learns, not by the contrast** (148089-148091).
  Emission against training rate: 0.039 -> 0.007, 0.086 -> 0.03-0.08, **0.251 -> 0.62-0.66**,
  1.0 -> 0.99. Below a tenth the convention is barely absorbed; at a quarter it is absorbed and
  amplified. My prediction of a floor near 0.05 to 0.1 was wrong in kind, not just in place.
- [ ] **The gate is non-monotone in fingerprint strength.** At 0.25 the planted adapter
  over-applies the convention and loses on its own half (0.160 against 0.246), so the contrast
  there is -0.086, excluding zero on the refuting side. Between floor and ceiling the gate never
  passes. A Stage 1 validity issue: decide whether to register it as a stated limitation, or a
  metric less punishing of over-application beside exact match.
- [ ] **Symmetric calibration**: convention X on one half, Y on the other, since two organizations
  each carry their own. Shows whether the matched adapter wins on both sides once neither half is
  convention-free.
- [ ] Test whether greedy decoding causes the amplification: rerun 0.25 with sampling at
  temperature 1, which should reproduce the training rate if greedy is the amplifier.

## Plan D — granularity

Added 2026-09-16, from a probe the design never considered. The study fixes the organization
as the unit; the evidence says the codebase is.

- [x] **Separability probe** (`sphragis/measure/probe.py`, `scripts/separability.py`). Is
  organization decodable from review text at all? Content-controlled and compared against a
  within-organization baseline: cross-organization 0.849 [0.769, 0.867] against 0.828 and
  0.892 for two projects inside one organization. It does not beat its own baseline, so the
  classifier reads the codebase, not the organization. CPU only, runs in a minute.
- [ ] **Project-level contrast.** Train adapters on two projects inside ONE organization and
  run the identical matched-versus-mismatched contrast. Needs no new experiment code: build
  per-project corpora and pass them where `--corpus org=path` expects organizations.
- [ ] Register the probe and the project contrast as Stage 1 secondary analyses, with the
  within-organization baseline as the reading rule rather than raw accuracy.
- [ ] If the project contrast separates where the organization contrast does not, say what
  that costs the direction: a privacy perimeter drawn around an organization is not drawn
  where the signal lives.

## Plan C — the experiment

- [x] `grid` — the condition grid as data. The base arm is evaluated once per window, not
  once per seed, so its sample is not silently inflated. 14 evaluations, 6 training runs.
- [x] `power` — pilot power analysis by simulation: bisect the effect until bootstrapped
  power reaches 80%. This is what section 5 of the Stage 1 report needs.
- [x] `runner` — orchestration against `Generator` and `Trainer` protocols. Pairs arms on
  example id rather than position, and refuses arms evaluated on different examples.
- [x] Purity enforced: only `model.py` may import a GPU stack, and a guard catches any new
  unguarded module in the package. Both guards tripped to confirm they fire.
- [x] `model.py` — base model, LoRA adapters, greedy decoding. Developed against a local
  RTX 3060 Ti on the same torch build TIGRIS resolves (2.14.0+cu130).
- [x] `slurm.py` — sbatch generation, verified against the live cluster. **One allocation
  walks the whole grid**: per-cell jobs would be twenty independent queue waits, and a
  freshly submitted job was estimated thirteen days out at fairshare 0.006.
- [x] **RQ1 on the study's own windows** (2026-09-15, job 144345, 2:19:50). Train window to
  dev window, equalized, one seed. Both contrasts positive for the first time: OpenStack
  +0.021 [-0.003, +0.047] over 235 changes, Qt +0.011 [-0.021, +0.043] over 175. Gate fail
  under both estimands; normalized EM agrees. Adaptation is sixfold (0.048 to 0.306) and the
  organization-specific part of it is 2 points: an OpenStack adapter scores 0.308 on Qt's
  refinements against Qt's own 0.319. Dev windows, censored 37.0% and 17.9%, not the answer.
- [x] Outcome-neutral tests wired to real model outputs: all seven pass on job 144345,
  reading live losses and adapter norms rather than being computed after the fact.
- [x] Grid `--time` sized from measured throughput: 38-43 tok/s steady on a GH200.
- [x] **Pilot floor check passed** (rerun 2026-09-15, after review removed three confounds).
  Exact match 0.074 base, 0.407 adapted, +0.333 [+0.133, +0.565] over 18 held-out changes;
  normalized exact match +0.148 [+0.036, +0.292]. Exact match discriminates on this corpus
  once a model is adapted, and about half the gain survives normalization.
- [x] Per-change outcomes from the pilot, and the bootstrap interval on them.
- [x] **Review of the apparatus.** The gate reads direction; train and eval share one
  prompt format; the training budget is realized as declared and tested in CI; changes
  outside every window block the freeze; drop counts persist.
- [x] Both estimands computed in one pass (`gate_under_each_estimand`), naming no primary, so
  the choice cannot be made after the numbers are visible. `agree` records whether it mattered.
- [x] Registered: pooled. Cluster size is non-informative and change-averaged runs at twice
  nominal alpha (`scripts/estimands.py`, registered decisions table).
- [x] **RQ1 grid at pilot scale** (2026-09-15, job 143899): both organizations, base plus both
  adapters on both held-out sets, through `walk` and `gate`. Matched minus mismatched exact
  match: OpenStack -0.037 [-0.152, +0.087], Qt +0.045 [+0.000, +0.099]. Pilot-scale gate
  fail, and not the RQ1 answer: one month, one seed.
- [x] Equal-size training: organization was confounded with training-set size (Qt 422 against
  OpenStack 145). `--equalize-train` subsamples to the smallest.
- [x] Equalized RQ1 pilot rerun (job 143957): both contrasts changed sign (OpenStack +0.037
  [-0.069, +0.182], Qt -0.038 [-0.089, +0.009]). Volume outweighs organization at this scale,
  and run-to-run variation is as large as either contrast.
- [x] Power analysis on the RQ1 pilot's variances (`scripts/power_rq1.py`): detectable gain
  about +0.030 exact match for OpenStack at ~880 changes, +0.012 for Qt at ~2,400; OpenStack
  binds. Needed a fix first: the simulation had treated the pilot's own difference as null.
- [x] Power at report-grade settings (400 trials, 1,000 resamples, tolerance 0.005, 3 seeds):
  OpenStack +0.030 [+0.028, +0.031] at 880 changes, Qt +0.011 [+0.011, +0.012] at 2,400.
- [x] **Power on the windowed run's variances, at the projected test-window sizes.** OpenStack
  MDE +0.0141 at 1,804 changes against an observed +0.0212; Qt +0.0133 at 3,197 against an
  observed +0.0110. **Qt now binds, where the pilot analysis said OpenStack did**, because
  what binds is the ratio of effect to MDE and Qt's effect sits below what its sample detects.
- [x] **Pass rule registered: conjunctive, and powered as conjunctive.** The gate's power is
  the joint probability, 0.757 over ten test months; the window is extended to twelve, which
  reaches 0.833 (`scripts/conjunctive_power.py`).
- [x] Replace the estimated test-window sizes: projected from the train window through the
  capture model (`scripts/project_windows.py`), OpenStack ~1,809 changes against the 880
  assumed and Qt ~3,281 against 2,400, so the registered MDEs are conservative. The held-out
  check passes for both without having seen either dev window, OpenStack at -5.3% and Qt at
  +11.6%, and the script now refuses to project when it misses by more than 15%. The window
  itself stays sealed.
- [x] Registered: equal-size training, the pooled estimand, and strictly-above-zero at the
  pass rule's boundary.
- [ ] The confirmatory RQ1 contrast, on the frozen windows after in-principle acceptance.

**Metric change forced by a real run (2026-09-14).** The model answers refinement prompts
correctly and wraps the answer in prose and a markdown fence, so raw exact match scored 1
of 3 on output that was 3 of 3 right. `score()` now scores extracted code and reports
`exact_match_raw` beside it. This changes the primary metric's definition and is therefore
a Stage 1 pre-registration item, not an implementation detail.

Plan C is the only part needing a GPU, and the only part needing collected data.

## Completed

- **2026-09-15** — Outcome-neutral tests as code (`sphragis/experiment/neutral.py`) and the apparatus halt rule.
- **2026-09-15** — Fetch refuses the sealed test window and every month after it.
- **2026-09-15** — Power analysis at report-grade settings: about 3 exact-match points detectable, OpenStack binding (`scripts/power_rq1.py`).
- **2026-09-15** — RQ1 grid at pilot scale, unequal and equalized training (`scripts/rq1_pilot.py`); organization confounded with volume, fixed.
- **2026-09-15** — Contamination battery on real data, bare hunks and with context (`scripts/contamination_battery.py`).
- **2026-09-15** — Structured review of PR #13: directional gate, one prompt format, the declared training budget realized, seal and freeze guards.
- **2026-09-14** — Pilot floor check: adaptation moves exact match on this corpus.
- **2026-09-13** — Repository split from phalanx-fl; corpus stages A2.

## Registered decisions

Each of these is fixed before the seal opens and stated in the Stage 1 report. Every one is
recorded with the evidence that chose it, so a reader can check the choice rather than take
it. Changing one after the test window is fetched is a protocol deviation and has to be
declared as one.

| decision | registered | why |
|---|---|---|
| **Estimand** | pooled (`paired_difference`) | Cluster size is non-informative here: correlation with the outcome is -0.052 and -0.019, with the per-change effect -0.025 and +0.004, so the two estimands do not answer materially different questions (Kahan et al., IJE 2023, make this the deciding test). RQ1 is a claim about refinements, so the participant-average is the matching unit. And change-averaged runs at roughly twice nominal alpha at 48-91 changes where pooled sits at nominal, which disqualifies it from binding a confirmatory gate. Both are computed and reported. |
| **Pass rule** | conjunctive: both organizations' 95% intervals strictly above zero | RQ1 claims organizations leave a fingerprint, which is a generality claim; a rule passing on one organization does not support it. Weakening it to raise power would change the question to fit the answer. |
| **Boundary** | strictly above zero (`>`) | Not a formality: on the unequalized pilot Qt's pooled lower bound is exactly `0.0` in 19 of 20 bootstrap seeds, so `>=` would have read that run as supporting the hypothesis. |
| **Reported power** | conjunctive, not marginal | The gate passes only when both arms do, so its power is the joint probability, which for near-independent arms is the product: two arms at 80% give a gate at 64%. Section 5 had been stating two marginal figures. At the projected sizes and the effects the windowed run showed, the gate reaches 0.833 with Qt binding at 0.833 and OpenStack saturated. |
| **Test window** | 2025-11 to 2026-10, twelve months | Ten months gives the gate 0.757. Twelve gives 0.833, for 0.4 points of additional differential censoring (0.36 to 0.75), still a twelfth of what the dev window carries. Set before any test data exists: 2026-10 closes before the 2026-11-20 submission. |
| **Fetch horizon** | no earlier than three months after the window's final month | Makes the confirmatory contrast's low censoring a protocol guarantee rather than an accident of when acceptance landed. 2026-10 plus three is 2027-01; MSR 2027 notifies 2027-02-04, so the horizon binds only if acceptance comes early, in which case the answer is to accept more censoring rather than fetch sooner. |
| **Contamination** | Min-K%++ on the base checkpoint is primary; the time partition is corroborative only | Temporal decay is not dependable contamination evidence (Zhang et al., ACL 2026): item construction distorts it independently of the source. Identical construction across windows answers their specific confound, not the confound with ordinary distribution shift. |
| **Guided completion** | reported as a null instrument | At its floor under both criteria the literature offers: 0 of 55 hunks verbatim, and edit similarity 0.380 against 0.387, a gap of -0.007 [-0.118, +0.085]. Swapping criteria until one separates is what pre-registration exists to prevent. |
| **Leakage threshold** | 2% at Jaccard >= 0.7 | Must clear the measured train-into-dev rate, 1.06% for OpenStack and 0.42% for Qt. At J >= 0.8 both are 0.0000 by construction, because dedup removes pairs at that threshold across windows, so registering there is a test that cannot fail. |

**Open, and genuinely a question about the claim rather than the statistics.** Whether RQ1
asserts "organizations leave a learnable fingerprint" (conjunctive, as registered above) or
"this organization does" (per-organization, higher power, weaker claim). Everything above
assumes the first, which is what the research question as written says.

## Study invariants

These are not style rules. CI asserts them, and loosening one has to show up in a diff.

- **The test window is sealed.** It is defined and hashed at Stage 1 and fetched only after
  in-principle acceptance, so fetch timestamps are the Stage 2 evidence that collection
  followed acceptance.
- **One binding metric.** Exact match decides the gate. Normalized EM and edit similarity are
  reported and are not part of the pass rule. LLM-as-judge is excluded from both.
- **The measurement runs without a GPU.**
- **Dependencies are pinned.** A paper artifact has to reproduce years from now.

## Relationship to the other repos

Sphragis is the Federated Agents apparatus. It does not vendor the others.

| Repo | Relationship |
|---|---|
| `phalanx-fl` | RQ2 federates adapters on its Flower stack. Not a dependency yet; decided when RQ2 starts. |
| `velocity-fl` | Aggregation kernel and Byzantine arena for RQ2. |
| `pharos` | The disclosure-measurement methodology this study reuses on real rather than generated data. Cited. |
| `papers` | `org-fingerprint/` holds the Stage 1 report. LINEAGE row P4. |

`provenance.py` is copied from phalanx-fl, not imported. Twelve lines of standard library is
the wrong thing to take a cross-repo dependency for, especially on a repo whose stated
invariant is to ride the latest Flower while this one must stay reproducible.
