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

**The collection stop, in code.** REST requests go only to hosts in `REST_PERMITTED`
(review.opendev.org, paced at its 2 s crawl delay), and the test suite refuses remote
connections, datagrams and lookups made in-process (subprocesses excepted). A rebased test had reached chromium-review; the
research log records what is and is not known about those requests.

## In flight

- **Label audit v2.** 384 examples under a two-question rubric; two blind model raters, and a
  human's blind check of rater A on a published page. Kappa, AC1 and specific agreement are in the
  artifact; the human's are added once the check is in.
- **Retraining on v2** waits on Qt's answer about access; TIGRIS gets the refined corpora, and
  RQ2's client corpora are recut with records, before any GPU job.
- **FDLoRA's schedule** (`sphragis/experiment/fdlora.py`, `scripts/fdlora_schedule.py`) matches
  the registered reading of Algorithm 1 (`docs/research-log.md`, 2026-09-21). Job 183579 ran the
  pre-review code and was cancelled before a result; both defects are fixed (2026-09-23). No job
  is queued: a TIGRIS job reads the AOSP and Qt corpora only after the ruling on their terms of use.

## Next

1. The granularity re-registration (PR #37) rebases onto this corpus once it merges; it carries
   the choice of unit, Chromium as a third organization and the rank-branch amendment.
2. **FDLoRA's measurement**, the last unmeasured adapter cut, once a job may read the corpora.

## Standing

The test window is sealed until in-principle acceptance. Stage 1 is due 2026-11-20, abstract
2026-11-13. `make redact` before committing a fresh result, `make pull-logs` after a job finishes,
and `make docs-index` *after* staging, never before.
