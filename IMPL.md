# Sphragis — Active Implementation Log

What is being built right now. The dated record of findings, numbers and corrections is
`docs/research-log.md`; milestones, open decisions and the Completed log are `ROADMAP.md`.
Figures are quoted from artifacts through `scripts/reading.py`, never transcribed.

---

## In flight

- **This branch (`results/h2-pilot`, issue #228): H2's development pilot and simulation.**
  Done: Qt's 22 runs' foreign arms (TIGRIS, all completed 2026-10-29); the pilot by change and by
  chain (`partition-pilot-qt-foreignchromium-likelihood[-chains].json`, read with
  `scripts/reading.py`); the published-text guard lists the two hash places the foreign reads add,
  and the four foreign read families are committed while their job sidecars stay ignored (two
  review rounds, the second clean). Running on TIGRIS at this branch's first commit (525d1af):
  `partition_sensitivity.sbatch` with `FOREIGN_SOURCE=chromium CHAINS=1 RUN_TAG=rp1.0`, smoke
  635096 (done), full 635097 (running), null-only K 12/15/20/25 635098-635101 (done, copied to
  `datasets/results`, sha256 equal at both ends, untracked until the results commit). No K
  through 25 holds at the sizing bound (stricter rate 0.0135 at K 25 against 0.0125; K 10's
  log reads 0.020), so the rest of the registered grid, null-only K 30/35/40, was submitted
  2026-10-29 from a detached worktree at 525d1af (`.claude/worktrees/h2-submit`, the cluster's
  commit): 654028-654030. A grid with no K that holds is refused by `coverage_runs`; that case
  goes to AJ. K 10's live log also reads the sizing bound detecting +0.0174 at 97.5%, above δ
  (0.0161); judge on the artifact at the coverage K, and a detectable effect above δ goes to AJ
  before P4. Next: copy 635097 and 654028-654030, `h2-steps.sh step4b` with the grid extended;
  a full simulation at the coverage K; research-log entry; PR linking #228; review until clean;
  merge; then H2's detectable effect into P4.
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
- **H2's foreign source is Chromium** (research log, 2026-10-27, "Chromium's pool covers every
  matched draw with its first project"): first in item 1's order, its pool's first project,
  `chromium/src`, covers all 80 matched draws and 40 partitions against Qt, so
  `decomposition.FOREIGN_SOURCE = "chromium"`. Fuchsia's pool also covers (2026-10-20) and is not
  used; AOSP's and V8's do not. No H2 development contrast has been read. The pool's raw data stay in
  `sphragis-data-local/chromium-pool/` (not published); its logs are in
  `datasets/logs/chromium-pool-2026-10-20/`.
- **H2 in the partition design** (research log, 2026-10-18): the matched foreign arms run
  beside each H1 partition run (`foreign_arms.sbatch`, chained decoder_check -> foreign_arms ->
  likelihood_score), `partition_pilot.py --hypothesis H2` reads the development cell, and
  `partition_sensitivity.sbatch` with `FOREIGN_SOURCE` simulates it (`power.cell_contrast`,
  `plan_cell`). Both wait on Qt's 22 development runs with their arms: submitted 2026-10-28 from
  9716914 (TIGRIS jobs 613360 to 613405, `foreign_arms.sbatch` with `FOREIGN_SOURCE=chromium` and
  the cleared pool, each followed `afterok` by `likelihood_score.sbatch`).

## Next

1. At the Stage 1 freeze, one commit sets `REPLICATION_MEMBERS` to Apache and .NET;
   `replication_gate()` refuses to read the family before it. Each member's development pilot
   and simulation come before its test window.
2. After the permission deadline (2026-11-20) and before the first test window: one commit sets
   `ADMITTED_ORGANIZATIONS` (`FOREIGN_SOURCE` is set earlier, before the first H2 development
   contrast, item 3(b)). With Qt admitted, H2 is confirmatory against Chromium, so
   `decomposition_gate` refuses a Qt read until H2's bounds are simulated and `h1_test_gate`
   refuses until H2's test read over partitions is built. Qt can be admitted until 2026-11-20, with its
   cells' bounds simulated and committed by then, and Chromium only if its split is frozen by
   2026-10-23, so it is not set earlier.
3. H2 as a Qt cell against a C++-matched foreign adapter (research log, 2026-10-14), in order:
   (a) done 2026-10-27: Chromium's pool covers every draw, first in the order, so
   `FOREIGN_SOURCE = "chromium"`; `partition_pilot.py`, `partition_sensitivity.py` and
   `rq1_pilot.py` refuse an H2 step against another source (`require_registered_source`). The pool,
   cleared of Qt's copies as the stop rule read it, was written on 2026-10-27 at the registered
   digest (research log, "Chromium's pool cleared of Qt's copies"):
   `sphragis-data-local/chromium-pool/cleared/chromium.jsonl` with its `.source.json` record, the
   file `rq1_pilot.py --foreign-pool` and `foreign_arms.sbatch FOREIGN_POOL` take. Both were
   copied to TIGRIS on 2026-10-28 (`~/ajsoftworks/sphragis-data/chromium-pool/cleared/`, sha256
   equal at both ends: `chromium.jsonl` 03f3faa3, its record 79c3267a); (b) done 2026-10-14: `registered_design`, `matched_clusters`, the
   gate's C++ population and `whole_hunk_supplement`; the commit that fixes the source sets
   `FOREIGN_SOURCE` before the first H2 development contrast, and a foreign pool over check 4 is
   recorded by setting `H2_WITHDRAWN`; (c) the matched foreign
   sampler is `holdout.matched_foreign_draw` (2026-10-14); the two foreign adapters per run in the
   partition jobs take `FOREIGN_SOURCE=chromium` and the cleared pool from the job's
   environment; the H2 development read needs H2 bounds in
   `detectable_effects`, which only the H2 simulation supplies; (d) Qt's terms arrived 2026-10-15 and its host is in `REST_PERMITTED`: its fetch, freeze, split check,
   development pilot for K and both cells' simulation, then the H2 test read over partitions
   through `require_test_read`.
4. When Qt or Chromium is admitted, rerun every cell's simulation at the admitted H1 cell count
   (`partition_sensitivity.py --cells`).
5. Decisions that are AJ's: purging changes withdrawn from the hosts out of the raw snapshots;
   GitHub Pro (Student Pack) for required checks on papers `main`. Decided 2026-10-19: AJ reads
   Chrome Infra's answer as a rate covering Chromium, AOSP and Fuchsia; by that reading Chromium's
   pool, first in the order, is H2's source (2026-10-27).
6. Dependabot alerts 2-4 (filelock) stay open though `main` pins the patched 4.2.1 (#203): the
   graph update GitHub ran on that merge failed on its side ("fetching job details: missing
   response") and cannot be rerun, so its graph still reads 3.32.6. The next push that changes
   `uv.lock` updates it and closes them as fixed; nothing to bump or dismiss.

## Standing

A test window below its projected size is read with `power_at_size.py` run on its report
(`power_at_size.sbatch`), and its artifact passed to the gate as `powers`.

The test window is sealed until in-principle acceptance. Stage 1 is due 2026-11-20, abstract
2026-11-13. The paper ends its body on page 6 again (papers#112) and is mirrored to the lab's
Overleaf project (`make overleaf PAPER=org-house-style`; Overleaf c418573 = papers 6e595c7, #113:
Chromium as H2's foreign source, its pool's widening disclosed, body on six pages). `make redact` before committing a fresh result, `make pull-logs` after a job finishes,
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
