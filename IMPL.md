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
- **The GitHub replication family** (research log, 2026-10-18): all four collected, frozen and
  split-checked; Apache and .NET are members, LLVM and Grafana cannot qualify.
- **H2's foreign source** (research log, 2026-10-19): AOSP's pool, read across its whole grant,
  covers 32 of the 80 matched draws (1,071 C++ examples of the 1,308 the largest needs), so it
  does not qualify, as Chromium's V8 pool did not. Fuchsia is the one source left; until it
  grants and covers, `FOREIGN_SOURCE` stays unset and H2's development arms wait.
- **H2 in the partition design** (research log, 2026-10-18): the matched foreign arms run
  beside each H1 partition run (`foreign_arms.sbatch`, chained decoder_check -> foreign_arms ->
  likelihood_score), `partition_pilot.py --hypothesis H2` reads the development cell, and
  `partition_sensitivity.sbatch` with `FOREIGN_SOURCE` simulates it (`power.cell_contrast`,
  `plan_cell`). Both wait on AOSP's pool and Qt's 22 development runs with their arms.

## Next

1. At the Stage 1 freeze, one commit sets `REPLICATION_MEMBERS` to Apache and .NET;
   `replication_gate()` refuses to read the family before it. Each member's development pilot
   and simulation come before its test window.
2. After the permission deadline (2026-11-20) and before the first test window: one commit sets
   `ADMITTED_ORGANIZATIONS` and `FOREIGN_SOURCE`. Qt can be admitted until 2026-11-20, with its
   cells' bounds simulated and committed by then, and Chromium only if its split is frozen by
   2026-10-23, so it is not set earlier.
3. H2 as a Qt cell against a C++-matched foreign adapter (research log, 2026-10-14), in order:
   (a) AOSP's pool (In flight); (b) done 2026-10-14: `registered_design`, `matched_clusters`, the
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
   GitHub Pro (Student Pack) for required checks on papers `main`. Fuchsia's discuss list was
   asked on 2026-10-15 and has not answered; by item 1 of the H2 plan a grant before the first H2
   development contrast would make Fuchsia the source in AOSP's place.
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
