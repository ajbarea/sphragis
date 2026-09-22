# Sphragis — Roadmap

Sphragis tests whether an organization's house style -- the tacit conventions it never wrote
down -- is learnable from the code it reviews,
and what a declared boundary protects once it can. It is the apparatus for the Federated Agents
research direction: RQ1 is a go/no-go gate, RQ2 the leakage study that follows it.

**Design of record:** `docs/superpowers/specs/2026-09-13-gerrit-review-corpus-harness-design.md`
**Deadline that sets the order:** MSR 2027 Registered Reports Stage 1, **2026-11-20**, with the
abstract a week earlier on **2026-11-13** (read from the call, 2026-09-18). PC reviews come
2026-12-23, the response letter and revision 2027-01-15, Stage 1 notification 2027-02-04, the
accepted report 2027-02-28, and the full paper to EMSE 2027-09-30.
The pre-submission checklist lives in the `papers` repo at `org-house-style/STAGE1-SKELETON.md`,
and the call itself, quoted, at `org-house-style/VENUE.md`.

**What the call requires of this code:**
- Stage 2 is when "the actual data collection, experiments, and analysis" happen, so the test
  window stays sealed until in-principle acceptance (2027-02-04) and pilot numbers never feed a
  confirmatory result.
- Reviewers score whether the procedure can be exactly replicated and whether outcome-neutral
  tests with positive controls are pre-specified, so dependencies stay pinned, the pass rule
  stays in code, and those tests run before 2026-11-20. A power analysis is expected.
- Every deviation from the accepted protocol must be reported in the EMSE paper, so any change
  after acceptance that touches the protocol is recorded with its date and reason.
- Stage 1 review is not double-anonymous: the track chair confirmed it by email on 2026-09-18,
  and is correcting the submission site, which was set for blind submissions. The artifact link
  may therefore identify the author and no anonymized mirror is needed. Whether this repository
  is public at submission is a separate decision, and the report needs a link a reviewer can
  open.
- Generative-AI use must be disclosed in the paper, so it is logged as the work happens.

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
  examples (pilot 606 / train 4,327 / dev 565), Qt 10,692 (1,300 / 8,442 / 950). Each window
  content-hashed beside its drop profile, package versions and the SHA that produced it. The
  test window stays sealed and uncollected.
- [x] **Refrozen 2026-09-18, one example per id.** A Gerrit Change-Id is shared across
  cherry-picks, so three Qt pairs created weeks apart shared an example id; one crashed RQ1 job
  148093 after 4h13m of evaluation. Dedup now keeps the earliest per id. Both manifests were
  deleted deliberately and refrozen so they cite one pipeline version; the old ones are in git.
- [ ] `verify` checks that the frozen files are intact, not that current code reproduces them:
  before the refreeze it reported Qt clean at 10,695 while the pipeline yielded 10,692. Decide
  whether a reproduction check belongs beside it.
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
- [x] State the dev window's censoring wherever dev numbers appear; they are pre-registration
  estimates, not unbiased previews. Stated with the three-seed RQ1 result: 39.1% and 28.8% of the
  dev window's changes were still open at collection, against 1.1% and 0.7% in the test window.

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
- [x] Gap-K% (arXiv:2601.19936) implemented from the paper and wired through the battery, which
  now records how many smoothed windows the unscored positions cost. It needs only the top-1
  log-probability, recorded from the same log-softmax. Reported, not registered.
- [x] **Gap-K% measured** (job 149555): gap -0.0558 against Min-K%++'s -0.0746 and Min-K%'s
  -0.1296. The shift-robust statistic returns the smallest separation of the three, which is what
  the "differently distributed rather than differently memorized" reading predicts. Its window-cost
  diagnostic reported one example under a plural name and now aggregates over the side; the true
  corpus-wide cost rides along with the next battery run.
- [x] Registered: hunks with three context lines either side are the scored text (table below).
- [x] **Source the checkpoint's cutoff.** The widely repeated "June 2024" for Qwen2.5-Coder is
  a community guess, not a maintainer statement. The technical report states a repository
  *creation* filter of February 2024, which bounds nothing for repositories as old as
  OpenStack and Qt; the content cutoff is unstated. The post-cutoff window does not depend on
  it, starting after the checkpoint was published (research log).
- [x] Registered: Min-K%++ primary, time partition corroborative only.
- [x] Registered: guided completion is reported as a null instrument, at its floor under
  both verbatim and edit-similarity criteria.
- [x] Purity enforced by test: no measurement module may import a GPU stack.

## Plan G — variance the gate does not see

Added 2026-09-18. A pure null crossed zero: `sym-0` and `marker-0` are byte-identical experiments
with identical seeds, and one returned -0.036 [-0.065, -0.007] on a side where the other returned
-0.018 covering zero. Neither training nor inference is bit-reproducible on the GPU; even the base
model changes 23 to 28 of about 440 greedy predictions between runs.

Revised the same day. The swing is what seed-by-change noise produces (z = -1.47 and 0.00 against
it), and a single-seed change bootstrap already carries that noise. What it omits is a seed main
effect, a shift common to every change, which two runs give no evidence of (rough estimate 0.005).

- [x] **A crossed seed x change bootstrap** (`stats.crossed_bootstrap`, `walk.crossed_gate`): Owen's
  pigeonhole bootstrap, seeds and changes resampled independently, because they are crossed rather
  than nested. Built beside the registered gate, not registered. Reviewed; three findings fixed
  (simulated seed effect under-sized by clipping, a merge that accepted different studies, seed runs
  overwriting seed-1 results without a tag).
- [x] **Coverage measured** (`datasets/results/crossed-coverage.json`): the crossed interval is
  nominal at five seeds throughout and at three up to a seed effect of 0.01 (0.073 two-sided at
  0.02); the median-seed rule reaches 0.122 at three seeds and 0.02. No width cost when there is no
  seed effect.
- [x] **Seed main effect measured on the null**: sigma_b about 0.013 over three seeds and two
  halves (`seed-effect-sym-0.json`). Past 0.01, so five seeds by the coverage rule.
- [x] **Seed effect in RQ1's own setting**: 0.000, one-sided 95% upper bound 0.0098 over three
  seeds (`seed-effect-qtfull.json`). The null's 0.013 came from a quarter of the training data.
- [x] **RQ1 re-read at three seeds** (148404, 148406): Qt +0.0312 [+0.0080, +0.0565], OpenStack
  +0.0171 covering zero, mixed under both rules and both estimands.
- [x] **Registered: the crossed interval, three seeds**, by the rule fixed before the runs landed.
- [x] **Registered: fp32 inference**, which reproduces across jobs and nodes where bf16 does not,
  at no measurable cost (a pilot ran 12m31s against bf16's 12m41s).
- [x] **Sensitivity replaces power at an observed effect**: the design resolves 1.3 to 2.4
  exact-match points, bracketing the seed effect's own uncertainty.
- [x] **Dev window re-read under fp32 at three seeds** (149258 to 149260): pooled +0.0230 and
  +0.0316, mixed, with OpenStack's lower bound exactly 0.0. Qt matches its bf16 reading; OpenStack
  is half a point higher and still short. Change-averaged under the same interval would pass, which
  is why the estimand was registered in advance.

- [x] **The medium is not visibly moving**: within each organization, its earliest quarter against
  its latest is at chance (OpenStack 0.552 [0.466, 0.602], Qt 0.510 [0.487, 0.572]) over the span
  that separates training from test. A cross-organization series is not measurable here, since the
  only shared suffix is Python and Qt stops writing it mid-window.
- [x] **The code side is at chance too** (0.511 and 0.513), read as convention shapes rather than
  identifiers, so the medium the study trains on is not visibly moving within its year.

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
- [x] **FedAttr read** (Zhang, Guo, Huang, arXiv:2605.06596, May 2026): identifies under secure
  aggregation which clients trained on *watermarked* data, claiming 100% TPR and 0% FPR. The server
  draws M random client subsets with and without the target each round and differences their
  aggregates; honest-but-curious, LoRA on Llama-3.2-3B, 10 to 100 IID UltraChat clients, KGW and
  fictitious-fact watermarks. Naturally occurring source features are not discussed.
- [x] **RQ2's aggregate attack is FedAttr's mechanism with the watermark removed**, and it works:
  an organization's participation is detectable from a round's aggregate, and the paired subset
  difference recovers its direction where a stranger's gives nothing.
- [x] **Weight-space geometry of the saved adapters** (job 148438): initialization dominates (same
  data, new seed: 0.13; rerun at the same seed: 0.96), training length next (0.21 at 788 examples,
  0.055 at 4,327), source a 0.02 margin at matched size that language may explain. A raw-cosine
  attack is weak; RQ2's per-client attack has to be learned at matched init and size.
- [x] **Per-client attribution at matched init and size** (148513, 148621): 34 clients over eight
  projects. A client's update identifies its **codebase family** (OpenStack Python services align
  as closely as one project; so do Qt's C++ libraries); organization alone and language alone do
  not raise alignment. The first reading, "organization within a language", overreached and is
  corrected in the log. Late layers carry most of it.
- [x] **Organization separated from family, and it does not survive** (job 149461): with C++
  carried by AOSP (3 projects) and Qt (6), organization is attributed at 0.395 against a 0.698
  baseline, p 0.75 with the project as the unit, while content reaches 0.909 and project 0.442.
  The earlier 0.912 was the organizations' content mix. What an update betrays is the kind of code
  and the codebase, not the owner.
- [ ] **Robustness**: several initializations, longer local training (the RQ1 geometry says length
  moves updates apart), and a learned classifier on spectral features beside nearest-mean cosine.
- [x] **The aggregate attack leaks too** (`aggregate-attack.json`): membership of a source in a
  round is detectable from the aggregate alone at AUC 0.65 to 0.75 over rounds of four and eight,
  and FedAttr's paired subset difference recovers the target's direction where a stranger's gives
  nothing. Inherits the family caveat, and the rounds are one source against all others.
- [x] **Positioned against LoRA's own privacy claim** (arXiv:2409.17538, revised 2026-02): LoRA is
  inherently differentially private at the *example* level when A is frozen. RQ2 asks about the
  source, a property of the distribution rather than of any example, and runs where A is trained.
  The guarantee and the leak are about different quantities, which is the paper's opening.
- [x] **Mixed cohorts and more than one target client a round**: rounds drawn from every client,
  detection rising with the target's share and with the round size.
- [x] **A defence curve** (`defence-curve.json`): per-round Gaussian masking is a delay, not a
  defence. At sixteen times the update's own norm, detection is still 0.81 and 0.70 after 200
  rounds, because fresh masks average away and the source's direction does not. One round hides it
  (0.50 to 0.55); two hundred do not.
- [x] **Several local-training lengths** (`CLIENT_SIZE`), since longer training moves updates
  apart. It does, and how much leaks is a function of that knob rather than a property of the
  corpus. Three points, on the rebuilt corpus where the last two are measured: at 64 examples a
  client neither instrument identifies an organization beyond its own projects (0.395, p 0.75); at
  128 a detector finds AOSP at the floor of 1,716 groupings (p 0.0006, AUC 0.972, TPR 0.787) and
  not Qt (median 0.070), while the classifier still fails beyond project (0.583, p 0.30); at 256
  the detector saturates (AOSP AUC 0.995, TPR 0.985) and the classifier passes beyond project too
  (1.000 at p 0.0079, the floor of its null). A second packing at 128 confirmed the detector's rise
  and showed the nearest-class reading to be packing-dependent.
  Composition moves with length, since only the largest projects fill a client at 256, so these are
  three points on a ladder rather than a controlled doubling; composition-matched draws of the
  64-example set never reach the floor, so length survives that confound at the bottom of the
  range. The earlier reading that Qt joins AOSP at 128 came from an 84-grouping null and did not
  survive the rebuilt project set.
- [x] **Explained the non-monotonicity in noise.** Not the finite pool: it survives 77 clients.
  The detector scores a cosine, so a mask attenuates each draw by `||v||/sqrt(||v||^2+||n||^2)`,
  and the null class's difference vector is shorter than a member class's once averaging has
  removed the sampling term. The null is deflated more (0.627 against 0.844 at 200 rounds) and
  the separation widens. A per-draw model predicts every cell within 0.01
  (`scripts/masking_mechanism.py`). Below some mask size, masking is worse than nothing against
  a scale-free statistic. **Reproduced on the rebuilt corpus at 128 examples a client**
  (`defence-curve-cpp-rebuilt-c128-mid.json`): five of six rows peak at a 1x mask read as distance
  from chance, and AOSP's true-positive rate at 1% false alarms goes 0.794 to 0.885 at 50 rounds.
  Qt's detector is inverted there, which is signal once the rule is flipped. The script's default
  rounds of 1, 10 and 100 cannot show any of this, since the detector saturates by 100.
- [x] **The motivating deployment, cited** (Luo et al., arXiv:2412.01072, TOSEM): federated
  program repair over up to 100 clients with QLoRA and FedAvg, adapters uploaded to a central
  server, privacy claimed from not centralizing raw data, no threat model, DP and secure
  aggregation named but not implemented. RQ2 tests exactly that claim.
- [x] **Framed at both altitudes, and the answer is the project**: per client, an update
  identifies its codebase family; through secure aggregation, most projects are detected more
  clearly (0.63 to 0.82) than either organization containing them (0.66 to 0.75). An
  organization-level policy protects the wrong boundary in both directions: it over-promises for a
  federation whose projects are separately identifiable, and under-describes a house whose
  projects carry one hand. Whether an organization adds anything beyond its projects is what the
  AOSP cell tests.
- [x] Both threat models exist and both leak: per-client updates, and aggregates over rounds of
  varying composition.

## Plan E — calibration

Added 2026-09-16. Every null was ambiguous between "no organization-specific
adaptation" and "a blind instrument".

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
- [ ] **The gate is non-monotone in signal strength** (reproduced under fp32, job 149248:
  +0.2116 and -0.0840 against bf16's +0.2138 and -0.0861, so it is not a numerics artefact). At 0.25 the planted adapter
  over-applies the convention and loses on its own half (0.160 against 0.246), so the contrast
  there is -0.086, excluding zero on the refuting side. Between floor and ceiling the gate never
  passes. A Stage 1 validity issue: decide whether to register it as a stated limitation, or a
  metric less punishing of over-application beside exact match.
- [x] **Symmetric calibration passes** (job 148200): a 25% convention on each half gives +0.084
  [+0.049, +0.124] and +0.039 [+0.005, +0.071]. Each adapter over-applies its own marker (0.57 to
  0.67) and never the other's. The non-monotonicity is a property of a one-sided signal, which
  stays a stated limitation.
- [x] **Greedy adds to the amplification; it does not cause it** (job 148202). Sampling at T=1
  from the same adapter emits the annotation at 0.499 and 0.545 against greedy's 0.617 and 0.656,
  still about twice the 0.251 it was trained on. The over-weighting is learned, so changing the
  registered decoder would not make the gate monotone.
- [x] **A tagless submission can no longer destroy a result.** Job 148093 was submitted without
  `RUN_TAG`, so its output and adapters overwrote the first windowed run's on the cluster; that
  result survived only because it was committed. Remembering the tag is not a fix, so every job
  now takes its output through `result_path` in `cluster-env.sh`, which stops the job before the
  queue time if the file exists and takes `OVERWRITE=1` to replace one deliberately. A test holds
  all eleven sbatch scripts to it. The guard found its first live case the day it landed: the
  geometry and projection jobs named their output after `RUN_TAG` while `PATTERN` chose the
  input, so the 128-example run would have overwritten the 64-example geometry every committed
  RQ2 number rests on. Those two now name the output after the adapters they read.

### Defence evaluation: do the field's adapter splits resist source attribution?

Every personalized federated adapter design splits the adapter into a transmitted part and a part
kept local, differing only in where the cut falls. None measures whether its transmitted part
identifies its source; PFAdapter states the gap in its own words. This apparatus can rank them.

- [x] **SDFLoRA's subspace cut** (`scripts/subspace_split.py`): leaks, and the cut is what makes it
  leak. Both tests registered for the ten-project rebuild pass on 48 C++ clients over thirteen
  projects, corrected over the rank sweep against 1,716 groupings: the residual reaches 1.000 at a
  family-wise p of 0.0006, and the shared half the design transmits reaches 0.917 at 0.0012, where
  the intact update identifies nothing (0.542, p 0.39). The detector reads the shared half, AUC
  0.974 at rank 2, and runs inverted on the residual.
- [x] **PFAdapter's module-role cut** (`scripts/module_split.py`): leaks, on both corpora. The
  transmitted q and k carry AOSP at 0.702 against 0.735 for everything on the nine-project set, and
  at 0.778 against 0.842 on the rebuilt thirteen-project one; every projection type alone lands
  0.700-0.755 and 0.775-0.863. The half the design keeps local is the more identifying of the two.
- [x] **FedSA-LoRA's factor cut** (jobs 149581, 149582, 150133, 150134): leaks most of the three,
  on both corpora. A alone beats both B alone and the product on every cell, and on the rebuilt
  thirteen-project corpus it is the only reading that finds Qt at all: p 0.023 against 0.100 for
  the whole update and 0.106 for B. A is by far the more similar factor across clients, which is
  the paper's own reason for sharing it.
- [x] **SecureGate's scrubbing baseline**: already applied. Account objects and review comment
  text are pseudonymised at ingestion and every RQ2 number was measured on that corpus. The
  diff payload is deliberately not swept, so an address inside a config file reaches the corpus;
  the published artifacts are redacted separately (`scripts/redact_identities.py`, guarded by a
  test over every committed file) because a released dataset retaining contributor addresses is
  not defensible practice.
- [x] **FedDPA's adapter-instance cut** (jobs 150272 and 150904, withheld half 162578): the only
  one of the four that holds back more than it sends, and still not source-hiding. The transmitted
  global adapter puts AOSP at the floor of 1,716 groupings at every seed and catches 73% of
  two-client rounds at one false alarm in a hundred; nearest-class attribution beyond projects
  reads it at 0.750 (p 0.036), above the single adapter's 0.583 (p 0.30), because the iterative
  schedule trains the communicated adapter twice where the single-adapter run trains once. The
  withheld local adapter, same run and same number of passes, reaches **1.000 at the floor**, and
  the detector swaps organizations: AOSP is readable only in what is sent, Qt only in what is kept.
  The mechanism is the schedule, not the instance boundary. The local adapter fits what the frozen
  global one has not explained, and that residue is both what personalization is for and what an
  attacker wants, so the defence works here because the two coincide.
- [ ] **FDLoRA's schedule**, the remaining half of the instance cut: its global module is seeded
  from the average of the personalized ones and periodically overwrites them, with inner steps and
  a sync period to sweep. FedDPA's result makes the question sharper rather than answered: if what
  leaks is the residue the local adapter fits, a schedule that keeps overwriting the local adapter
  should move the leak back into what is transmitted.
- [ ] **SecureGate's learned secure adapter**, which is not the same object as a scrubber.
- [ ] **Fed-DiffLoRA's content/style cut** (IEEE TIP 2026, `# research(2026-09)`): splits each
  client's adapter into orthogonal content and style subspaces and aggregates the style half
  "while suppressing client-identifiable information". It is the only design in this family that
  states source-hiding as a property, and it states it without measuring it. Text-to-image, so
  this apparatus cannot run it directly; it is the strongest citation for why the measurement is
  missing rather than a target.
- [ ] **AS-LoRA's adaptive component selection** (arXiv:2605.05769): chooses A or B per layer and
  per round from a curvature score. Given that A alone identifies a source best here, a cut that
  moves between rounds is the case where the leak is not a fixed property of the design.
- [ ] **FedAMoLE's data-dependent architecture** is not a cut at all: the expert assignment is a
  function of the client's data and the server observes it by construction. Out of scope for the
  weight-space attacks, and worth a sentence in the paper.

## Plan D — granularity

Added 2026-09-16, from a probe the design never considered. The study fixes the organization
as the unit; the evidence says the codebase is.

- [x] **Separability probe** (`sphragis/measure/probe.py`, `scripts/separability.py`). Is
  organization decodable from review text at all? Content-controlled and compared against a
  within-organization baseline: cross-organization 0.849 [0.769, 0.867] against 0.828 and
  0.892 for two projects inside one organization. It does not beat its own baseline, so the
  classifier reads the codebase, not the organization. CPU only, runs in a minute.
- [x] **Project-level contrast** (job 148203, qt-creator vs qtbase). qt-creator's own adapter
  beats qtbase's on qt-creator's refinements by **+0.079 [+0.041, +0.118]**, about four times the
  organizational +0.021; qtbase shows none (-0.019, covering zero). Controlled for data
  difficulty by construction, since both adapters score the identical examples. qt-creator is
  also the latency outlier, so two unrelated measurements single out the same project.
- [x] **All three Qt pairs** (148377, 148378): qt-creator's advantage does not replicate against
  qtdeclarative (+0.005), qtdeclarative's does against qt-creator (+0.042 [+0.005, +0.074]), qtbase
  never wins at home. Two of six directional contrasts exclude zero: pairwise, not per project.
- [x] **Size is not the explanation** (148408): qt-creator vs qtbase at 788 per side keeps
  qt-creator's advantage (+0.044 [+0.013, +0.077]) where qt-creator vs qtdeclarative at 788 has
  none, so the pattern is the pair's. The effect halves with half the data.
- [ ] Register the probe and the project contrast as Stage 1 secondary analyses, with the
  within-organization baseline as the reading rule rather than raw accuracy.
- [x] **The two organizations differ in kind, not in noise** (exploratory, dev window): every Qt
  project above fifty examples is at or above zero, while OpenStack's projects disagree in sign and
  average to nothing. An organization is a useful boundary when its projects share conventions; a
  Gerrit host federating independent projects is not one. This is what the conjunctive gate exists
  to expose, and it agrees with RQ2's codebase-family result from the generative side.
- [ ] Say what that costs the direction in the Stage 1 text: a privacy perimeter drawn around an
  organization is not drawn where the signal lives, and RQ1's claim holds for a house rather than a
  federation.

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
  the joint probability, which is why the window is twelve test months rather than ten
  (`scripts/conjunctive_power.py`; the absolute figures are superseded by the sensitivity
  analysis).
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

- **2026-09-19** — The interval's own false-positive rate measured into an artifact (`scripts/interval_calibration.py`): 5.7% at 19 changes, 4.7% at 91, against a nominal 5%, at the accuracy the gate operates at.
- **2026-09-19** — Second packing at 128 examples a client: the detector's rise with training length replicates, nearest-class attribution does not.
- **2026-09-19** — AOSP rebuilt over ten projects, 5,130 examples, 2024-01 to 2025-08.
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
| **Estimand** | pooled (`paired_difference`) | Cluster size is non-informative here: correlation with the outcome is -0.052 and -0.019, with the per-change effect -0.025 and +0.004, so the two estimands do not answer materially different questions (Kahan et al., IJE 2023, make this the deciding test). RQ1 is a claim about refinements, so the participant-average is the matching unit. And change-averaged runs at roughly twice nominal alpha at 48-91 changes where pooled sits at nominal, which disqualifies it from binding a confirmatory gate. Both are computed and reported. **Tested on 2026-09-18**: under fp32 at three seeds, pooled returns mixed and change-averaged returns a pass on both organizations, so the choice decides the headline. It was fixed before that run existed. |
| **Pass rule** | conjunctive: both organizations' 95% intervals strictly above zero | RQ1 claims organizations have a learnable house style, which is a generality claim; a rule passing on one organization does not support it. Weakening it to raise power would change the question to fit the answer. |
| **Boundary** | strictly above zero (`>`) | Not a formality: on the unequalized pilot Qt's pooled lower bound is exactly `0.0` in 19 of 20 bootstrap seeds, so `>=` would have read that run as supporting the hypothesis. It decided a second verdict on 2026-09-18, when OpenStack's fp32 lower bound came out at exactly 0.0. |
| **Reported power** | conjunctive, not marginal | The gate passes only when both arms do, so its power is the joint probability, which for near-independent arms is the product: two arms at 80% give a gate at 64%. Section 5 had been stating two marginal figures. The figure itself is now a sensitivity analysis: the 0.833 computed at the windowed run's own effects is withdrawn, since power from a pilot's estimate is biased upward. |
| **Test window** | 2025-11 to 2026-10, twelve months | Twelve months buys more changes for 0.4 points of additional differential censoring (0.36 to 0.75), still a twelfth of what the dev window carries. The figures that chose it, 0.757 against 0.833, are superseded as absolute power (see **Reported power**); the comparison between ten months and twelve is what the choice rested on. Set before any test data exists: 2026-10 closes before the 2026-11-20 submission. |
| **Fetch horizon** | no earlier than three months after the window's final month | Makes the confirmatory contrast's low censoring a protocol guarantee rather than an accident of when acceptance landed. 2026-10 plus three is 2027-01; MSR 2027 notifies 2027-02-04, so the horizon binds only if acceptance comes early, in which case the answer is to accept more censoring rather than fetch sooner. |
| **Contamination** | Min-K%++ on the base checkpoint is primary; the time partition is corroborative only | Temporal decay is not dependable contamination evidence (Zhang et al., ACL 2026): item construction distorts it independently of the source. Identical construction across windows answers their specific confound, not the confound with ordinary distribution shift. |
| **Guided completion** | reported as a null instrument | At its floor under both criteria the literature offers: 0 of 55 hunks verbatim, and edit similarity 0.380 against 0.387, a gap of -0.007 [-0.118, +0.085]. Swapping criteria until one separates is what pre-registration exists to prevent. |
| **Contamination scored text** | the hunk with three context lines either side | Bare hunks put only about 20% of a deduplicated month over the 32-token minimum (35 of 172, 28 of 133), too few to read. With context the scored share rose to 94% and 87%, and on the six-month windows to 1,971 and 2,503 examples. The same text is what the model-less baseline was run on, so the two are directly comparable. |
| **Secondary estimate** | pooled across organizations, reported beside the gate, never binding it | The gate asks whether each organization shows the effect, which is what generality needs and what limits the resolution. The pooled estimate answers the weaker question, whether organizations show the effect on average, and is sharper for it: +0.0260 [+0.0090, +0.0440] over 697 dev changes against 0.74 and 1.28 effect-to-half-width alone. It cannot bind the gate, since one organization could carry it and two organizations cannot support a heterogeneity model. |
| **Leakage threshold** | 2% at Jaccard >= 0.7 | Must clear the measured train-into-dev rate, 1.06% for OpenStack and 0.42% for Qt. At J >= 0.8 both are 0.0000 by construction, because dedup removes pairs at that threshold across windows, so registering there is a test that cannot fail. |

**Decided by the measurements their rules named in advance.**

| decision | decided by | rule |
|---|---|---|
| **Interval the gate reads** | coverage and the seed runs | **The crossed seed x change interval.** It holds nominal where the median-seed rule reaches 0.122 two-sided, and costs no width at the measured seed effect. |
| **Seed count** | the seed main effect in RQ1's own setting | **Three**, by the rule fixed before the runs: the effect is 0.000 with a one-sided 95% upper bound of 0.0098, at or below the 0.01 where three seeds stop holding nominal. The null at 1,800 training examples gives 0.013, which is not the registered setting. |
| **Stated power** | sensitivity, not power at an observed effect | **The design detects between 1.3 and 2.4 exact-match points**, bracketing the seed effect's own uncertainty: +0.0160 and +0.0129 at its point estimate, +0.0235 and +0.0211 at its upper bound at three seeds and the seed effect's upper bound, for a conjunctive gate near 0.80 (`sensitivity-b0.0098.json`). Power from a pilot's own estimate is biased upward (Albers and Lakens 2018), so the 0.833 figure is withdrawn. Qt's dev-window effect is above its threshold and OpenStack's below, which is the mixed verdict the dev window gave. |
| **Inference numerics** | `determinism_check` across jobs and nodes | **fp32**, weights upcast exactly from bf16: it reproduces on 150 of 150 predictions across two jobs on different nodes, where bf16 differs on 5 to 7 and on up to 2 within one process. |

**A sharper RQ1 is now available, and it is AJ's call.** The dev window says the two organizations
differ in kind: Qt's projects all lean the same way, OpenStack's disagree in sign. That suggests
replacing "is an organization's house style learnable" with "what unit carries it, and what
makes an organization one". RQ2 has since measured the second half directly: AOSP behaves as a
coherent unit in update space and Qt does not, but four candidate mechanisms for that difference,
each registered before its test, were refuted (pairwise and centroid coherence, shared people,
shared code conventions). So the sharpened question is answerable and its answer is not yet
known, which is a better position for a registered report than a question whose only reachable
answer is "mixed".
The apparatus already measures both altitudes, RQ2's client updates point the same way, and the
test window is still sealed, so the question can be sharpened at no cost to pre-registration. My
recommendation is to sharpen it: the current phrasing can only return "mixed" on a federation, and
mixed is the least informative outcome the design can produce. Against it: the registered question
is the one the proposal was written around, and a reframing costs a rewrite of sections 1 and 3.

**Open, and genuinely a question about the claim rather than the statistics.** Whether RQ1
asserts "an organization's house style is learnable" (conjunctive, as registered above) or
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
| `papers` | `org-house-style/` holds the Stage 1 report. LINEAGE row P4. |

`provenance.py` is copied from phalanx-fl, not imported. Twelve lines of standard library is
the wrong thing to take a cross-repo dependency for, especially on a repo whose stated
invariant is to ride the latest Flower while this one must stay reproducible.
