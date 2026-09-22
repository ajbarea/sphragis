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
OpenStack's 243. AOSP would have fixed it and cannot: its public review stopped on 2026-03-27 and
its dev window holds three examples.

## In flight

Nothing. The queue is empty and every result is committed.

## Next, and blocked on a decision rather than on work

1. **The unit.** Keep the organization and report three controls against it, or ask at what
   granularity adaptation transfers. The second uses every measurement, including the nulls.
2. **A third organization.** Chromium yields 0.7 reviewer-anchored comments a change against Qt's
   0.5 and is still active, so it clears the bar; adding a host touches the ethics determination.
3. **The rank branch fires on the wrong condition.** It requires *neither* arm to exclude zero,
   so a per-arm capacity artefact is never checked. Amending is legitimate while the test window
   is sealed.
4. **FDLoRA's schedule**, the last unmeasured cut. Its reading is registered; the implementation
   is not written.

## Standing

The test window is sealed until in-principle acceptance. Stage 1 is due 2026-11-20, abstract
2026-11-13. `make redact` before committing a fresh result, `make pull-logs` after a job finishes,
and `make docs-index` *after* staging, never before.
