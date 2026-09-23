# Sphragis — Active Implementation Log

What is being built right now. The dated record of findings, numbers and corrections is
`docs/research-log.md`; milestones, open decisions and the Completed log are `ROADMAP.md`.
Figures are quoted from artifacts through `scripts/reading.py`, never transcribed.

---

## Just landed

**RQ1's hardening is complete, and all three results say the same thing.** Registered rule
throughout: pooled estimand, crossed interval, three seeds.

| | the organization | an arbitrary half of it | eight times the rank |
|---|---|---|---|
| Qt | +0.0316 [+0.0089, +0.0567] | +0.0305 [+0.0037, +0.0565] | +0.0309 [+0.0068, +0.0567] |
| OpenStack | +0.0230 [+0.0000, +0.0457] | +0.0099 [-0.0273, +0.0463] | +0.0136 [-0.0100, +0.0360] |

The placebo splits one organization's own projects in half, a boundary with no organizational
meaning. In Qt it is indistinguishable from the real one. In OpenStack it returns nothing, and
the organizational contrast only just clears zero to begin with. Eight times the adapter capacity
moves Qt by seven ten-thousandths and moves OpenStack the wrong way, so the pre-registered
capacity explanation for a null arm does not hold.

**Language is the rival explanation and cannot be controlled on this pair.** OpenStack's training
window is 47% Python, Qt's is 50% C++, and the dev window holds eight Qt Python examples against
OpenStack's 243. AOSP would have fixed it and cannot: its public review stopped on 2025-03-27 and
its dev window holds three examples.

## In flight

- **RQ1 re-registered around granularity** (AJ, 2026-09-22). Spec
  `docs/superpowers/specs/2026-09-22-granularity-redesign.md`, gate as code in
  `sphragis/experiment/decomposition.py`, Stage 1 text in `papers` on branch
  `p4/granularity-registration`. Crossed-interval coverage is being re-measured at 97.5%.
- **Chromium, the third organization.** Host added and project set being scoped; the split has to
  qualify by 2026-10-23 or H2 has no confirmatory cell.
- **FDLoRA's schedule**, the last unmeasured adapter cut: implementation and TIGRIS run.

## Next

1. ✅ The seed effect at the half size is above 0.01 on both placebos, so five seeds (research log,
   2026-09-23).
2. The sensitivity analysis recomputed at the half size, at five seeds, at 97.5% and 95%, for the three-cell
   intersection, against the smallest effect of interest.
3. Sibling-half leakage and the C++-restricted estimand, both reported beside H2.
4. The decomposition's pilot on the dev window before 2026-11-20.

## Standing

The test window is sealed until in-principle acceptance. Stage 1 is due 2026-11-20, abstract
2026-11-13. Human-subjects review, deferred for every host on 2026-09-14, is due before
submission. `make redact` before committing a fresh result, `make pull-logs` after a job finishes,
and `make docs-index` *after* staging, never before.
