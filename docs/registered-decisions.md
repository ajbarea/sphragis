---
title: Registered decisions
description: Every choice fixed before the seal opens, with the measurement that chose it.
---

# Registered decisions

Each of these is fixed before the seal opens and stated in the Stage 1 report. Every one is
recorded with the evidence that chose it, so a reader can check the choice rather than take
it. Changing one after the test window is fetched is a protocol deviation and has to be
declared as one.

They are grouped by where the decision came from. The first group holds choices argued from
the design and from prior work. The second holds choices a measurement made, under a rule
written down before that measurement existed.

## Fixed in advance

### Estimand: pooled (`paired_difference`)

Cluster size is non-informative here. The median correlation between a change's size and its
contrast is -0.026 for OpenStack and -0.006 for Qt, so the two estimands do not answer
materially different questions, which is the deciding test in Kahan et al. (IJE 2023). RQ1 is
a claim about refinements, so the participant-average is the matching unit. Pooled is also the
better-calibrated interval: under a true null its two-sided false-positive rate is 0.0575 at 45
changes and 0.0480 at 91, against the change-averaged interval's 0.0638 and 0.0555 (nominal
0.05), and change-averaged runs above pooled at every size measured.

Both are computed and reported. Fixed before any reading it could decide.

### Pass rule: every admitted organization's cell supported, Holm across H1 and H2

H1 (own half against sibling half) is read on every admitted organization, and passes only when
every one of its cells is supported, so a pass generalizes across the organizations rather than
resting on one. Requiring every cell makes it an intersection-union test, which holds its level
without adjustment across cells (Berger, Technometrics 1982). H2 (the organization beyond the
evaluated projects) is confirmatory only for Qt and Chromium, when both are admitted. The two
hypotheses share one family-wise level under Holm's step-down: both are read on a two-sided
97.5% interval, and if one passes, the other is read again on a 95% one. When H2 has no
confirmatory cell (Qt and Chromium not both admitted), H1 is the whole family and carries the whole
family-wise level: it is read on the 95% interval alone, against the bound simulated at that level.

A cell is **supported** when its lower bound is above zero, **bounded** when its upper bound is
below the cell's registered detectable effect, and **inconclusive** otherwise. A hypothesis is
bounded only when every cell is.

The admitted organizations are Gerrit hosts. H1 stays intersection-union however many are
admitted, each cell sized at 0.95^(1/k); with two to four cells a between-organization variance is
not estimable with useful precision, so heterogeneity is read beside the rule, not in it (below).
With four cells (Qt and Chromium both admitted) every cell's simulation is rerun at four cells
before the seal.

### GitHub organizations: a registered replication on a second platform (2026-10-04)

On GitHub, review before merging depends on each project's settings and is often optional, where
Gerrit's is enforced, so a GitHub cell inside H1 would let platform decide the hypothesis; and no
GitHub cell can be powered by Stage 1. The GitHub organizations are therefore a family of their
own, read after the confirmatory hypotheses. **Membership is fixed at Stage 1:** Apache, LLVM, .NET
and Grafana, in that order, each a member only if its pilot, train and development windows are
frozen and its split meets the criteria at the registered N by 2026-11-20; one not ready is
reported as not collected, and none joins later. Each member is read on H1 only, with the Gerrit
cells' windows, rules and estimand, on the two-sided 97.5% interval; its K comes from its own
development pilot and its bound from its own simulation, of that one cell at 97.5%, both fixed
before its test window is fetched. The family's reported outcome is the partial conjunction r over its members at
one-sided 0.0125, with each cell's verdict; it binds no verdict on H1 or H2.

### Boundary: strictly above zero

Not a formality. On the unequalized pilot, Qt's pooled estimate is +0.045 with a lower bound
of exactly +0.000, so an inclusive boundary would have read that run as supporting the
hypothesis. It decided a second verdict on the dev window, where OpenStack's lower bound under
the registered numerics came out at exactly +0.0000.

### Power: 0.95 for each hypothesis test

Registered-report guidelines ask for "0.95 or higher for all proposed hypothesis tests"
(Nature Registered Reports author guidelines). H1 passes only when all of its cells do, and the
organizations are independent, so its power is the product of its cells' powers: with k
admitted organizations each cell is sized at 0.95^(1/k). The figure is a sensitivity analysis
rather than a power calculation, because power computed from a pilot's own estimate is biased
upward (Albers and Lakens 2018).

### Test window: 2025-11 to 2026-10, twelve months

Twelve months buys more changes for a fraction of a point of additional differential
censoring. At the registered fetch month the twelve-month window carries 0.75 points, against
the 10.33 points the dev window carries, so the confirmatory contrast is read on far cleaner
data than any pre-registration estimate. The window closes before the submission deadline, and
it was set before any test data existed.

### Reading the test window: everything it is read under is fixed first (registered 2026-10-05)

A test-window read (`scripts/partition_pilot.py`) is read on the registered metric, the
reference's per-token log-probability (`cells.REGISTERED_METRIC`), and takes its K from the
organization's K coverage artifact (`--sizing`, written by `scripts/k_coverage.py` from its own
development pilot), its bounds from the organization's own simulation at that K
(`--sensitivity`), outcome-neutral check 5 (`--planted`), and 10,000 resamples at bootstrap seed
7, the values every development reading used; it refuses to run without any of them. Its levels,
the cell count its bound is simulated for and the spread target it is read at come from the
registration, never the command line: a Gerrit organization's from the design of the admitted
organizations, frozen by one commit before any test window (the Holm levels of its confirmatory
hypotheses, a simulation of its H1 cells), a GitHub member's from the replication family (97.5%
alone, one cell), and for both the spread K was sized on ("Stated power"). The simulation must have
been calibrated on the same development pilot K came from. A test window is read only after its
organization is frozen as admitted or as a member. So nothing the reading depends on can be
chosen, or redrawn, after its result is seen.

### Fetch horizon: no earlier than three months after the window's final month

This makes the confirmatory contrast's low censoring a protocol guarantee rather than an
accident of when acceptance landed. The horizon binds only if acceptance comes early, in which
case the answer is to accept more censoring rather than to fetch sooner.

The sealed window's size is projected from the training months' arrival rate. Where the dev window
holds more changes than that rate predicts (Wikimedia), the projection is a lower bound, which only
enlarges the simulated bounds, provided arrivals do not fall below the training rate. So at fetch
the realised change count is compared with the projection, and a window below it is reported
beside the verdict with the simulation's power at the realised size.

### Contamination: Min-K%++ on the base checkpoint is primary, the time partition corroborative only

Temporal decay is not dependable contamination evidence (Zhang et al., ACL 2026): item
construction distorts it independently of the source. Identical construction across windows
answers their specific confound, not the confound with ordinary distribution shift. The
measured gap is -0.075.

### Guided completion: reported as a null instrument

It sits at its floor under both criteria the literature offers, at a gap of +0.003. Swapping
criteria until one separates is what pre-registration exists to prevent.

### Contamination scored text: the hunk with three context lines either side

Bare hunks put only about a fifth of a deduplicated month over the 32-token minimum, too few
to read. With context the scored share rises past four fifths, and the same text is what the
model-less baseline was run on, so the two are directly comparable.

### Secondary estimate: H1 and H2 summed per organization, reported beside the verdicts

Within an organization both contrasts are bootstrapped on the same draws, so their sum, how much
an adapter from the evaluated half beats a foreign organization's, is reported with a 95%
interval. It answers a weaker question than either hypothesis and binds no verdict.

### Readings beside the pass rule, each binding no verdict (registered 2026-10-02)

From the October 2026 practice sweep (research log, 2026-10-01), each reported for every cell and
hypothesis the gate reads, on the same draws, at the same levels:

- **Meaningful.** A supported cell is also *meaningful* when its lower bound clears its metric's
  SESOI (`decomposition.metric_sesoi`: 0.0183 nats per token on the registered metric, 0.01 on
  exact match), the counterpart of `below_sesoi` (three-sided testing: Isager and Fitzgerald,
  AMPPS 2026). "Supported" says the effect is above zero; "meaningful" says it is above the
  smallest effect worth having.
- **At least r of k.** Beside the every-organization rule, the largest r for which "at least r of
  the k admitted organizations show the effect" is rejected, by the partial conjunction test in
  its Bonferroni form (Benjamini and Heller, Biometrics 2008): with each cell's one-sided
  bootstrap p-value (the share of draws at or below zero), reject for r when the r-th smallest
  times k - r + 1 is below the hypothesis's level. The organizations are disjoint corpora, so the
  cells are independent. A failed every-organization rule then still says how many show it.
- **Without AI-assisted targets.** The primary estimate recomputed without examples whose change's
  merged commit message carries an AI trailer naming a tool (`scripts/ai_trailers.py`'s rule, read
  at fetch time for the test window). Trailers are voluntary, so this bounds nothing; it shows
  whether the flagged share moves the estimate.
- **Without backports of older code.** Recomputed without examples whose (project, Change-Id) sits
  only on release, maintenance or deployment branches (`scripts/backport_share.py`'s rule), the
  traceable route by which code older than the checkpoint enters.
- **Reviewer exposure.** For the organization contrast, the per-example sibling-minus-foreign
  difference regressed on its exposure (the share of the sibling half's training examples from
  changes its reviewers reviewed, `scripts/reviewer_overlap.py`), the slope with a change-clustered
  95% interval. A positive slope excluding zero says part of the organization effect travels
  with shared reviewers rather than the organization as a whole. Reviewers are read from
  attention sets, per change, not per comment.

- **Across organizations.** A random-effects summary of every organization's H1 estimate, Gerrit
  and GitHub, each weighted by its bootstrap standard error: REML for the between-organization
  variance, the modified Hartung-Knapp-Sidik-Jonkman interval (scale held at one or above; Röver,
  Knapp and Friede 2015) from three organizations (two give the estimate and variance only), a
  prediction interval from five, and each platform's subgroup summary beside it, descriptive, with
  no moderator test, since meta-regression needs about ten studies (Cochrane Handbook 6.5,
  chapter 10).

Each is implemented before the seal opens (ROADMAP Plan H); none changes the pass rule, the
estimand or the levels.

### Comparators and audits beside the confirmatory test (registered 2026-10-04)

Exploratory, each on the same examples as H1 and scored on exact match, H1's registered
secondary, none binding a verdict:

- **Retrieval**: the base model with the k most similar training-window refinements in its
  prompt, from the own half, the sibling half and the foreign organization, at k = 1 and k = 3.
- **A rules file**: the base model with a house-rules file in its prompt, own, sibling and
  foreign: the organization's own written conventions, and a file distilled under a fixed prompt
  from each half's training-window review comments. It asks whether a declared rules file, the
  form coding agents take conventions in today, recovers what the adapters learn.
- **Exact-match misses, read by people** (Stage 2): a blind audit of 100 non-matching
  predictions per arm, judging whether each is a correct rewrite, so the secondary measure's
  blindness to correct rewrites is measured rather than assumed.
- **Renamed identifiers**: the base model's exact match with identifiers renamed against the
  original, post-cutoff against pre-cutoff, read descriptively as exposure through reused code.

### Leakage threshold: 2% at Jaccard 0.7 or above

The threshold has to clear the measured train-into-dev rate, which is 0.97% for OpenStack on corpus v3. At
Jaccard 0.8 that rate is 0.00% by construction, because dedup removes pairs at that threshold
across windows as well as within them, so registering there would be a test that cannot fail.

## Decided by the measurements their rules named in advance

### Outcome: the reference's per-token log-probability, exact match secondary (registered 2026-10-09)

Decided by outcome-neutral check 5, under rules fixed before any likelihood result was read. A
convention planted in half of each half's training refinements was found on every planted run
by the reference's mean log-probability per token, a strictly proper score, and on few by greedy
exact match, which credits only an adapter's most likely output. Its SESOI is exact match's,
carried by the adapters' gain over the base model on both (`likelihood-sesoi.json`); every
reading against a SESOI uses the metric's own (`decomposition.metric_sesoi`). The interval,
K and power figures in the sections below are measured on the registered metric; the exact-match
figures they replace are in the research log.

### Interval the gate reads: the crossed run-by-change bootstrap

Decided by the coverage study and the fixed-K simulation. A run is one admissible partition at its
own training seed; runs and changes are crossed rather than nested, so they are resampled
independently (Owen's pigeonhole bootstrap). On one partition at three seeds and a seed effect of
0.02 the crossed interval covered at 0.073 two-sided where the median-seed rule reached 0.122. Over
the registered runs, simulated on the registered metric, its one-sided false-positive rate at the
stricter level is 0.00725 to 0.01125 for OpenStack and 0.0035 to 0.01325 for Wikimedia, against a
nominal 0.0125, and at the 95% level 0.01775 to 0.02175 and 0.00725 to 0.02475, against 0.025.
Only Wikimedia's stricter rate at the pilot's 99% upper spread exceeds nominal, by less than one
Monte Carlo standard error at 4,000 null trials. OpenStack's two lowest spread points sit
at the simulation's floor, the spread partitions and change noise give with no run shift
(0.0065), so its range starts there.

### Number of runs: OpenStack K = 10, Wikimedia K = 20

Decided by the partition study, the pilot and the simulated null. H1 is the mean over K admissible
partitions, replacing five seeds on one registered partition, whose reading moved with the
partition as much as with the seed. K is the larger of two numbers, each kept within 10 to 40.
The reproducibility K is 2v(z / xi)^2, Ritzwoller and Romano's sizing rule, with v at the 90%
upper bound of the development-window pilot's per-run variance and xi the SESOI: the likelihood
pilots' per-run standard deviations at that bound, 0.0068 for OpenStack and 0.0080 for Wikimedia,
put it below the floor on both. The coverage K is the smallest K on a fixed grid (10, 12, 15, 20,
25, 30, 35, 40) at which the simulated null, at that 90% bound and 4,000 null trials, reads false
positives no more often than nominal at both Holm levels (`partitions.coverage_runs`). The rule
reads the grid in order and stops at the first K that holds. Near nominal, neighbouring K differ
by less than a Monte Carlo standard error, so the grid is not monotone: Wikimedia's K = 15 reads
0.0135 and K = 35 reads 0.013 at the stricter level against 0.0125, both recorded in the artifact,
and neither changes the K the rule takes. OpenStack's null holds at the floor and Wikimedia's
first at 20, so **OpenStack K = 10** and **Wikimedia K = 20**
(`k-coverage-<org>-likelihood.json`, the only K source a test read accepts). A sequential rule, adding runs until they agree, was simulated
first and rejected: it ran above nominal where it stopped early.

### Stated power: sensitivity, not power at an observed effect

With two cells each is sized at power 0.9747. At that power, its projected test size and K
runs, OpenStack's H1 cell detects a half-split contrast of +0.0109 and +0.0103 nats per token at
the two Holm levels and Wikimedia's +0.0071 and +0.0065, at the per-run spread K was sized on;
these are the bounds a bounded reading is judged against, the 97.5% one only when H2 is
confirmatory (Pass rule). Each is below the SESOI, so a bounded reading also excludes every
effect of interest. Wikimedia's test size is a lower bound if test-window arrivals hold at the
training rate (checked at fetch), and its bounds are then conservative. Power from a pilot's own
estimate is biased upward, so no power at an observed effect is stated.

### Inference numerics: fp32, weights upcast exactly from bf16

Decided by a determinism check across jobs and nodes. fp32 reproduces on every prediction
across two jobs on different nodes, where bf16 differs on a handful and on up to two within
one process, at no measurable cost in wall time.

## Where these live in the code

| decision | enforced by |
|---|---|
| cells, pass rule, Holm, verdicts (`h1_test_gate` on test reads), the replication family | [`sphragis/experiment/decomposition.py`](https://github.com/ajbarea/sphragis/blob/main/sphragis/experiment/decomposition.py) |
| K, the reading over partitions | [`sphragis/experiment/partitions.py`](https://github.com/ajbarea/sphragis/blob/main/sphragis/experiment/partitions.py) |
| power at a test window's realised size (Fetch horizon) | [`scripts/power_at_size.py`](https://github.com/ajbarea/sphragis/blob/main/scripts/power_at_size.py) |
| what makes a cell readable, the test-window read's resamples and seed | [`sphragis/experiment/cells.py`](https://github.com/ajbarea/sphragis/blob/main/sphragis/experiment/cells.py) |
| readings beside the pass rule, across organizations | [`sphragis/experiment/across.py`](https://github.com/ajbarea/sphragis/blob/main/sphragis/experiment/across.py) |
| estimand, interval | [`sphragis/measure/stats.py`](https://github.com/ajbarea/sphragis/blob/main/sphragis/measure/stats.py) |
| outcome-neutral checks and the halt rule | [`sphragis/experiment/neutral.py`](https://github.com/ajbarea/sphragis/blob/main/sphragis/experiment/neutral.py) |
| the seal, and refusing to fetch past it | [`sphragis/corpus/`](https://github.com/ajbarea/sphragis/tree/main/sphragis/corpus) |
| the evidence behind every row above | [Artifact index](artifacts.md) |

The dated record of how each of these was arrived at, including the readings that were
withdrawn, is the [research log](log/index.md).
