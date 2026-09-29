# H1 over repeated partitions

Status: design, 2026-09-29. Decided by AJ the same day ("item 1"), after the partition-variance
measurement (research log, 2026-09-29).

## Why

H1 reads the half-split contrast on one registered partition of an organization's projects. That
partition is a function of the corpus and moved 117 of 238 OpenStack projects between corpus v2 and
v3, and four alternative partitions meeting the same criteria put partition-to-partition variation
(0.0128, 90% about [0.0058, 0.035]) at the order of the seed effect. A single partition therefore
makes H1 a reading of an arbitrary draw: the "p-value lottery" of single sample splits (Meinshausen,
Meier and Bühlmann 2009; Ritzwoller and Romano 2023). The spread itself is the within-organization
heterogeneity the study is about.

## Estimand

For an admitted organization, H1 is the mean, over the distribution of admissible balanced
partitions and training seeds, of the equally weighted half-split contrast: each half's own adapter
against its sibling on the half's held-out changes, exact match, the two halves weighted equally.
An admissible partition is `assign(counts, order_seed=s)` over training-window counts, meeting
`split_criteria` against OpenStack's registered halves. The registered largest-first split is
dropped as the reading and kept as one draw's worth of history.

## Runs

A run is one (partition seed, training seed) pair: a partition, two adapters, both halves scored.
One training seed per partition: across runs the seed effect averages with the partition effect,
and a second seed within a partition buys less than a second partition.

- **One evaluation set.** Each run's halves are built by `placebo_corpus.py --dedup-org`: the
  organization is deduplicated once, then partitioned, so no duplicate pair can land in both
  halves and survive in both, and every partition scores the same examples. The exact and
  near-duplicate stages then remove nothing inside a half; only the boilerplate stage, whose
  threshold is a share of the corpus, could, so the cell reads the examples every run scored and
  refuses the runs if more than 1% are missing from any (`partitions.MAX_DROPPED_SHARE`).
- **Admissible partitions, in order.** `scripts/admissible_partitions.py` tries partition seeds
  from 1 through the same pipeline and lists the first `K_MAX` that meet `split_criteria`; run k
  uses the k-th, with training seed k. The list is committed before any run, from training-window
  rows only.
- **A fixed training size.** Under dedup-first no OpenStack partition reaches the registered
  split's 2,004 (per-half dedup keeps a cross-half duplicate in both halves, inflating them), so
  the size floor is the design's own training size: **N = 1,850**, the 5th percentile of the
  smaller half over 300 seeded partitions, rounded down to 50, taken over training-window counts
  alone (smallest 1,791, median 1,988). Across admitted organizations N is the least of that
  figure. Every adapter trains at N.

## Number of runs: reproducible aggregation

K is chosen by Ritzwoller and Romano's Anscombe-Chow-Robbins rule (arXiv:2311.14204, Algorithm 1):
runs are added until the variance of the mean over runs, estimated as `s^2 / K` from the per-run
H1 estimates, is at most `cv = 0.5 * (xi / z_{1 - beta/2})^2`, with a burn-in `K_init` and a cap
`K_max`. Two researchers drawing partitions independently then report H1 estimates within `xi` of
each other with probability at least about `1 - beta`. Registered: `xi = 0.01` (the SESOI), `beta =
0.05`, `K_init = 8`, `K_max = 40`; the values are checked by simulation below before they are fixed.
The rule reads only per-run point estimates of the test window's contrast, which the interval
already reads, and is applied the same way in every admitted organization.

*Amended 2026-09-29, after runs 1 to 22 of the development-window pilot had been read at both 16
and 22 runs (both inconclusive):* runs may be computed in batches ahead of the rule. The rule is then applied in the admissible
list's order, K from `K_INIT` up, and the reading uses the first K at which it holds; runs past
that K are reported and never enter the cell. The stopping K depends only on the fixed order and
the runs' estimates, never on which runs happened to be computed.

## Interval

Runs and changes are crossed: every run scores every held-out change of the organization, since
each change belongs to one half or the other in every partition. The interval is the percentile
interval of a crossed bootstrap (Owen's pigeonhole, as the registered crossed seed-by-change
interval): each replicate resamples K runs and the organization's changes with replacement, and
computes each resampled run's equally weighted half-split contrast on the resampled changes of each
of its halves, averaged over runs. Changes are resampled over the organization rather than within
each half, since a change moves between halves across partitions, so on a single partition it
agrees with `sphragis.measure.stats.stratified_crossed_draws` to first order, not exactly. Holm
levels, the pass rule (lower bound above zero), the bounded reading against the
registered detectable effect, and the SESOI band are unchanged.

## What has to be shown by simulation before registration

1. **Coverage.** Under the null, the one-sided false-positive rate at each Holm level is at or below
   nominal across the run variance the measurement allows (sigma_run from 0.005 to 0.035), at the
   K the rule stops at.
2. **Sensitivity.** The detectable effect at power 0.928 per cell, at the projected test size,
   at the measured run variance and at its upper bound, replacing the single-partition figures.
3. **Stopping.** The distribution of the stopping K, and the share of studies hitting `K_max`.
4. **Reproducibility.** Two independent aggregations on the same simulated data agree within `xi`
   at rate at least `1 - beta`.

## Cost

Each run is one placebo job, about 1.5 GPU-hours on a GH200. At the measured run variance the rule
stops near 20 runs, about 30 GPU-hours an organization, against 7.5 for five seeds on one
partition.

## Out of scope here

H2 (the organization beyond the evaluated projects) reads the sibling half against a foreign
organization's halves and inherits the same partition dependence on both sides; it follows once H1's
machinery is validated, and it runs only if Qt and Chromium are both admitted.
