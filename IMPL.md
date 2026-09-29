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

- **Corpus v3** (research log, 2026-09-28): `refine` removes examples whose target the reviewer
  wrote, a Gerrit suggested edit or an applied fix, since their share grows from train to dev and
  differs by organization. OpenStack and Qt refrozen; OpenStack's split criteria refixed on v3
  (`split-criteria-openstack.json`; v2's kept as `-v2`) before any Wikimedia split is computed.
  OpenStack's five placebo seeds rerun on TIGRIS under `RUN_TAG=v3` from `corpus-v3`, then the
  seed effect, the sensitivity and the dev pilot are recomputed, and the Stage 1 figures move to v3.
- **Split criteria as code** (`scripts/split_criteria.py`): run on Wikimedia once its train window
  is frozen, against the OpenStack ceiling in `split-criteria-openstack.json`.
- **RQ1 re-registered around granularity** (AJ, 2026-09-22). Spec
  `docs/superpowers/specs/2026-09-22-granularity-redesign.md`, gate as code in
  `sphragis/experiment/decomposition.py`, Stage 1 text in `papers` on branch
  `p4/granularity-registration`. Crossed-interval coverage is being re-measured at 97.5%.
- **Admitted organizations** (amended 2026-09-27). The cells are a rule over the organizations
  admitted by host permission (`design()`): H1 on each, H2 on Qt and Chromium only when both are.
  OpenStack is admitted; Wikimedia's corpus is building and its split is checked once it is frozen;
  Qt and Chromium wait on their permission requests (Chromium's corpus frozen by 2026-10-23).
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
log, NoteDb entry, rerun 2026-09-25).

Waiting on that route:

- Chromium bulk collection waits for the answer from Chromium's infra-dev list to the request for
  permission. One probe commit has been fetched, nothing more.
- `scripts/censoring.py` models creation to last update; an organization fetched by git is
  selected by creation to merge and needs that variable instead.
- The REST `build` stage re-fetches comments and diffs from the review host, so the Qt corpus can
  no longer be rebuilt from REST now that codereview.qt-project.org is closed to crawlers. Its
  frozen splits stand; whether Qt needs a git-side source is a decision, not work.

## Next

1. ✅ The seed effect at the half size is above 0.01 on both placebos, so five seeds (research log,
   2026-09-23).
2. The sensitivity analysis recomputed at the half size, at five seeds, at 97.5% and 95%, for the
   intersection over the admitted H1 cells, against the smallest effect of interest.
3. ✅ Sibling-half leakage on the dev window is below the registered threshold (research log,
   2026-09-23). 📋 The C++-restricted estimand beside H2.
4. The decomposition's pilot on the dev window before 2026-11-20.
5. **FDLoRA's measurement**, the last unmeasured adapter cut, once a job may read the corpora.

## Standing

The test window is sealed until in-principle acceptance. Stage 1 is due 2026-11-20, abstract
2026-11-13. Human-subjects review, deferred for every host on 2026-09-14, is due before
submission. `make redact` before committing a fresh result, `make pull-logs` after a job finishes,
and `make docs-index` *after* staging, never before.

## Cluster data root

TIGRIS/SPORC jobs now read and write under `$SPHRAGIS_DATA` (default
`$HOME/ajsoftworks/sphragis-data`, overridable), set by `sphragis/experiment/cluster-env.sh`:
`results/` (every `claim_result` output), `adapters/` (what was `$HOME/scratch`), `corpus/` and
`corpus-windows*` (what was `$HOME/corpus*`), and `hf-cache/hub` (`HF_HUB_CACHE`). `$HOME` itself
now holds only system files and `~/ajsoftworks`. `datasets-backup/` holds the second copy of `datasets/gerrit` and `datasets/gerrit-control`: the
raw REST snapshots are untracked and cannot be refetched, so `make backup-datasets` after every
fetch, which copies and then compares every file by sha256. RC keeps no backups; the adapters
are not copied anywhere, since they regenerate from the corpus and a pinned commit.

Pinned worktrees are created under
`$HOME/ajsoftworks/sphragis-pinned`, a sibling checkout, not data. The 33 under
`$HOME/sphragis-pinned` were removed with `git worktree remove` on 2026-09-26 after checking that
every job log in them was already in `datasets/logs/` byte for byte and nothing else was untracked.

One-time migration on each cluster. A job queued before the deploy runs its old script text
against the new `cluster-env.sh`, so drain the queue first, then deploy and migrate back to back:

    D=~/ajsoftworks/sphragis-data
    mkdir -p "$D/results" "$D/adapters"
    shopt -s nullglob
    mv -n ~/*.json ~/*.npz ~/*.claim "$D/results/"
    mv -n ~/pilot-examples.jsonl ~/corpus/
    mv -n ~/corpus ~/corpus-windows* ~/hf-cache "$D/"
    mv -n ~/scratch/* "$D/adapters/" && rmdir ~/scratch
