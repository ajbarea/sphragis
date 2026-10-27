# Sphragis — Active Implementation Log

What is being built right now. The dated record of findings, numbers and corrections is
`docs/research-log.md`; milestones, open decisions and the Completed log are `ROADMAP.md`.
Figures are quoted from artifacts through `scripts/reading.py`, never transcribed.

---

## In flight

- **Simulations under relation chains** (research log, 2026-10-22, "Relation chains simulated",
  and 2026-10-25, "Wikimedia at K = 20 by chain"): every organization's K and its full
  simulation at that K are read under chains (OpenStack 10, Wikimedia 20, Qt 10; Qt's moved from
  15 by change). `k-coverage-<org>-likelihood-chains.json` is a Gerrit test read's K source.
  The paper (papers#111: organizations table, calibration, a run-by-chain crossed interval) and
  the deck (talks#34) read the chain K artifacts and simulations. The deck's split figure and team
  readings still read the by-change development pilots, since no chain split files exist; its
  `chains_hold` asserts the verdicts match under chains.
  Every pilot, K and simulation now records the chains it drew by a digest of what a read takes
  from the chains file (`chains.chains_sha256`), and the coverage rule, the simulation, the test
  read and both gates refuse chains that differ; the artifacts made before the digest resolve
  through `cells.CHAINS_SHA256_BEFORE_RECORDED`. `require_test_read` checks the read's, its K's
  and its bounds' cluster unit against `registered_read` (Gerrit by chain, GitHub by change).
- **Qt's simulations** (IMPL 3d): the development pilot is read (H1 supported on likelihood), its
  planted check 5 passes (research log, 2026-10-18), and its K under the coverage rule and its
  simulation at that K are read by change (2026-10-18, 2026-10-19) and by chain, the Gerrit test
  read's unit (2026-10-22). Qt's generation runs stay off the
  repository (their predictions can repeat review text); the likelihood rescorings are committed.
  The guard knows any written-grant host's run by its content, 20 pre-grant Qt files aside
  (research log, 2026-10-19, "The published-text guard reads a granted host from its content").
  Every other run is published through `scripts/withhold_text.py`, which digests its model text
  and keys its plain prompt and rules hashes, 120 earlier files pinned as they are (research log,
  2026-10-19, "Plain hashes of generated text keyed for publication").
- **The GitHub replication family** (research log, 2026-10-18): all four collected, frozen and
  split-checked; Apache and .NET are members, LLVM and Grafana cannot qualify.
- **H2's foreign source** (research log, 2026-10-20): Fuchsia's pool covers every matched draw
  with its first project, `fuchsia` (5,552 examples after dedup against Qt, 1,348 C++ of the 1,308
  the largest draw needs), so Fuchsia qualifies; AOSP's and V8's pools do not. Chromium, first in
  item 1's order, is being read under its rate grant (research log, 2026-10-19): ranking listings
  over every code project in two-day ranges (started 2026-10-20), then the covering prefix listed
  by name into its own root, built and covered the same way. The pool build (`chromium/src`,
  worktree chromium-data at 42c2f95) stopped at the 2026-10-23 08:35 shutdown with 2024-11 to
  2025-07 built; restarted 2026-10-26 19:21 (unit `sphragis-chromium-pool`, coverage waiter
  `sphragis-chromium-coverage`), it resumed 2025-08 from its checkpoint and built it by 19:34;
  2025-09 and 2025-10 follow, then refine, dedup and coverage. If it covers it is the source, else
  Fuchsia; that commit sets `FOREIGN_SOURCE`, and no H2 development contrast is read before it.
- **H2 in the partition design** (research log, 2026-10-18): the matched foreign arms run
  beside each H1 partition run (`foreign_arms.sbatch`, chained decoder_check -> foreign_arms ->
  likelihood_score), `partition_pilot.py --hypothesis H2` reads the development cell, and
  `partition_sensitivity.sbatch` with `FOREIGN_SOURCE` simulates it (`power.cell_contrast`,
  `plan_cell`). Both wait on a qualifying foreign source and Qt's 22 development runs with its arms.

## Next

1. At the Stage 1 freeze, one commit sets `REPLICATION_MEMBERS` to Apache and .NET;
   `replication_gate()` refuses to read the family before it. Each member's development pilot
   and simulation come before its test window.
2. After the permission deadline (2026-11-20) and before the first test window: one commit sets
   `ADMITTED_ORGANIZATIONS` (`FOREIGN_SOURCE` is set earlier, before the first H2 development
   contrast, item 3(b)). Qt can be admitted until 2026-11-20, with its
   cells' bounds simulated and committed by then, and Chromium only if its split is frozen by
   2026-10-23, so it is not set earlier.
3. H2 as a Qt cell against a C++-matched foreign adapter (research log, 2026-10-14), in order:
   (a) a source whose pool covers every draw (Fuchsia's does, AOSP's does not, 2026-10-20; Chromium's
   is being read and wins if it covers); (b) done 2026-10-14: `registered_design`, `matched_clusters`, the
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
   GitHub Pro (Student Pack) for required checks on papers `main`. Decided 2026-10-19: AJ reads
   Chrome Infra's answer as a rate covering Chromium, AOSP and Fuchsia, so H2's source is the
   first of Chromium and Fuchsia whose pool covers; without one, H2 has no confirmatory cell.
6. `provenance._git_record` splits `git status --porcelain` on whitespace, so an untracked path
   git quotes (non-ASCII) drops out of `uncommitted_code_sha256` and a rename records `old -> new`;
   read `--porcelain -z` instead, with a test (open bug issue, found in the comment sweep).

## Standing

A test window below its projected size is read with `power_at_size.py` run on its report
(`power_at_size.sbatch`), and its artifact passed to the gate as `powers`.

The test window is sealed until in-principle acceptance. Stage 1 is due 2026-11-20, abstract
2026-11-13. `make redact` before committing a fresh result, `make pull-logs` after a job finishes,
and `make docs-index` *after* staging, never before. A host is read only as its registry admits
it: Qt's REST API at the terms granted 2026-10-15, Chromium's, AOSP's and Fuchsia's under Chrome
Infra's rate answer of the same day (`REST_PERMITTED`); over git, AOSP alone
(`GIT_PERMITTED`).

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
