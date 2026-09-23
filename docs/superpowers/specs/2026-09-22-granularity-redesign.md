# RQ1 re-registered around granularity

**Status:** design of record, 2026-09-22, revised the same day after an independent adversarial
review (eleven findings, four blocking, all taken). Supersedes the organization-only hypothesis of
`2026-09-13-gerrit-review-corpus-harness-design.md` for the Stage 1 report; the corpus harness it
specifies is unchanged.

**Decided by:** AJ, 2026-09-22, choosing "at what granularity adaptation transfers" over "keep the
organization and report three controls", and adding Chromium as a third organization.

## Why the hypothesis changes before Stage 1

The registered organization-level gate came out **mixed** on the dev window at three seeds, and
three follow-up analyses pointed at the unit rather than the effect (research log, 2026-09-22):
one of Qt's two project halves (qt-a) reproduces Qt's organizational contrast while the other does
not; eight times the adapter rank does not rescue OpenStack's null arm; and the OpenStack/Qt pair
cannot separate organization from language. The design below was chosen **after** those readings,
and the report says so.

Registered-report practice lets pilot data shape the hypotheses and freezes them at in-principle
acceptance, provided the pilot is reported as pilot and every contingent analysis is specified in
advance (`# research(2026-09)`: Nature Portfolio and Taylor & Francis RR guidelines; MSR 2027
reviews the "rationale and plausibility" of hypotheses). The test window is sealed and untouched.

**Disclosure the report carries**, as a pilot-history paragraph: every dev-window analysis that
fed this choice, namely the organization gate at one and three seeds under both estimands (the
estimand choice decided that headline), the placebo at one seed and its retracted "fires on both"
reading, the placebo at three seeds, rank 256 at one and three seeds, the language histogram, the
separability probe and the project contrasts; and the framing that was rejected (keep the
organization, report three controls).

## The design: a nested decomposition of the organizational contrast

Each organization's projects are split into two halves by the rule `scripts/placebo_corpus.py`
already registers (largest first, always to the smaller side, ties by name, no seed). Projects with
no training-window examples are excluded before the split, and the exclusion is reported. Every
adapter is trained on one half at one size `N`, the smallest half's training count across all
organizations.

For a refinement from half `a` of organization `O`, three adapters are scored on the identical
example:

| adapter | trained on the evaluated half | same organization |
|---|---|---|
| `O-a` (own half) | yes | yes |
| `O-b` (sibling half) | no | yes |
| `P-x` (a foreign organization's half) | no | no |

- **Half-split contrast** `d_proj(O)`: `EM(O-a) - EM(O-b)` on `a`'s refinements, and symmetrically
  on `b`'s. The two halves are **weighted equally**, each pooled over its own examples, and the
  bootstrap resamples changes **within** each half. Pooling over examples across halves would let a
  difference in how well each half's data teaches survive as `(w_a - w_b)(q_a - q_b)` instead of
  cancelling.
- **Organization beyond the evaluated projects** `d_org(O, P)`: `EM(O-b) - mean_x EM(P-x)` on `a`'s
  refinements, and symmetrically, weighted and resampled the same way. The foreign halves are
  averaged per example, so each change stays one cluster.

The two sum to `EM(O-a) - mean_x EM(P-x)`, an organizational contrast at `N`. It is reported per
organization with its interval, and it is **not comparable** to the pilot's organization gate, which
trained at 4,327.

`O-a` has seen any one evaluated project only in proportion to that project's share of its half,
which differs by organization (many small projects in OpenStack, a few large ones in Qt). So H1 is
worded as the half-split contrast it measures. The per-project breakdown is reported against each
project's share of its half's training data, as exploratory.

## Hypotheses

- **H1 (half-split).** An adapter trained on the half of an organization containing the evaluated
  projects beats one trained on the other half: `d_proj(O) > 0`.
- **H2 (organization beyond the evaluated projects).** An adapter trained on the organization's other
  half beats one trained on a foreign organization: `d_org(O, P) > 0`.

| hypothesis | confirmatory cells | why |
|---|---|---|
| H1 | OpenStack, Qt, Chromium | both halves of one organization share its language mix |
| H2 | Qt against Chromium, Chromium against Qt | the only language-matched pair; OpenStack's `d_org` against Qt is exploratory |

"Both C++" is a claim to measure, not to assume: Qt's training window is about half C++, and
Chromium's mix is measured when its corpus is built. So H2 carries a **supplementary** estimand
restricted to C++ source and header hunks. Under ICH E9(R1) a restricted population is a different
estimand, so it is reported beside H2 and does not bind it.

## Verdicts, the pass rule and multiplicity

**Smallest effect of interest (SESOI): 0.01 exact match.** That's about 4% of the adaptation gain the
pilot measured (0.048 to 0.306, job 144345), and the size of the seed-effect upper bound (0.0098).
An effect smaller than a single training run's own seed noise does not change what an organization
should do. Fixed here, not derived from any contrast.

Each cell gets one of three verdicts, read off its crossed interval:

- **supported**: the lower bound lies strictly above zero;
- **absent**: the interval lies inside `(-SESOI, +SESOI)`. This is two one-sided tests at the same
  level (Lakens et al., AMPPS 2018);
- **inconclusive**: anything else. It is reported with its upper bound as "not detected above X".

A hypothesis **passes** only when every confirmatory cell is supported. Requiring all cells makes it
an intersection-union test, which holds its level without adjustment across cells (Berger,
Technometrics 1982). It is **absent** only when every cell is absent; otherwise it is
**inconclusive**.

Across the two hypotheses the family-wise rate is held at one-sided 0.025, the level the original
registration used per organization, by **Holm**. The hypothesis with the smaller p-value is tested
at 0.0125, on the lower bound of a two-sided 97.5% interval. If it passes, the other is tested at
0.025, on a 95% interval. Holm is uniformly more powerful than Bonferroni and valid under any
dependence. A fixed testing order would be cheaper still, but it would make H2 conditional on H1
and rule out in advance the reading in which the organization carries style that the halves do not.
Rubin (Synthese 2021) supports adjusting here, because "some boundary transfers" is a disjunctive
claim.

The two hypotheses share the sibling adapter `O-b`, with opposite signs. Within an organization
their intervals are therefore computed on **the same resamples**, and the report gives, per
organization, the share of draws in which each contrast is above zero and the interval of their
sum, beside the marginal verdicts.

A cell whose interval lies above zero and inside the SESOI band is **supported**, the registered
boundary deciding, and is flagged "below SESOI"; a hypothesis passing on such a cell is reported
with that qualifier.

Under the fallback design (no Chromium) H1 is the only confirmatory hypothesis and is read at
one-sided 0.025, a 95% interval. The confirmatory cells of both designs are constants in
`sphragis/experiment/decomposition.py` and the gate refuses any other set.

## Pre-committed readings

| H1 | H2 | reading |
|---|---|---|
| pass | absent | style transfers within a half and an organization adds nothing detectable above SESOI; a perimeter drawn around an organization is drawn in the wrong place |
| pass | inconclusive | style transfers within a half; whether an organization adds anything is not resolved at this design's sensitivity |
| absent or inconclusive | pass | an organization carries style beyond the evaluated projects |
| pass | pass | nested: both boundaries carry style, and the decomposition says how much each carries |
| absent | absent | no transferable style above SESOI at either boundary; the federated direction does not proceed on the assumption that one exists |
| otherwise | | not resolved at this design's sensitivity, reported with the upper bounds |

Negative readings ("adds nothing", "no transferable style") need **absent at both ranks** (see
below). A cell that fails where the others pass is characterised against the measured confounds,
as exploratory.

## Rank 256: supplementary, on the full grid

Rank 256 is run on **every** cell, not only on failing ones, so the decomposition is complete at
both ranks. A rerun at a different rank estimates a different estimand, so it is a supplementary
analysis under ICH E9(R1) (Step 5, EMA/CHMP/ICH/436221/2017: supplementary analyses "provide
additional insights" and are "given lower priority"). It never changes a verdict. A cell that is
inconclusive at rank 32 and supported at rank 256 is reported as capacity-limited. A negative
reading needs both ranks.

## Seeds

The registered seed rule requires five seeds where the seed main effect exceeds 0.01. The only
measurement near `N` is 0.013, taken at about 1,800 training examples; three seeds were justified at
4,327. So the decomposition runs at **five seeds** unless the seed effect, measured at `N` on the
dev window, has a one-sided 95% upper bound below 0.01. Crossed-interval coverage is re-measured at
the 97.5% level (`scripts/crossed_coverage.py`) before the report states it.

## Leakage between halves

A sibling half can share boilerplate with the evaluated half: tox and zuul configuration, CMake,
licence headers, bot dependency bumps. That would raise `EM(O-b)` and be credited to the
organization. The leakage battery is extended to sibling-half training against the evaluated half,
at Jaccard >= 0.5, and the rate is reported for every organization. The registered threshold (2% at
Jaccard >= 0.7) applies to it as an outcome-neutral test.

## Chromium: criteria, deadline, fallback

The Chromium corpus qualifies only if its split yields two halves where each has at least three
projects, no project holds more than 50% of its half's training examples, and each half has at least
as many training examples as OpenStack's smaller half. The project set is selected on feasibility
(merged human changes, anchored comments, language) before any contrast is computed.

**Deadline: frozen by 2026-10-23.** If Chromium misses the deadline or the criteria, H2 has **no
confirmatory cell**, since AOSP's public review stopped on 2025-03-27 and OpenStack and Qt share no
language. H2 is then registered as exploratory and H1 runs on OpenStack and Qt at their own `N`. The
report states this plainly.

## Power and what it binds

Training at `N`, at most OpenStack's smaller half, costs power. On the dev window the project
contrast halved with half the data (research log, 2026-09-18; one pair, one seed, one direction).
Before the report states a resolution, the sensitivity analysis is recomputed at `N`, at five seeds,
at 97.5% and 95%, and for the three-cell intersection. The report registers the minimum detectable
effect per cell and the SESOI side by side. If the design cannot reach absent at SESOI, the
"absent" readings are stated as unreachable in advance rather than discovered after the test.

## Compute

Six half-adapters, five seeds and two ranks give sixty training runs. Each half's window is scored
by four adapters (own, sibling, both foreign halves), five seeds and two ranks: 240 evaluations on
half-size windows. This is Stage 2 work on the test window after 2027-02-04, and the pilot runs the
same grid on the dev window before 2026-11-20.

## Open, recorded rather than taken

`crossed_bootstrap` draws one seed index for every adapter in a contrast, although the adapters are
independent training runs. Resampling seeds per adapter matches the crossed model more closely. This
was already true of the registered gate and is not changed here. It is measured against the coverage
simulation before Stage 1.

## What stays

The outcome-neutral tests and the halt rule, the contamination battery, the censoring rules, the
test window and its fetch horizon, equal-size training, exact match as the primary metric, the
pooled estimand, fp32 inference and a strict boundary.
