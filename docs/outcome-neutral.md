---
title: Outcome-neutral tests
description: What has to hold for the study to be interpretable, and the evidence each test can fire.
---

# Outcome-neutral tests

These are pre-specified checks on the apparatus, run before the confirmatory comparison. They
are neutral to the outcome: none of them can be passed by the hypothesis being true, and none
of them can be failed by it being false. **A failure halts the study and is reported as an
apparatus failure rather than as a result.**

The halt rule is one function,
[`apparatus_holds`](https://github.com/ajbarea/sphragis/blob/main/sphragis/experiment/neutral.py),
and it is true only when every check passed. There is no partial credit and no override: if it
is false, `H1` is not read.

Each check returns its evidence beside its pass flag, so a run's JSON carries why it passed
and not only that it did. The pilot figures below are what each check returned on real model
output, and they are here to show that the check *can* fire rather than to preview a result.

## 1. Contamination battery

Three methods, each comparing the post-cutoff corpus against a pre-cutoff control window from
the same projects: the time-based partition itself, Min-K%++ probability, and guided
completion measuring verbatim continuation. Min-K%++ runs on the base checkpoint, of which the
registered Instruct model is a fine-tune, because detection methods are reported inconsistent
on modern models and contamination introduced by instruction fine-tuning went undetected by
every method but one. Guided completion stays on the evaluated Instruct model.

This is the one check that returns no verdict, deliberately. With no published content cutoff
for the checkpoint, a post-versus-pre difference near zero means either no exposure or an
instrument blind to it, and the reading is pre-committed in the report rather than computed:
it is reported as an inconclusive exposure bound, never as evidence of absence.

Measured on six months each side, every membership statistic separates the windows by less
than a tenth of a point:

| statistic | gap |
|---|---|
| Min-K% | -0.130 |
| Min-K%++ | -0.075 |
| Gap-K% | -0.056 |
| guided completion | +0.003 |

Gap-K% exists to be robust to distribution shift, and it returns the *smallest* separation of
the three, which is what the drift reading predicts rather than the memorization one. A
model-less bag-of-words classifier separates the same windows better than Min-K%++ does, so by
the criterion in Meeus et al. (SoK, SaTML 2025) the gap is indistinguishable from drift.
Contamination protection rests on the post-cutoff windows postdating the checkpoint's release,
not on this battery.

## 2. Positive control

Each organization's own adapter must beat the base model on that organization's data, read
with the same one-sided rule as the gate. If it fails, the training setup is broken and the
comparison between adapters means nothing.

Passed on OpenStack, deduplicated, both arms under the model's chat template: exact match
0.074 base against 0.407 adapted, paired cluster bootstrap on the difference +0.333
[+0.133, +0.565]. Qt's own control returns +0.218 [+0.120, +0.337]. The base arm uses the chat
template deliberately: scored on the raw prompt the base model falls to zero, which would have
inflated this control.

## 3. Manipulation check

Training loss decreases and adapter weights differ from initialization, per run and per seed.

"Decreases" is the final epoch's mean step loss below the first epoch's, not the last step
against the first: the last step holds only the examples left over after full batches, so its
loss is noise. With one epoch, the halves are compared instead.

## 4. Leakage check

The residual near-duplicate rate between train and test, at a stated similarity, below a
threshold fixed in the report. The check applies dedup's own shingling and Jaccard comparison
across the boundary between two windows rather than within one, and the caller passes the
threshold rather than inheriting a default, so the threshold that ran is always the one
written down.

Measured on the complete OpenStack corpus v2 of 5,469 deduplicated examples, train into dev:

| Jaccard | rate |
|---|---|
| 0.7 | 1.06% |
| 0.6 | 1.42% |
| 0.5 | 1.77% |

At 0.8 the rate is zero by construction, since dedup removes pairs at that threshold across
windows as well as within them, so a threshold registered there would be a test that cannot
fail. The registered threshold is therefore set at a looser similarity and bounded above the
measured rate, so that it has something to detect.

## 5. Non-degeneracy

Exact match is neither 0 nor 1 across every condition on both held-out sets. A condition
pinned at either end carries no information, and a contrast between two such conditions is
arithmetic rather than measurement.

This check has fired for real. On the mismatched arms of one calibration run the adapters
score exactly zero, because an adapter trained on annotated refinements can never match an
unannotated reference, so non-degeneracy failed and the halt rule went false. The ceiling
condition is degenerate by construction, and the apparatus is what said so.

## Beyond the registered five

Two further checks are not part of the halt rule and are reported beside it.

**The contrast is not blind.** Every null is otherwise ambiguous between "no
organization-specific adaptation" and "an instrument that cannot see one". A convention
planted on every refinement of one half returns +0.310 and +0.316 over the two sides and the
gate returns `pass`, so the contrast can see a house style of that size. For scale, the
planted convention moves the contrast by about ten times what the two real organizations
differ by.

**The interval is calibrated.** The bootstrap's own false-positive rate, measured against the
nominal 5% at the accuracy the gate operates at, is 6.0% at 19 changes and 4.8% at 91. The
gate is conjunctive, so its own rate sits below either arm's.

## Reading the evidence yourself

Every figure above resolves to a committed file under `datasets/results/`, listed with its
writing script in the [artifact index](artifacts.md). The checks themselves are
`sphragis/experiment/neutral.py`, and a test holds the measurement package to running without
a GPU: a halt condition that can only be exercised on a cluster is one that never fires.
