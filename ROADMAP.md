# Sphragis — Roadmap

Sphragis tests whether an organization's house style -- the tacit conventions it never wrote
down -- is learnable from the code it reviews,
and what a declared boundary protects once it can. It is the apparatus for the Federated Agents
research direction: RQ1 is a go/no-go gate, RQ2 the leakage study that follows it.

**Design of record:** RQ1's design is `docs/superpowers/specs/2026-09-22-granularity-redesign.md`, with the
repeated partitions of `2026-09-29-repeated-partitions-design.md`; the corpus harness is
`2026-09-13-gerrit-review-corpus-harness-design.md`.
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
  may therefore identify the author and no anonymized mirror is needed. The repository is public;
  the artifact link is its GitHub URL (and the Zenodo DOI once minted).
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
- [x] **`verify --reproduce`** reruns dedup and split from the refined examples and compares every
  window and the dedup counts with the manifest, since the rule digests cover build and refine
  only. Before the refreeze, plain `verify` reported Qt clean at 10,695 while the pipeline
  yielded 10,692. Both corpus v2 manifests reproduce (2026-09-23).
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
- [x] **A NoteDb route** (`sphragis/corpus/notedb.py`, `gerrit_diff.py`; `fetch --via git`). The
  review UIs of AOSP, Chromium and Qt publish `Disallow: /`; AOSP's and Chromium's git hosts allow
  fetching and carry the review record as NoteDb (Qt's git host is closed too). The route reproduces Gerrit's own diff, pseudonymises through
  the same `scrub`, obeys the same seal, and paces each host at its crawl delay. Checked against
  the AOSP REST corpus on every branch: the refined examples match but for one pseudonym from the
  older scrub, and every revision's kind agrees (research log, NoteDb entry, rerun 2026-09-25).
- [ ] **AJ's call: changes withdrawn after collection.** Changes a host now answers 404 for
  (deleted or made private after the snapshot listed them) are counted `comment_error` and never
  built. Their metadata stays in the raw snapshots. Open: purge it from raw snapshots and any
  release, or keep raw internal for reproducibility and exclude it from every release. Ground the
  choice in MSR ethics guidance (Gold and Krinke, "Ethics in the mining of software repositories", EMSE 27, 2022, doi:10.1007/s10664-021-10057-7) before deciding.
  The same rewrite would also re-scrub the raw snapshots' Gerrit account numbers (next item), so
  the two are one decision about rewriting raw snapshots.
- [ ] **With the next build-rules change: count a 404 as `change_gone`**, apart from
  `comment_error`, which counts it today (`build_from_change` drops any failed comments fetch under
  `comment_error`). The patch is not committed and has to be written again; any `build.py` edit
  stales every frozen corpus month.
- [ ] **With the next build-rules change: scrub Gerrit account numbers that are not `_account_id`
  values** (`scrub.py` is in `BUILD_SOURCES`). Attention-set entries are keyed by the raw account
  number and their `reason` text carries `<GERRIT_ACCOUNT_n>`; `scrub` replaces `_account_id`
  values and addresses only. Every refined and built example on disk holds none (checked
  2026-10-01, all four Gerrit corpora); the raw snapshots, untracked and never released, do.
- [ ] **GitHub organizations as a registered replication** (decided 2026-10-04: not H1 candidates;
  read as their own family, binding no verdict; research log). Collect Apache, LLVM, .NET, Grafana
  in that order (pilot, train, dev windows); members are those frozen and split-checked by
  2026-11-20, named in Stage 1. Then per member: admissible list, development pilot for K,
  sensitivity simulation, all before its test window is fetched. The family is read by
  `replication_gate()` beside `design()` (2026-10-05), once the freeze commit sets
  `REPLICATION_MEMBERS`. Sized 2026-10-01
  (`github-sizing-report.json`): Apache and LLVM are of H1-cell size, .NET and Grafana of RQ2 size; OpenJDK and HashiCorp borderline. Needs: a GitHub
  collection route (threads to hunk, comment, next revision; suggestion blocks and bot or AI authors
  removed; pseudonymised at ingestion); GitHub's own thread-to-example conversion from a built pilot
  month per organization (`scripts/github_conversion.py`; OpenJDK 2024-11 first, the rest borrow
  its span until theirs is built); LLVM's projects defined as its top-level directories before
  its split is read; open access for every resulting publication (GitHub's terms: EMSE's open-access
  option for Stage 2, plus arXiv); Stage 1's replication-family paragraph.
- [ ] Chromium through the NoteDb route, once Chromium's infra-dev list answers the permission
  request, with its projects scoped by the component mapping stage.
- [ ] `scripts/censoring.py` for a git-fetched organization: selection there is creation to
  merge, not creation to last update.
- [x] State the dev window's censoring wherever dev numbers appear; they are pre-registration
  estimates, not unbiased previews. Stated with the three-seed RQ1 result: 39.1% and 28.8% of the
  dev window's changes were still open at collection, against 1.1% and 0.7% in the test window.
- [x] **Chromium approved as a third organization and scoped** (2026-09-22): a C++ organization
  with several projects, the pairing for Qt that controls language. In `GERRIT`; fetch now refuses
  a query the host truncated. Selection rule, feasibility and split outcome are in the research log.
- [ ] **Collect Chromium** over train and dev with `scripts/fetch_chromium.sh`, frozen by
  2026-10-23. Blocked on the cut of chromium/src below the project (no project-level set meets the
  split criteria), on the permission bulk Chromium collection waits for (the terms permit the git
  host, research log 2026-10-08), and then on sub-month fetching for chromium/src.

## Plan B — measurement

- [x] `score` — exact match binds the pass rule; normalized EM and edit similarity reported.
- [x] `stats` — pairs cluster bootstrap over changes; `gate_verdict` is the pass rule as code
  (superseded 2026-09-22 by `decomposition.h1_test_gate`).
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

## Plan H — hardening against October 2026 practice

Decided 2026-10-01 (AJ: "you decide", highest quality) from a five-part literature sweep, every
source opened and recorded in the research log entry of that date. Ordered by what must land
before the test window is fetched; items that change a registered decision say so.

Before Stage 1 (2026-11-20), each registered before the seal:

- [ ] **Contamination: provenance primary, Min-K%++ descriptive** (changes a registered decision).
  Membership inference barely beats chance on LLMs (Duan et al., COLM 2024, arXiv:2402.07841)
  and the battery's model-less baseline beats Min-K%++ here; protection rests on provenance.
  Score only target tokens given context; Min-K%++ is read as exposure only above the blind
  baseline.
- [ ] **Pre-cutoff code through cherry-picks and backports** (the bounded dev-window share landed
  2026-10-06: `backport_share.py`, `backport-share.json`, read beside H1 in
  `partition-pilot-*-sensitivities.json`; the lookup against the checkpoint's branches remains): look each target's added lines up
  in every branch as it stood at the checkpoint's weight upload, report the rate and a
  sensitivity estimate without them (infini-gram mini, arXiv:2506.12229, as exact-lookup practice).
- [ ] **Pin the checkpoint revisions** (pins done: `model.MODEL_REVISIONS`, every load pinned; still to do: the checkpoint date and the disclosure table below; the Qwen repos changed config and
  tokenizer files on 2024-11-18) to the revision the pilots ran on, and cite the weight-upload
  commit as the checkpoint date; adopt a contamination disclosure table (arXiv:2608.29463).
- [ ] **AI-assisted pull requests on the GitHub family**: the route drops accounts typed `Bot` and
  the logins in `github.AGENT_LOGINS`, which misses agents that commit under a developer's account
  (an agent `Co-Authored-By:` trailer, Cursor's `cursoragent` committer). The raw rows carry no
  commit messages, so, as `ai_trailers.py` does on Gerrit, read each member's trailers and
  committers after collection and report its H1 cell without those changes as a sensitivity;
  `GITHUB_RULES` and the frozen months stay as they are. Stage 1 states detection at the login
  level only.
- [ ] **AI-assisted targets** (development windows landed 2026-10-06: `ai_trailers.py`,
  `ai-trailers.json`, read beside H1 in `partition-pilot-*-sensitivities.json`; the test window's
  listing follows the unsealing, and the GitHub pilot months remain): flag an example whose successor commit carries `Assisted-by`,
  `Generated-by` or an agent `Co-authored-by` trailer (OpenInfra's AI policy requires them) or an
  agent committer; report the share per organization, window and half, and an estimate without
  them (a lower bound, trailers being voluntary). Measured first on the GitHub pilot months.
- [ ] **Reviewers, not the organization**: a secondary analysis splitting dev and test examples
  by whether their reviewer also reviewed the sibling half's training data (salted pseudonyms).
- [x] **A retrieval comparator**: the base model with BM25 top-k examples from the own-half,
  sibling-half and foreign pools, mirroring H1 without training (Pornprasit and
  Tantithamthavorn, IST 2024; retrieval beat fine-tuning in arXiv:2505.15179, lost in
  arXiv:2606.06492). Exploratory. Reported at k = 1 and k = 3: a single most similar past review
  works best for review generation and more retrieval hurts (RARe, arXiv:2511.05302).
  Read on the development window 2026-10-06 (research log): every own-minus-sibling reading
  inconclusive, as are the adapters' over the same partitions.
- [x] **A rules-file comparator** (added 2026-10-04): the base model with a house-rules file in
  context, mirrored own, sibling and foreign: (a) the organization's own written conventions
  (OpenStack's HACKING guide, MediaWiki's coding conventions), (b) an AGENTS.md-style file an LLM
  (Qwen3.6-27B, pinned) distils from each half's training-window review comments, under a fixed
  prompt and a fixed grounding check. Same examples,
  exact match and own-minus-sibling contrast as H1; exploratory. Coding agents take conventions
  from declared rules files and memory, and rules files are measured on task success and
  instruction following, not on whether code follows a team's conventions (Gloaguen et al.,
  arXiv:2602.11988v3). If a rules file recovers the own-half advantage, adapters are not
  needed for it; if not, that is the answer to "why train weights".
  Read on the development window 2026-10-07 (research log): distilled own minus sibling carries
  no contrast as large as the SESOI on either organization; the adapters' stays inconclusive.
- [ ] **Exact-match misses, read by people** (Stage 2, exploratory): a blind human audit of 100
  non-matching predictions per arm (own, sibling, foreign), judging whether each is a correct
  rewrite. About one exact-match failure in six is a correct rewrite (15.7% and 17.4% of
  non-matching predictions, Tufano et al., TSE 2024), and if that share differs by arm the
  contrast is not conservative; the audit measures it.
- [ ] **Renamed identifiers, descriptive**: the base model's exact match on held-out hunks with
  identifiers renamed against the originals, post-cutoff against pre-cutoff, so code reused
  from older repositories (which a time split cannot see; SrDetection, arXiv:2606.29815) shows
  as a gap. Uses the identifier anonymisation of the tacit item below.
- [ ] **A likelihood outcome**: teacher-forced bits-per-byte of the target, own vs sibling, as a
  registered secondary with its own SESOI and sensitivity simulation (arXiv:2508.13144).
- [ ] **Readings beside the pass rule** (registered in `docs/registered-decisions.md` 2026-10-02,
  with the AI-trailer, backport and reviewer-exposure sensitivities). Implemented 2026-10-04 in
  `sphragis/experiment/across.py` and the H1 cell: meaningful, at least r of k, the random-effects
  summary. The AI-trailer and backport sensitivities run on the development window 2026-10-06
  (`sensitivity_ids.py`, `partition_pilot.py --without`; both gates require them); their test-window
  listing and trailer searches follow the unsealing at acceptance. Reviewer exposure, on the
  organization contrast, waits for the H2 read. "meaningful" when a cell's lower bound clears the SESOI
  (three-sided testing, Isager and Fitzgerald, AMPPS 2026); the largest r with "at least r of k
  organizations" rejected (partial conjunction, Benjamini and Heller 2008), so a failed
  every-organization test still says how many show the effect; and a random-effects summary over
  every organization read (REML, HKSJ interval, prediction interval from five, platform
  subgroups descriptive, no moderator test). The intersection-union rule stays the pass rule
  (decided 2026-10-04).
- [ ] **Controls**: a helper-substitution plant (a call rewritten into the house helper) piloted
  like the marker plant; a report-only plant near the bound (+0.02 to +0.03) read against the
  simulated power; the placebo rerun once on the repeated-partition pipeline.
- [ ] **Tacit, measured**: H1 on the refinements each organization's own linters would not make
  (flake8/hacking, mediawiki-codesniffer, eslint-config-wikimedia), and exact match after
  identifier anonymisation, both supplementary estimands like `file_type_supplement`.
- [ ] **The rank-256 arm** (changes a registered arm): alpha held at 64, or rsLoRA, so the arm
  changes capacity and not step size too (LoRA Without Regret, Thinking Machines 2025); a one-seed
  learning-rate check at 1e-4 to 1e-3, chosen by pooled held-out loss, logged even if 2e-4 stands.
- [ ] **Writing**: open-source communities stand in for companies and their public conventions
  make the measured effect a lower bound; familiarity with old repository code raises base
  scores and cancels in the contrasts; literature comparisons use normalized exact match (as
  CodeReviewer's evaluation code does); a descriptive specification curve (Cassee and Feldt,
  arXiv:2512.08910); ADEMP for the simulations; the 2026 LLM-in-SE guidelines checklist
  (arXiv:2508.15503); background on industrial comment resolution and retrieval vs fine-tuning.
- [x] **An open-weight label-audit rater** at pinned weights beside the two API raters (2026-10-02,
  `label-audit-v2-open-rater-labels.json`).

From the lab meeting of 2026-10-02 (Dr. Reznik's questions), carried as directions, not Stage 1 work:

- [ ] **A checking agent ("agent control").** The label audit's grades (a revision answers the
  comment, partly, not at all, or needs context) are the judgment an agent checking a coding
  agent's change would make before a human sees it. The audited sample is the seed of a
  training and test set for such a checker; it would be scored against human labels, as the
  audit scores its model raters. Its place is the Federated Agents line after P4, where a
  checker can also sit at the shared/private boundary.
- [ ] **Beyond code review.** Collection, pseudonymisation and the sealed time split are host
  routes and apply to any review system; pairing and the cleaning rules are code-review specific.
  Stated in the deck's backup and here, not built.
- [ ] **Talks at 10 to 15 minutes**, built from the one deck (`papers/federated-agents-deck`).

RQ2, after Stage 1:

- [ ] **A harm model for known participants**: in cross-silo federation members are named, so
  the headline is what leaks about a known member's content and properties; the min-loss
  record-to-organization attack (Hu et al.) becomes a result, property inference is cited and
  scored in n_leaked (Suri and Evans, PETS 2022).
- [ ] **A canary audit**: canary clients and source canaries (the planted-convention machinery)
  for an empirical epsilon per split at the record and organization units (Andrew et al., ICLR
  2024, arXiv:2302.03098; Steinke et al., NeurIPS 2023).
- [ ] **A DP arm** with accounting on the shared half, at both units (FedASK, arXiv:2507.09990;
  user-level DP, arXiv:2406.14322).
- [ ] **A curious peer and the final model**: extraction and attribution from the global model
  alone (arXiv:2506.06060), and a record-level LoRA-Leak baseline on each half.
- [ ] **The provenance-tag split tested adversarially** against the learned splits, and FedRoRA
  (arXiv:2609.00632) and FedLAFP (arXiv:2609.37033) placed; the harness released as the first
  source-leakage benchmark for federated LLM fine-tuning.

Considered and not adopted: CodeBLEU and embedding metrics (surface-biased, arXiv:2509.15397),
LoRA variants (within 1-2% once the learning rate is tuned, arXiv:2602.04998), e-values (K is
fixed), FSD and dataset inference (no IID non-members). A human audit of exact-match misses and a
second human label rater wait on rater time.

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

- [x] **Source inference is RQ2's attack family**, read in full 2026-09-30. Hu et al., "Source
  Inference Attacks: Beyond Membership Inference Attacks in Federated Learning" (IEEE TDSC
  21(4):3012-3029, 2024, doi 10.1109/TDSC.2023.3321565): an honest-but-curious server attributes a
  known training record to the client whose local model has the smallest prediction loss on it,
  in FedSGD, FedAvg and FedMD, ten clients, differential privacy as the defence. Every client there
  transmits a whole model, gradient or prediction. RQ2 asks the question of an organization rather
  than a record, and of the part of an update a defence transmits, which their setting does not
  cover.
- [ ] **Test the claim that aggregation stops source inference** (added 2026-10-04).
  Athanasiou, Jung and Palamidessi (ICLR 2026, arXiv:2603.02017) prove that once only the
  per-round sum is released, source inference falls to random guessing; their appendix shows
  client subsampling restores the attack against undefended training, and the defence is not
  tested under subsampling. Run Hu et al.'s min-loss attack through the paired subset difference
  over rounds of varying membership, beside RQ2's aggregate results, and state, as our reading,
  the assumption the result relies on (the server does not learn who took part).
- [ ] **User inference as the final-model baseline** (Kandpal et al., EMNLP 2024,
  arXiv:2310.09266): their likelihood-ratio statistic at the project and organization unit on the
  final model, so the paper says how much more the transmitted half reveals than the model does.
- [ ] **Stronger attackers**: a learned GL-invariant attacker on LoRA weights (Putterman and Lim,
  arXiv:2410.04207) and unsupervised spectral clustering of updates (Xu, Cohn and Ohrimenko,
  ECAI 2023, arXiv:2310.05960), across initializations. FedRand (arXiv:2503.07216) does attack
  its own shared half, at the record level: claims say no split design measures *source*, not
  that none measures leakage. Cross-organization code clones (Chen and Jiang, ASE 2024,
  arXiv:2409.12020) are checked as a confound.
- [ ] **Their min-loss rule as RQ2's baseline attack**: attribute a held-out refinement to the
  organization whose transmitted part, completed with the receiver's own local part, gives the
  smallest loss. It needs no trained attacker, so the learned attack is reported against it.
- [ ] **Two 2026 LoRA methods to place, neither a new cut.** FedAS-LoRA (arXiv:2608.09742) picks
  share-A or share-B per deployment by a rank-aware residual, so it transmits one of the two cuts
  already measured, chosen by rule; LA-LoRA (ICLR 2026, arXiv:2602.19926) is a differentially private
  method, a different defence class from the adapter splits.
- [ ] **A second model family, exploratory.** Every neighbouring federated SE study runs several
  models (the federated program-repair study six, arXiv:2412.01072); P4 runs Qwen2.5-Coder-7B.
  An H1 dev-window replication on Llama-3.1-8B-Instruct (Meta; data to December 2023, released
  2024-07-23), outside the registered cells, so the frozen model stack does not change; it meets
  the registered model's rule that the corpus postdates the checkpoint, which Qwen3-8B (no stated
  cutoff, self-reported early 2025) does not (research log, 2026-10-01). Needs the gated
  checkpoint's licence accepted on the Hugging Face account.
- [ ] **RQ2 on QLoRA adapters, exploratory** (decided 2026-10-04). The deployment RQ2 tests
  (Luo et al., arXiv:2412.01072) trains QLoRA: a 4-bit NF4 base with bf16 adapters (Dettmers et
  al., NeurIPS 2023). RQ2's attribution and defence-curve measurements are rerun on adapters
  trained that way, at the registered rank and steps, so the finding is shown on the setup it
  critiques; whether a quantized base changes what an update reveals is unmeasured for adapters
  (the nearest evidence is post-training quantization of image models, arXiv:2512.15335). Fits
  SPORC's 40 GB A100s. Not for H1: its registered numerics, pilots and simulations are bf16, and
  QLoRA's best reported case is parity, so it would add noise, not quality.

- [ ] **The source claim is narrowed to what is ours** (October 2026 sweep, papers intake). An
  anonymous ICLR 2027 submission (OpenReview u82w02YBmy) names the document a released LoRA
  adapter was trained on from its weights, and still tells apart adapter pairs that share data
  when each holds many documents. RQ2 claims source at the organization unit, from the transmitted
  half of a split, in federated training; never that adapter weights reveal their source in
  general. Cited once it is public under its authors' names.
- [ ] **Organization identity separated from distribution shift** (October 2026 sweep). A second
  anonymous submission (OpenReview wxEvaxbxFl) shows a post-hoc privacy audit can measure the
  shift between sources rather than leakage; the adapter-to-document paper concedes the same
  confound, and the weight-space note below already finds source a small margin that language may
  explain. RQ2 attributes between organizations matched on language (Qt and Chromium in C++, and
  within one language elsewhere), and reports beside each attack the same attack between
  adapters trained on two disjoint draws from one organization, so what shift alone gives is
  measured, not assumed.
- [ ] **The owner-declared boundary, worded as ours** (October 2026 sweep). Owner-approved sharing
  of agent memory already exists (SAP's deployed shared organizational memory,
  dhanyamraju2026shared; two anonymous submissions on approval-gated and audited memory writes).
  The claim is the boundary declared per example, by provenance, before training.

- [ ] **RQ2 is rebuilt on permitted hosts before anything is published** (decided 2026-09-29,
  research log). Every RQ2 number so far rests on AOSP and Qt corpora fetched over REST before the
  robots.txt stop; they stay as internal pilots. The publishable corpora are OpenStack and Wikimedia
  among the Gerrit hosts (LineageOS is too small for a cell, 2026-09-30) and the GitHub
  organizations (2026-10-04); Qt only with permission. AOSP's git host is permitted (terms re-read
  2026-10-08), so AOSP may enter again through the NoteDb route once its data audit shows how
  Android's 2025 move to internal development changed its public review stream in the windows.
- [ ] **Optional: a written "not human subjects research" determination** from hsro@rit.edu, to
  cite in the ethics section. The study contacts no one and reads only publicly posted reviews,
  so it is not human-subjects research under 45 CFR 46.102(e) (research log, 2026-10-08).
- [ ] **GitHub's written confirmation** that its Acceptable Use Policies permit research on public
  pull-request review threads, which its Privacy Statement counts as personal data. Requested
  2026-10-10 by email to GitHub's privacy team (its privacy form routes other questions there),
  awaiting a reply. Collection continues; the GitHub organizations stay out of every published figure until
  GitHub answers, and a refusal is met by deleting what was collected.
- [ ] **Removal on request.** `docs/protocol.md` promises it, and GitHub's Acceptable Use Policies
  (section 8) require it: a script that looks up the account's numeric id, computes the pseudonyms
  the scrub writes for it (the salted id, and on GitHub the salted login its @-mentions carry), and
  drops that author's comments and examples from the corpus, its manifests and the next release.

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
  now takes its output through `claim_result` in `cluster-env.sh`, which stops the job before the
  queue time if the file exists and takes `OVERWRITE=1` to replace one deliberately. A test holds
  every sbatch script to it. The guard found its first live case the day it landed: the
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
  should move the leak back into what is transmitted. Implemented (`scripts/fdlora_schedule.py`).
  Reviews before the first job ran found and fixed a round-0 average scoped per source, a default
  schedule whose withheld half equalled the final upload, a mislabelling shape guard and a corpus
  read that bypassed the loader; job 183579 was cancelled before it ran. Read by its pseudocode,
  the personalized module is always a past transmission (`docs/research-log.md`, 2026-09-24), so
  the measurement reports what `-local` equals rather than treating it as withheld. It runs once
  the v2 client corpora are recut.
- [ ] **SecureGate's learned secure adapter**, which is not the same object as a scrubber.
- [ ] **Fed-DiffLoRA's content/style cut** (IEEE TIP 2026, `# research(2026-09)`): splits each
  client's adapter into orthogonal content and style subspaces and aggregates the style half
  "while suppressing client-identifiable information". It is the only design in this family that
  states source-hiding as a property, and it states it without measuring it. Text-to-image, so
  this apparatus cannot run it directly; it is the strongest citation for why the measurement is
  missing rather than a target.
- [ ] **FedSAIL's action-subspace cut** (Du, He, Feng, arXiv:2609.32485, posted 2026-09-26): shares a
  subspace of each client's update weighted by the second moments of its layer inputs, and keeps
  client-specific coefficients local. What it transmits is built from input statistics, the most
  direct trace of a client's data of any cut here. It also reports that the similarity of
  LoRA factors across clients is largely an artefact of common initialization; RQ2's clients share
  initialization as federated clients do, so FedSA-LoRA's A-factor result stands for that setting,
  and the robustness item above (several initializations) is where independent initialization is
  read.
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
- [x] ~~Register the probe and the project contrast as Stage 1 secondary analyses~~: superseded by
  the re-registration below, which makes the project boundary a hypothesis rather than a secondary.
- [x] **The two organizations differ in kind, not in noise** (exploratory, dev window): every Qt
  project above fifty examples is at or above zero, while OpenStack's projects disagree in sign and
  average to nothing. An organization is a useful boundary when its projects share conventions; a
  Gerrit host federating independent projects is not one. This is what the conjunctive gate exists
  to expose, and it agrees with RQ2's codebase-family result from the generative side.
- [x] Say what that costs the direction in the Stage 1 text: the introduction now states the
  premise as a boundary a deployment can draw, and the readings table names the perimeter drawn in
  the wrong place as one pre-committed outcome.
- [x] **RQ1 re-registered around granularity** (AJ, 2026-09-22; research log). The decomposition
  into a half-split contrast (H1) and the organization beyond the evaluated projects (H2) replaces
  the organization-only hypothesis. Spec `docs/superpowers/specs/2026-09-22-granularity-redesign.md`,
  gate `sphragis/experiment/decomposition.py`. The rank branch is amended in the same change.
- [x] Cells as a rule over the organizations admitted by host permission, not two fixed designs
  (2026-09-27): H1 on every admitted organization, H2 on Qt and Chromium only when both are.
- [x] Wikimedia's split meets the criteria on its frozen v3 corpus and it is admitted at N = 1,850
  (research log, 2026-09-30).
- [x] Corpus v3: examples whose target the reviewer wrote (Gerrit suggested edits, applied fixes)
  removed by `refine`; OpenStack's ceiling and floor refixed on v3, 0.3187 and 2,004 (research
  log, 2026-09-28).
- [x] OpenStack on corpus v3 at the half size: seed effect 0.0077 (five seeds stand), detectable
  effects +0.0246 and +0.0210, H1 dev pilot +0.0284 [+0.0028, +0.0532] on a partition that moved
  117 of 238 projects from v2's; five seeds kept as a stated deviation (research log, 2026-09-28).
- [x] **Partition variance measured** (2026-09-29): sigma_partition 0.0128, 90% about [0.0058, 0.035]
  and partly change-sampling noise, comparable to the seed effect; five partitions average +0.0041
  [-0.015, +0.023] at two seeds.
- [x] **H1 read over several partitions** (repeated splitting: Chernozhukov, Demirer, Duflo and
  Fernández-Val, Econometrica 93(4):1121-1164, 2025), AJ's decision (research log, 2026-09-29);
  #67.
- [x] `scripts/split_criteria.py` and the language-mix ceiling fixed on OpenStack's halves: total
  variation 0.357 over file suffixes, size floor 2,158 (research log, 2026-09-27).
- [x] H1 carries a supplementary estimand on the suffix both halves hold most of
  (`file_type_supplement`, `matched_suffix`; `.py` for OpenStack), reported beside H1 (2026-09-27).
- [ ] **Zenodo, before Stage 1 goes out (AJ asked to be reminded).** The Zenodo GitHub integration is on for
  this repo (webhook verified 2026-10-02, AJ's ORCID linked): publish GitHub release `v0.1.0` from the
  commit the Stage 1 report describes, read the minted DOI off Zenodo, and cite it in the report's
  data and code availability statement. A Zenodo record is permanent, so the release is cut once,
  from the final commit. The dataset gets its own record later, after the licence per host, the
  withdrawn-changes decision and a takedown contact are settled; the test months never before Stage 2.
- [ ] **Every artifact's provenance commit reachable from a clone**, checked before the Zenodo
  release: squash merges leave the commit a result was produced at only in GitHub's pull-request
  refs, so a clone of `main` cannot check it out. A script lists each committed artifact's
  `provenance.git.commit` and tags any not on `main` (`sim/openstack-sporc-21794711` and
  `sim/wikimedia-sporc-21794712` were tagged by hand, 2026-10-04), with a test that fails on one
  reachable from neither.
- [ ] Qt admitted if permission arrives before 2026-11-20; Chromium if it arrives in time for its
  corpus to be frozen by 2026-10-23; otherwise each is registered as not collected.
- [x] The seed effect at the half size: above 0.01 on both placebos, so five seeds; on corpus v3
  0.0077 with an upper bound of 0.0376, five kept as a stated deviation from the rule's wording.
- [x] The crossed interval's coverage at 97.5% in both regimes (2026-09-23), and the sensitivity
  analysis at the half size on corpus v2 for OpenStack (2026-09-27).
- [x] The sensitivity analysis for every other admitted organization (Wikimedia's:
  `partition-sensitivity-wikimedia.json`).
- [x] Sibling-half leakage on the dev window: below the registered threshold in every half.
- [ ] The C++-restricted estimand beside H2.
- [x] OpenStack on corpus v2 at the half size: seed effect 0.0116 at five seeds, detectable
  effects +0.0266 and +0.0250, H1 dev pilot +0.0134 [-0.0091, +0.0350], inconclusive (research
  log, 2026-09-27).
- [x] The decomposition's pilot on the dev window with Wikimedia: K = 16 (research log, 2026-10-01).

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

## Completed

- **2026-10-07** — The contamination battery read against blind baselines: a bag of words separates the pilot windows better than any membership score, so the battery's gap cannot be read as exposure; proposed for Stage 1 as the battery's reading rule (research log).
- **2026-10-07** — The rules-file comparator read on the development window: 22 distillations (Qwen3.6-27B) and 22 `ARMS=rules` evaluations at 05465f1; distilled own minus sibling carries no contrast as large as the SESOI on either organization (research log).
- **2026-10-07** — A skip fails the suite unless the GPU stack or a GPU is absent; numpy joins `dev`, so the five script-test modules that CI had been skipping run there.
- **2026-09-28** — Corpus v3: `refine` removes examples carrying a Gerrit suggested edit or "Fix applied." reply, whose target is already in the prompt; OpenStack and Qt refrozen, OpenStack's split criteria refixed on v3 (ceiling 0.3187, floor 2,004), and examples whose target a reviewer typed into a comment reported per half (research log).
- **2026-09-29** — LineageOS added as a candidate organization, fetched under its robots.txt; the candidate order is Wikimedia, LineageOS, then Qt and Chromium on permission, and the Linux Foundation hosts are too small (research log).
- **2026-09-28** — The model stack frozen against Dependabot version updates for the study; security updates still open (#62).
- **2026-09-27** — H1's file-type-matched supplementary estimand registered and implemented (`file_type_supplement`), sharing its bootstrap with the C++ supplement (research log).
- **2026-09-27** — The split criteria as code (`sphragis/corpus/halves.py`, `scripts/split_criteria.py`), shared with the placebo builder; the language-mix ceiling fixed on OpenStack at total variation 0.357, whose halves split docs from code (research log).
- **2026-09-27** — OpenStack's placebo on corpus v2 at the half size, five seeds on TIGRIS: seed effect 0.0116, detectable effects +0.0266 and +0.0250, and the registered gate's H1 dev-window pilot +0.0134 [-0.0091, +0.0350], inconclusive (research log).
- **2026-09-27** — The confirmatory cells are a rule over the organizations admitted by host permission (`design()`): H1 on every admitted organization, H2 on Qt and Chromium only when both are; the gate takes the admitted set (research log).
- **2026-09-26** — Wikimedia added as an organization, fetched under its robot policy: the transport refuses robots.txt paths, pauses 15 minutes after a 5xx and admits one process at a time; train and dev collection running (research log).
- **2026-09-26** — Confirmatory design rests only on hosts permitting automated access: OpenStack, and Wikimedia as a candidate for Qt's place; Qt and Chromium admitted only with permission before Stage 1 (research log).
- **2026-09-26** — The human check of rater A stops at 152 items, registered before its figures, with first-half/second-half and human-to-rater-B readings added; read after: below the model pair item by item, a consistent valid share, no detectable rise toward rater A (research log).
- **2026-09-23** — `verify --reproduce`: dedup and split rerun from the refined examples and compared byte for byte with every frozen window; both corpus v2 manifests reproduce (#42).
- **2026-09-23** — Label audit agreement read with Gwet's AC1 and specific agreement beside kappa: the kappa of 0.557 is the prevalence paradox, AC1 0.853 (#41).
- **2026-09-23** — Chromium scoped as an organization; fetch refuses a truncated query and a listing that moves while it is paged (#35).
- **2026-09-23** — Stage 1 data audit: lint-bot comments (in Qt only) and one-click "Acknowledged" removed by a `refine` stage that alone applies label rules; the rebase-only-successor finding retracted; build and label rules recorded per month and checked by the loader; corpus v2 refrozen and every result it feeds regenerated at one commit (research log).
- **2026-09-23** — AOSP audited the same way: no bot comments, every successor a rework.
- **2026-09-23** — Label audit v2: 384 examples, two blind model raters under a two-question rubric, and a page for a human's blind check of one (`scripts/label_audit_*`).
- **2026-09-22** — RQ1 re-registered around granularity, Chromium added, the rank branch amended (spec and gate as code).

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

Every registered decision, with the evidence that chose it, is on the docs site:
[`docs/registered-decisions.md`](docs/registered-decisions.md).

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

`provenance.py` is copied from phalanx-fl, not imported. A short standard-library module is
the wrong thing to take a cross-repo dependency for, especially on a repo whose stated
invariant is to ride the latest Flower while this one must stay reproducible.
