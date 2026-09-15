# Sphragis — Roadmap

Sphragis tests whether an organization leaves a learnable fingerprint in the code it reviews,
and what a declared boundary protects once it can. It is the apparatus for the Federated Agents
research direction: RQ1 is a go/no-go gate, RQ2 the leakage study that follows it.

**Design of record:** `docs/superpowers/specs/2026-09-13-gerrit-review-corpus-harness-design.md`
**Deadline that sets the order:** MSR 2027 Registered Reports Stage 1, **2026-11-20**.
The pre-submission checklist lives in the `papers` repo at `org-fingerprint/STAGE1-SKELETON.md`.

---

## Plan A — corpus construction

- [x] `scrub` — salted pseudonyms, identity fields nulled, emails swept from free text.
- [x] `manifest` — per-window counts and content hashes over a shared provenance header.
- [x] `gerrit` — XSSI prefix, cursor paging, `Retry-After`, client errors not retried.
- [x] `examples` — changed hunks, comments anchored by line range, unanchored hunks dropped.
- [x] `dedup` — exact, near-duplicate, repeated-boilerplate.
- [x] `split` — changes assigned whole to windows; the test window sealed, not collected.
- [x] `cli` — stage dispatch.
- [x] **Plan A2 — stage bodies.** Every stage is a command and the pipeline runs end to
  end on real data. `storage` (immutable snapshots), `build` (change to examples, drops
  counted by reason, resumable), `fetchers` (scrubbing at ingestion), `pipeline` (dedup,
  split, freeze), all wired behind `python -m sphragis.corpus`.
- [ ] **A real frozen corpus.** Both organizations, the full window range, verified.

## Plan B — measurement

- [x] `score` — exact match binds the pass rule; normalized EM and edit similarity reported.
- [x] `stats` — pairs cluster bootstrap over changes; `gate_verdict` is the pass rule as code.
- [x] `contamination` — Min-K%, guided completion, time partition, each post-versus-pre.
- [x] Purity enforced by test: no measurement module may import a GPU stack.

## Plan C — the experiment

- [x] `grid` — the condition grid as data. The base arm is evaluated once per window, not
  once per seed, so its sample is not silently inflated. 14 evaluations, 6 training runs.
- [x] `power` — pilot power analysis by simulation: bisect the effect until bootstrapped
  power reaches 80%. This is what section 5 of the Stage 1 report needs.
- [x] `runner` — orchestration against `Generator` and `Trainer` protocols. Pairs arms on
  example id rather than position, and refuses arms evaluated on different examples.
- [x] Purity enforced: only `model.py` may import a GPU stack, and a guard catches any new
  unguarded module in the package. Both guards tripped to confirm they fire.
- [x] `model.py` — base model, LoRA adapters, greedy decoding. Developed against a local
  RTX 3060 Ti on the same torch build TIGRIS resolves (2.14.0+cu130).
- [x] `slurm.py` — sbatch generation, verified against the live cluster. **One allocation
  walks the whole grid**: per-cell jobs would be twenty independent queue waits, and a
  freshly submitted job was estimated thirteen days out at fairshare 0.006.
- [ ] Wire the grid walk to the frozen windows (needs the corpus).
- [ ] Outcome-neutral tests wired to real model outputs.
- [x] Grid `--time` sized from measured throughput: 38-43 tok/s steady on a GH200.
- [x] **Pilot floor check passed** (rerun 2026-09-15, after review removed three confounds).
  Exact match 0.074 base, 0.407 adapted, +0.333 [+0.133, +0.565] over 18 held-out changes;
  normalized exact match +0.148 [+0.036, +0.292]. Exact match discriminates on this corpus
  once a model is adapted, and about half the gain survives normalization.
- [x] Per-change outcomes from the pilot, and the bootstrap interval on them.
- [x] **Review of the apparatus.** The gate reads direction; train and eval share one
  prompt format; the training budget is realized as declared and tested in CI; changes
  outside every window block the freeze; drop counts persist.
- [ ] Decide the estimand (pooled against change-averaged) for the Stage 1 report.
- [ ] Run the actual RQ1 contrast: adapter trained on one organization against one trained
  on the other, evaluated on the first.

**Metric change forced by a real run (2026-09-14).** The model answers refinement prompts
correctly and wraps the answer in prose and a markdown fence, so raw exact match scored 1
of 3 on output that was 3 of 3 right. `score()` now scores extracted code and reports
`exact_match_raw` beside it. This changes the primary metric's definition and is therefore
a Stage 1 pre-registration item, not an implementation detail.

Plan C is the only part needing a GPU, and the only part needing collected data.

## Study invariants

These are not style rules. CI asserts them, and loosening one has to show up in a diff.

- **The test window is sealed.** It is defined and hashed at Stage 1 and fetched only after
  in-principle acceptance, so fetch timestamps are the Stage 2 evidence that collection
  followed acceptance.
- **One binding metric.** Exact match decides the gate. Normalized EM and edit similarity are
  reported and are not part of the pass rule. LLM-as-judge is excluded from both.
- **The measurement runs without a GPU.**
- **Dependencies are pinned.** A paper artifact has to reproduce years from now.

## Relationship to the other repos

Sphragis is the Federated Agents apparatus. It does not vendor the others.

| Repo | Relationship |
|---|---|
| `phalanx-fl` | RQ2 federates adapters on its Flower stack. Not a dependency yet; decided when RQ2 starts. |
| `velocity-fl` | Aggregation kernel and Byzantine arena for RQ2. |
| `pharos` | The disclosure-measurement methodology this study reuses on real rather than generated data. Cited. |
| `papers` | `org-fingerprint/` holds the Stage 1 report. LINEAGE row P4. |

`provenance.py` is copied from phalanx-fl, not imported. Twelve lines of standard library is
the wrong thing to take a cross-repo dependency for, especially on a repo whose stated
invariant is to ride the latest Flower while this one must stay reproducible.
