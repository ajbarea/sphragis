# Sphragis — Active Implementation Log

What is being built right now. The dated record of findings, numbers and corrections is
`docs/research-log.md`; milestones, open decisions and the Completed log are `ROADMAP.md`.

---

## In flight

**PR #13** (`feat/wellposed-filter`), open for AJ's review: adapter training, the pilots, the
RQ1 grid walk and gate, the contamination battery, power analysis, outcome-neutral checks,
and the fixes from a structured review. Rewrites the statistics the gate reads.

**`feat/cluster-switch`**, stacked on PR #13: `make submit CLUSTER=tigris|sporc|sporc-h100`,
per-machine uv and venv on the shared `$HOME`, and run provenance (cluster, job, GPU, peak
memory) in every job's results. TIGRIS has no `fl-mlm` association until Dr. Reznik completes
the ColdFront project review, so SPORC carries the work meanwhile.

**Train and dev windows collecting** (2024-11 through 2025-10, both organizations), raw
snapshots then build. Freezing waits on AJ's decisions below: it is irreversible.

## Next pickups

- **When SPORC job 21706441 ends** (A100 40 GB, `RUN_TAG=sporc-a100`, commit `c5ad368`): record
  in the research log the `train_adapter: gpu` peak lines from its log and the `provenance.gpu`
  block of `~/rq1-pilot-sporc-a100.json`, which settle whether 7B training fits in 40 GB, and
  the seconds per step, which size every `--time` for SPORC. If it ran out of memory, the next
  candidate is `CLUSTER=sporc-h100`, not a smaller batch: batch 16 is registered.
- Record the collected months' counts and drop profiles in the research log and the Stage 1
  skeleton's sampling section.
- Run the outcome-neutral checks inside a full RQ1 pilot run on TIGRIS, so the manipulation
  check reads real per-step losses and adapter norms.
- Replace the estimated test-window sizes in the power analysis once the window is defined.

## Waiting on AJ

- Venue (shortlist in the research log and session notes), and authorship, fixed at Stage 1.
- The estimand: pooled over examples or averaged over changes.
- The leakage threshold for outcome-neutral test 4; the pilot driver's 0.01 is provisional.
- The contamination control window's size, and guided completion's match criterion.
- The comment-anchoring rule (strict hunk, or within 10 lines).
- How windows are assigned: the seal censors about 22% of the dev window's slowest reviews.
- Merging PR #13, and when to freeze the windows.
