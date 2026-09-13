# Sphragis — Active Implementation Log

Rolling notes on in-flight work. See `ROADMAP.md` for the stable plan.

---

## Current focus

### Repo split from phalanx-fl (2026-09-13)

The corpus and measurement code was built inside `phalanx-fl` and moved here. It had grown to
80% of that repo's test suite while importing exactly one function from it, in a repo whose
stated purpose is a Flower testbed.

The decisive argument was not tidiness but a policy conflict: phalanx's roadmap carries a
recurring invariant to ride the latest Flower release, and a paper artifact needs the opposite.
Those are contradictory release policies sharing one lockfile, already visible in Dependabot
alerts pinned by `flwr` on a repo whose paper code never imported `flwr`.

`phalanx.corpus` became `sphragis.corpus`, plan B's three modules became `sphragis.measure`,
and `provenance_header` was copied rather than imported.

### Next: plan A2, stage bodies

Wire `fetch`, `build`, `dedup`, `split` and `freeze` to real artifacts under
`datasets/gerrit/<org>/`. Blocked on the HSRO determination, which is drafted at
`corpus/HSRO.md` and awaiting submission.

Everything else is unblocked: `gerrit.fetch_changes` takes a transport seam, so the stage
bodies can be built and tested against recorded fixtures before any live collection.

---

## Open bugs & findings

_None active._
