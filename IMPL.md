# Sphragis — Active Implementation Log

What is being built right now. The dated record of findings, numbers and corrections is
`docs/research-log.md`; milestones, open decisions and the Completed log are `ROADMAP.md`.

---

## In flight

**PR #13** (`feat/wellposed-filter`), open for AJ's review: adapter training, the pilots, the
RQ1 grid walk and gate, the contamination battery, power analysis, outcome-neutral checks,
and the fixes from a structured review. Rewrites the statistics the gate reads.

**Train and dev windows collecting** (2024-11 through 2025-10, both organizations), raw
snapshots then build. Freezing waits on AJ's decisions below: it is irreversible.

## Next pickups

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
