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
hypotheses share one family-wise level under Holm: the first is read on a two-sided 97.5%
interval, the second, if the first passes, on a 95% one.

A cell is **supported** when its lower bound is above zero, **bounded** when its upper bound is
below the cell's registered detectable effect, and **inconclusive** otherwise. A hypothesis is
bounded only when every cell is.

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

### Fetch horizon: no earlier than three months after the window's final month

This makes the confirmatory contrast's low censoring a protocol guarantee rather than an
accident of when acceptance landed. The horizon binds only if acceptance comes early, in which
case the answer is to accept more censoring rather than to fetch sooner.

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

### Leakage threshold: 2% at Jaccard 0.7 or above

The threshold has to clear the measured train-into-dev rate, which is 0.97% for OpenStack on corpus v3. At
Jaccard 0.8 that rate is 0.00% by construction, because dedup removes pairs at that threshold
across windows as well as within them, so registering there would be a test that cannot fail.

## Decided by the measurements their rules named in advance

### Interval the gate reads: the crossed run-by-change bootstrap

Decided by the coverage study and the fixed-K simulation. A run is one admissible partition at its
own training seed; runs and changes are crossed rather than nested, so they are resampled
independently (Owen's pigeonhole bootstrap). On one partition at three seeds and a seed effect of
0.02 the crossed interval covered at 0.073 two-sided where the median-seed rule reached 0.122; over
the registered runs its one-sided false-positive rate at the stricter level is 0.011 to 0.0125
against a nominal 0.0125.

### Number of runs: fixed from the pilot, K = 24 for OpenStack

Decided by the partition study and two simulations. H1 is the mean over K admissible partitions,
replacing five seeds on one registered partition, whose reading moved with the partition as much as
with the seed. K = 2v(z / xi)^2, Ritzwoller and Romano's sizing rule, with v at the 90% upper bound
of the development-window pilot's per-run variance and K kept within 10 to 40. OpenStack's pilot runs
give a per-run standard deviation of 0.0140, so **K = 24**. A sequential rule, adding runs until
they agree, was simulated first and rejected: it ran above nominal where it stopped early.

### Stated power: sensitivity, not power at an observed effect

With two cells each is sized at power 0.9747. At that power, its projected test size and K
runs, OpenStack's $H_1$ cell detects a half-split contrast of +0.0248 and +0.0234 exact-match
points at the two Holm levels, at the per-run spread K was sized on; these are the bounds a
bounded reading is judged against. Power from a pilot's own estimate is biased upward, so no
power at an observed effect is stated.

### Inference numerics: fp32, weights upcast exactly from bf16

Decided by a determinism check across jobs and nodes. fp32 reproduces on every prediction
across two jobs on different nodes, where bf16 differs on a handful and on up to two within
one process, at no measurable cost in wall time.

## Where these live in the code

| decision | enforced by |
|---|---|
| cells, pass rule, Holm, verdicts | [`sphragis/experiment/decomposition.py`](https://github.com/ajbarea/sphragis/blob/main/sphragis/experiment/decomposition.py) |
| K, the reading over partitions | [`sphragis/experiment/partitions.py`](https://github.com/ajbarea/sphragis/blob/main/sphragis/experiment/partitions.py) |
| estimand, interval | [`sphragis/measure/stats.py`](https://github.com/ajbarea/sphragis/blob/main/sphragis/measure/stats.py) |
| outcome-neutral checks and the halt rule | [`sphragis/experiment/neutral.py`](https://github.com/ajbarea/sphragis/blob/main/sphragis/experiment/neutral.py) |
| the seal, and refusing to fetch past it | [`sphragis/corpus/`](https://github.com/ajbarea/sphragis/tree/main/sphragis/corpus) |
| the evidence behind every row above | [Artifact index](artifacts.md) |

The dated record of how each of these was arrived at, including the readings that were
withdrawn, is the [research log](log/index.md).
