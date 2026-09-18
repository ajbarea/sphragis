# Sphragis — Active Implementation Log

What is being built right now. The dated record of findings, numbers and corrections is
`docs/research-log.md`; milestones, open decisions and the Completed log are `ROADMAP.md`.

---

## In flight

**PR #13** (`feat/wellposed-filter`), open for AJ: the whole apparatus, from corpus to gate.
Everything since the last merge sits on it, including the statistics the gate reads. Reviewed in
five rounds against the diff; every finding reproduced and fixed, summarised on the PR.

**RQ1 is answered on the dev window, at three seeds** (jobs 148198, 148404, 148406): Qt +0.0312
[+0.0080, +0.0565], OpenStack +0.0171 covering zero, mixed under both rules and both estimands.
The test window stays sealed.

**RQ2 has its first result and its next design.** 34 client updates from one initialization say a
client's update identifies its codebase family, not its organization. Separating the two needs
several projects per organization in one language, which AOSP's C++ projects supply beside Qt's.
AOSP months 2024-01 to 2025-03 are fetching and building locally; the public record ends 2025-03-27.

## Next pickups

- Build the AOSP and Qt C++ client corpora, rerun `client_updates` over the larger source set, and
  read `organization beyond project` with the project as the exchangeable unit.
- Record the sensitivity analysis (`scripts/sensitivity.py`) in the Stage 1 skeleton's section 5,
  replacing the withdrawn 0.833 power figure.
- Re-run the calibration sweep under fp32 inference, since the gate's non-monotonicity was measured
  under bf16.
- Report Gap-K% beside Min-K%++, which needs the battery re-run: the saved results keep scores, not
  per-token log-probabilities.

## Waiting on AJ

- Merging PR #13, and when to freeze the windows: freezing is irreversible.
- Venue and authorship, fixed at Stage 1.
- Whether RQ1 claims "organizations leave a learnable fingerprint" (conjunctive, as registered) or
  "this organization does" (per-organization, higher power, weaker claim).
- Whether running a sole-authored paper on the lab's `fl-mlm` allocation needs an acknowledgement.
