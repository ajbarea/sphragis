# Sphragis — Active Implementation Log

What is being built right now. The dated record of findings, numbers and corrections is
`docs/research-log.md`; milestones, open decisions and the Completed log are `ROADMAP.md`.

---

## In flight

**PR #13** (`feat/wellposed-filter`), open for AJ: the whole apparatus, from corpus to gate and
both RQ2 threat models. Its description was rewritten on 2026-09-18 against current evidence.

**RQ1, dev window, three seeds, fp32:** Qt +0.0316 [+0.0089, +0.0567], OpenStack +0.0230
[+0.0000, +0.0457], mixed. The test window stays sealed until in-principle acceptance.

**RQ2 has become a defence evaluation, and its answer is a function of a knob.** How long each
client trains locally decides what leaks. At 128 examples on the rebuilt corpus, seven AOSP C++
projects against Qt's six, a detector with a project-split reference finds AOSP at the floor of
1,716 groupings at every seed (p = 0.0006, AUC 0.972, 79% of two-client rounds caught at one false
alarm in a hundred) and does not find Qt (median 0.070); a classifier finds neither beyond its
projects (0.583, p 0.30). At 256 examples both are found by the detector, AOSP at AUC 0.995 with
98.5% caught, and the classifier passes the beyond-project control too, 1.000 at the floor of its
null. Composition moves with length, so these are three points on a ladder rather than a
controlled doubling.
Two splits the personalized-adapter literature proposes leave the source readable in the part
they transmit: FedSA-LoRA's A, the best identifier of the factor readings and robust to every
control an adversarial review ran, and PFAdapter's q and k. SDFLoRA's subspace cut is not
established either way: where the signal sits depends on the rank. Masking fails against a
scale-free detector and can help it.

**The second packing landed** (job 149607, analysed 2026-09-19). Same clients, same training seed,
another assignment of examples to clients: the detector's rise with training length replicates
(AOSP 0.364 to 0.419 at one false alarm in a hundred, Qt 0.456 to 0.813) and AOSP's project
permutation stays at the floor, while nearest-class attribution falls from 0.765 to 0.559, below
its own majority. The classifier reading was a packing artefact; the detector reading is not.

**Both rebuilt runs are analysed** (jobs 149957, 149971, 150085, 150086). The 128-example run is
what the registered subspace tests read. Queued for it: projections 150123 and 150124, and the
factor geometries 150133 (A, init subtracted) and 150134 (B), which re-read FedSA-LoRA's cut on the
larger null.
The ladder over training length now has three points and the third saturates the detector.

**The interval's false-positive rate is now an artifact** rather than a comment:
`interval-calibration.json`, 5.7% at 19 changes and 4.7% at 91 against a nominal 5%, measured at
the accuracy the gate operates at.

## RQ2's analysis, end to end

```bash
# clients on a GPU, one initialization; the source list is committed, the corpus is on the cluster
make submit-pinned JOB=client_updates TIME=10:00:00 SBATCH_ARGS=--export=ALL,RUN_TAG=cpp-early,\
CLIENT_SIZE=128,CLIENTS=$HOME/corpus/clients2,SOURCES=scripts/clients-cpp-early.txt
# geometry (product, or one factor with MATRICES=a|b SUBTRACT_INIT=1) and sketches, CPU jobs
make submit-pinned JOB=adapter_geometry SBATCH_ARGS=--export=ALL,PATTERN=<adapters>/*-c*/adapter_model.safetensors
make submit-pinned JOB=adapter_projection SBATCH_ARGS=--export=ALL,PATTERN=...
# attacks, locally; hold content fixed with --content cpp throughout
scripts/client_attribution.py   # nearest class, beyond project
scripts/aggregate_attack.py     # detector, --beyond-project, permutation over projects
scripts/defence_curve.py        # masking, reference split over projects
scripts/masking_mechanism.py    # why a mask can help a cosine detector
scripts/subspace_split.py       # SDFLoRA's cut
scripts/module_split.py         # PFAdapter's cut
```

Every job claims its result file exclusively when it starts, so neither an existing result nor a concurrent job's can be replaced; a job that fails releases its claim, and `OVERWRITE=1` replaces one deliberately.

## Next pickups

- What the adversarial review left open: a fixed rank and a fair baseline for the subspace cut,
  which the registered tests on the rebuilt corpus supply. The permutation's move into
  `aggregate.py` with behavioural tests, its enumeration over distinct groupings, Qt's p over
  seeds and provenance's dirty-tree flag are in.
- FDLoRA and FedDPA's adapter-instance cut, which needs clients training a global and a personal
  adapter jointly.
- The registered subspace tests, which read the rebuilt corpus's clients: the detector on the
  shared half at rank 4, the classifier on the residual at rank 1.

## Waiting on AJ

- Merging PR #13, and when to freeze the windows: freezing is irreversible.
- Venue and authorship, fixed at Stage 1.
- Whether RQ1 is sharpened to "what unit carries the house style, and what makes an organization
  one": AOSP is a coherent unit and Qt is not, and four registered mechanisms for why were each
  refuted, shared code conventions among them. Recommended.
- Whether RQ2 is reframed as a defence evaluation of the personalized-adapter family.
- An acknowledgement for running a sole-authored paper on the lab's `fl-mlm` allocation.
