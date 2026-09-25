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
- **FDLoRA's schedule** (`sphragis/experiment/fdlora.py`, `scripts/fdlora_schedule.py`) follows
  the registered reading of Algorithm 1 except the outer aggregation step (lines 17 and 18), a
  recorded confound (`docs/research-log.md`, 2026-09-21 and 2026-09-23). Under that reading
  `-local` is always a past transmission, and the output's `local_equals` says which
  (2026-09-24). Job 183579 was cancelled before it ran. No job is queued: it trains on the v2
  client corpora, which wait on the recut above.

**A NoteDb route for hosts whose review UI robots.txt closes** (android-review, chromium-review,
codereview.qt-project.org). `fetch --via git` reads review records from the git hosts, which
allow fetching, into the same snapshot shape; `build` answers those rows from their own data under
the REST build rules, unchanged. It reads every branch and seals by last update, as REST does.
On AOSP it holds every REST change but those on branches the host no longer lists or filed in
another month, and the refined examples match but for one pseudonym from the older scrub (research
log, NoteDb entry, rerun 2026-09-25). In review as a PR, not merged.

Waiting on that route:

- Chromium bulk collection waits for the answer from Chromium's infra-dev list to the request for
  permission. One probe commit has been fetched, nothing more.
- `scripts/censoring.py` models creation to last update; an organization fetched by git is
  selected by creation to merge and needs that variable instead.
- The REST `build` stage re-fetches comments and diffs from the review host, so the Qt corpus can
  no longer be rebuilt from REST now that codereview.qt-project.org is closed to crawlers. Its
  frozen splits stand; whether Qt needs a git-side source is a decision, not work.

## Next

1. The granularity re-registration (PR #37) rebases onto this corpus once it merges; it carries
   the choice of unit, Chromium as a third organization and the rank-branch amendment.
2. **FDLoRA's measurement**, the last unmeasured adapter cut, once a job may read the corpora.

## Standing

The test window is sealed until in-principle acceptance. Stage 1 is due 2026-11-20, abstract
2026-11-13. `make redact` before committing a fresh result, `make pull-logs` after a job finishes,
and `make docs-index` *after* staging, never before.
