# Sphragis — Active Implementation Log

What is being built right now. The dated record of findings, numbers and corrections is
`docs/research-log.md`; milestones, open decisions and the Completed log are `ROADMAP.md`.
Figures are quoted from artifacts through `scripts/reading.py`, never transcribed.

---

## In flight

- **Qt's simulations** (IMPL 3d): the development pilot is read (research log, 2026-10-18; H1
  supported on likelihood, K 10). On TIGRIS: the full simulation at the pilot's K and the null at
  every other K in `K_GRID` (`partition_sensitivity.sbatch`, `METRIC=logprob_per_token`, no
  placebo), then `k_coverage.py` for Qt's K, its cell's bounds into the gate, and Qt's planted
  check 5 run. Qt's generation runs stay off the repository (their predictions can repeat review
  text); the likelihood rescorings are committed.
- **The GitHub replication family's collection**: pilot, train and dev windows (2024-10 to
  2025-10) for Apache, then LLVM, .NET, Grafana, one organization at a time, from the
  `data/github-collect` worktree into `~/ajsoftworks/sphragis-data-local/github/` (`collect.sh`,
  progress in `status.log`). Each month resumes from its checkpoint. A member not frozen and
  split-checked by 2026-11-20 is reported as not collected.

## Next

1. Collect Apache, then LLVM, .NET, Grafana, as the GitHub replication family (pilot, train and
   dev windows) to freeze members by 2026-11-20; then each member's admissible list, development
   pilot and simulation before its test window. At the freeze, one commit sets
   `REPLICATION_MEMBERS`; `replication_gate()` refuses to read the family before it.
2. After the permission deadline (2026-11-20) and before the first test window: one commit sets
   `ADMITTED_ORGANIZATIONS` and `FOREIGN_SOURCE`. Qt can be admitted until 2026-11-20, with its
   cells' bounds simulated and committed by then, and Chromium only if its split is frozen by
   2026-10-23, so it is not set earlier.
3. H2 as a Qt cell against a C++-matched foreign adapter (research log, 2026-10-14), in order:
   (a) once AJ admits the source's host, its pool sized by counts (AOSP: projects listed by
   language at `android-15.0.0_r1`); (b) done 2026-10-14: `registered_design`, `matched_clusters`, the
   gate's C++ population and `whole_hunk_supplement`; the commit that fixes the source sets
   `FOREIGN_SOURCE` before the first H2 development contrast, and a foreign pool over check 4 is
   recorded by setting `H2_WITHDRAWN`; a fixed source other than Chromium refuses every read
   once Chromium is admitted, so admitting Chromium then resets it; (c) the matched foreign
   sampler is `holdout.matched_foreign_draw` (2026-10-14); the two foreign adapters per run in the
   partition jobs wait on a granted source; the H2 development read needs H2 bounds in
   `detectable_effects`, which only the H2 simulation supplies; (d) Qt's terms arrived 2026-10-15 and its host is in `REST_PERMITTED`: its fetch, freeze, split check,
   development pilot for K and both cells' simulation, then the H2 test read over partitions
   through `require_test_read`.
4. When Qt or Chromium is admitted, rerun every cell's simulation at the admitted H1 cell count
   (`partition_sensitivity.py --cells`).
5. Decisions that are AJ's: purging changes withdrawn from the hosts out of the raw snapshots;
   GitHub Pro (Student Pack) for required checks on papers `main`; sending the Fuchsia request,
   the Chromium follow-up and the AOSP request (drafts in the rit account), and an IRB0000393
   amendment naming the source that grants.
   Also whether a published generation run keeps its `prediction` field: a prediction can repeat
   the example's review comment word for word, which the protocol's "no review text is
   published" excludes; Qt's generation runs are held back meanwhile (research log, 2026-10-18).

## Standing

A test window below its projected size is read with `power_at_size.py` run on its report
(`power_at_size.sbatch`), and its artifact passed to the gate as `powers`.

The test window is sealed until in-principle acceptance. Stage 1 is due 2026-11-20, abstract
2026-11-13. `make redact` before committing a fresh result, `make pull-logs` after a job finishes,
and `make docs-index` *after* staging, never before. Every googlesource git host waits on its
maintainers' written permission; Qt's REST API is open to the collector at the terms granted
2026-10-15 (`REST_PERMITTED`).

## Cluster data root

TIGRIS/SPORC jobs now read and write under `$SPHRAGIS_DATA` (default
`$HOME/ajsoftworks/sphragis-data`, overridable), set by `sphragis/experiment/cluster-env.sh`:
`results/` (every `claim_result` output), `adapters/` (what was `$HOME/scratch`), `corpus/` and
`corpus-windows*` (what was `$HOME/corpus*`), and `hf-cache/hub` (`HF_HUB_CACHE`). `$HOME` itself
now holds only system files and `~/ajsoftworks`. `datasets-backup/` holds the second copy of `datasets/gerrit` and `datasets/gerrit-control`: the
raw REST snapshots are untracked and cannot be refetched, so `make backup-datasets` after every
fetch, which copies and then compares every file by sha256; `make backup-github` does the same for
the GitHub family's corpus (`datasets-backup/gerrit-github`). RC keeps no backups; the adapters
are not copied anywhere, since they regenerate from the corpus and a pinned commit.

Pinned worktrees are created under
`$HOME/ajsoftworks/sphragis-pinned`, a sibling checkout, not data. The 33 under
`$HOME/sphragis-pinned` were removed with `git worktree remove` on 2026-09-26 after checking that
every job log in them was already in `datasets/logs/` byte for byte and nothing else was untracked.
