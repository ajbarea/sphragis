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
a claim about refinements, so the participant-average is the matching unit. Change-averaged
also runs at roughly twice nominal alpha at the cluster counts the gate operates at, where
pooled sits at nominal, which disqualifies it from binding a confirmatory gate.

Both are computed and reported. **The choice decided the headline.** Under the registered
numerics at three seeds, pooled returns mixed while change-averaged returns `pass` on both
organizations, at +0.0380 for OpenStack and +0.0334 for Qt. It was fixed before that run
existed.

### Pass rule: conjunctive, both organizations' 95% intervals strictly above zero

RQ1 claims that organizations have a learnable house style, which is a generality claim. A
rule passing on one organization does not support it, and weakening the rule to raise power
would change the question to fit the answer.

### Boundary: strictly above zero

Not a formality. On the unequalized pilot, Qt's pooled estimate is +0.045 with a lower bound
of exactly +0.000, so an inclusive boundary would have read that run as supporting the
hypothesis. It decided a second verdict on the dev window, where OpenStack's lower bound under
the registered numerics came out at exactly +0.0000.

### Reported power: conjunctive, not marginal

The gate passes only when both arms do, so its power is the joint probability, which for
near-independent arms is the product: two arms at 80% give a gate at 64%. The figure itself is
a sensitivity analysis rather than a power calculation, because power computed from a pilot's
own estimate is biased upward (Albers and Lakens 2018).

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

### Secondary estimate: pooled across organizations, reported beside the gate, never binding it

The gate asks whether *each* organization shows the effect, which is what generality needs and
what limits the resolution. The pooled estimate answers the weaker question, whether
organizations show the effect on average, and is sharper for it. It cannot bind the gate,
since one organization could carry it and two organizations cannot support a heterogeneity
model.

### Leakage threshold: 2% at Jaccard 0.7 or above

The threshold has to clear the measured train-into-dev rate, which is 1.06% for OpenStack. At
Jaccard 0.8 that rate is 0.00% by construction, because dedup removes pairs at that threshold
across windows as well as within them, so registering there would be a test that cannot fail.

## Decided by the measurements their rules named in advance

### Interval the gate reads: the crossed seed by change bootstrap

Decided by the coverage study and the seed runs. Seeds and changes are crossed rather than
nested, so they are resampled independently (Owen's pigeonhole bootstrap). At three seeds and
a seed effect of 0.02 the crossed interval covers at 0.073 two-sided where the median-seed
rule reaches 0.122, and it costs no width where there is no seed effect.

### Seed count: three

Decided by the seed main effect measured in RQ1's own setting, by a rule fixed before the runs
landed. The effect there is 0.000 with a one-sided 95% upper bound of 0.0098, at or below the
0.01 where three seeds stop holding nominal. A null trained on a quarter of the data gives
0.013, which is not the registered setting.

### Stated power: sensitivity, not power at an observed effect

The design detects +0.0160 exact-match points for OpenStack at the seed effect's point
estimate and +0.0235 at its upper bound, for a conjunctive gate near 0.80. Power from a
pilot's own estimate is biased upward, so an earlier absolute figure is withdrawn.

### Inference numerics: fp32, weights upcast exactly from bf16

Decided by a determinism check across jobs and nodes. fp32 reproduces on every prediction
across two jobs on different nodes, where bf16 differs on a handful and on up to two within
one process, at no measurable cost in wall time.

## Where these live in the code

| decision | enforced by |
|---|---|
| pass rule, boundary, estimand | [`sphragis/measure/stats.py`](https://github.com/ajbarea/sphragis/blob/main/sphragis/measure/stats.py) |
| outcome-neutral checks and the halt rule | [`sphragis/experiment/neutral.py`](https://github.com/ajbarea/sphragis/blob/main/sphragis/experiment/neutral.py) |
| the seal, and refusing to fetch past it | [`sphragis/corpus/`](https://github.com/ajbarea/sphragis/tree/main/sphragis/corpus) |
| the evidence behind every row above | [Artifact index](artifacts.md) |

The dated record of how each of these was arrived at, including the readings that were
withdrawn, is the [research log](research-log.md).
