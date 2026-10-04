# Sphragis — Active Implementation Log

What is being built right now. The dated record of findings, numbers and corrections is
`docs/research-log.md`; milestones, open decisions and the Completed log are `ROADMAP.md`.
Figures are quoted from artifacts through `scripts/reading.py`, never transcribed.

---

## In flight

- **H1 over repeated admissible partitions** (#67, `measure/repeated-partitions`), with
  Wikimedia's admission and the planted-convention check stacked on it. The OpenStack power
  rerun and Wikimedia's sensitivity simulation are queued on TIGRIS; their outputs set the stated
  power in `docs/registered-decisions.md` and Stage 1 sections 5 and 7 (papers #30).
- **A GitHub collection route** (`sphragis/corpus/github.py`, stacked on the planted check).
  Pilot months (2024-11) are collected outside the corpus, under
  `~/ajsoftworks/sphragis-data-local/github-pilot/`, OpenJDK first, then the other sized
  candidates in turn (`queue.sh`). Each built month gives that organization's own
  thread-to-example conversion (`scripts/github_conversion.py`), and
  `scripts/github_sizing_report.py` re-derives the candidate table from it.

## Next

1. Merge this route (#82, with the 2026-10-04 decisions); the stack below it is on `main`.
2. Implement the readings beside the pass rule (meaningful, at least r of k, the random-effects
   summary across organizations) before the seal.
3. Collect Apache, then LLVM, .NET, Grafana, as the GitHub replication family (pilot, train and
   dev windows) to freeze members by 2026-11-20; then each member's admissible list, development
   pilot and simulation before its test window, and a replication gate beside `design()`, which
   refuses organizations outside the registered four.
4. If Qt and Chromium are both admitted, rerun every cell's simulation with `--cells 4`.
5. Rebuild the retrieval comparator (#77) on the registered runner.
6. Decisions that are AJ's: purging changes withdrawn from the hosts out of the raw snapshots;
   GitHub Pro (Student Pack) for required checks on papers `main`.

## Standing

The test window is sealed until in-principle acceptance. Stage 1 is due 2026-11-20, abstract
2026-11-13. `make redact` before committing a fresh result, `make pull-logs` after a job finishes,
and `make docs-index` *after* staging, never before. Chromium's bulk collection waits on its
infra-dev list's answer; Qt's REST build cannot be rerun now that its review UI is closed.

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

One-time migration on each cluster (done on TIGRIS). A job queued before the deploy runs its old script text
against the new `cluster-env.sh`, so drain the queue first, then deploy and migrate back to back:

    D=~/ajsoftworks/sphragis-data
    mkdir -p "$D/results" "$D/adapters"
    shopt -s nullglob
    mv -n ~/*.json ~/*.npz ~/*.claim "$D/results/"
    mv -n ~/pilot-examples.jsonl ~/corpus/
    mv -n ~/corpus ~/corpus-windows* ~/hf-cache "$D/"
    mv -n ~/scratch/* "$D/adapters/" && rmdir ~/scratch
