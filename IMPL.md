# Sphragis — Active Implementation Log

What is being built right now. The dated record of findings, numbers and corrections is
`docs/research-log.md`; milestones, open decisions and the Completed log are `ROADMAP.md`.
Figures are quoted from artifacts through `scripts/reading.py`, never transcribed.

---

## Just landed

**Corpus v2, the Stage 1 data audit's label rules.** Two defects passed the build's filters:
comments written by bots (Qt's Sanity Bot, flake8 output on pyside-setup, the QUIP-23 notice),
in Qt only, and Gerrit's one-click "Acknowledged". Both are removed by `refine`, which alone
applies label rules, so changing one is a re-refine from disk and never a refetch; bot templates
are read from each bot's own source.

- The build records the build rules each month was built under; the loader refuses a month built
  under other rules, a refinement under other label rules, and any derived corpus or file whose
  source moved or went stale. Manifests record both digests for `verify`.
- The months on disk predate those records and were stamped once, limited to the 54 audited
  snapshots in `sphragis/corpus/stamped-months.json`; Qt's 2024-10 carries no prompt context.
- Nearly half of Qt's organizational effect was its bot: +0.0316 as registered, +0.0171
  [-0.0034, +0.0374] without it. The placebo half survives.
- The rebase-only-successor finding was retracted: a Change-Id collision across cherry-picks.
  Every successor is a rework. AOSP audited the same way: no bot comments.

## In flight

- **Label audit v2.** 384 examples under a two-question rubric; two blind model raters, and a
  human's blind check of rater A on a published page. Agreement is reported once the check is in.
- **Retraining on v2** waits on Qt's answer about access; TIGRIS gets the refined corpora, and
  RQ2's client corpora are recut with records, before any GPU job.
- **RQ1 re-registered around granularity** (AJ, 2026-09-22). Spec
  `docs/superpowers/specs/2026-09-22-granularity-redesign.md`, gate as code in
  `sphragis/experiment/decomposition.py`, Stage 1 text in `papers` on branch
  `p4/granularity-registration`. Crossed-interval coverage is being re-measured at 97.5%.
- **Chromium, the third organization.** Host added and project set being scoped; the split has to
  qualify by 2026-10-23 or H2 has no confirmatory cell.
- **FDLoRA's schedule**, the last unmeasured adapter cut (PR #36).

## Next

1. ✅ The seed effect at the half size is above 0.01 on both placebos, so five seeds (research log,
   2026-09-23).
2. The sensitivity analysis recomputed at the half size, at five seeds, at 97.5% and 95%, for the
   three-cell intersection, against the smallest effect of interest.
3. ✅ Sibling-half leakage on the dev window is below the registered threshold (research log,
   2026-09-23). 📋 The C++-restricted estimand beside H2.
4. The decomposition's pilot on the dev window before 2026-11-20.

## Standing

The test window is sealed until in-principle acceptance. Stage 1 is due 2026-11-20, abstract
2026-11-13. Human-subjects review, deferred for every host on 2026-09-14, is due before
submission. `make redact` before committing a fresh result, `make pull-logs` after a job finishes,
and `make docs-index` *after* staging, never before.
