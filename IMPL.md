# Sphragis — Active Implementation Log

What is being built right now. The dated record of findings, numbers and corrections is
`docs/research-log.md`; milestones, open decisions and the Completed log are `ROADMAP.md`.

---

## In flight

**PR #13** (`feat/wellposed-filter`), open for AJ: the whole apparatus, from corpus to gate and
both RQ2 threat models. Its description was rewritten on 2026-09-18 against current evidence.

**RQ1, dev window, three seeds, fp32:** Qt +0.0316 [+0.0089, +0.0567], OpenStack +0.0230
[+0.0000, +0.0457], mixed. The test window stays sealed until in-principle acceptance.

**RQ2 has become a defence evaluation.** On 43 C++ clients from one initialization:
organization is not a class a classifier can assign, but a detector with a project-split
reference finds AOSP (p = 0.012 over 84 groupings), and at 128 examples a client finds Qt too.
Two splits the personalized-adapter literature proposes leave the source readable in the part
they transmit: FedSA-LoRA's A, the best identifier of the factor readings and robust to every
control an adversarial review ran, and PFAdapter's q and k. SDFLoRA's subspace cut is not
established either way: where the signal sits depends on the rank. Masking fails against a
scale-free detector and can help it.

**Running:** job 149607, a second packing at 128 examples a client; the local AOSP ten-project
rebuild, detached, about half an hour a month. 256 examples a client waits for that rebuild, since
AOSP's system/core cannot fill one client at that size.

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

- The rest of the adversarial review: the permutation moved into `aggregate.py` with behavioural
  tests, enumeration over distinct groupings, Qt's p reported over seeds, a fixed rank and fair
  baseline for the subspace cut, and a dirty-tree flag in provenance.
- FDLoRA and FedDPA's adapter-instance cut, which needs clients training a global and a personal
  adapter jointly.
- Sensitivity analysis into the Stage 1 skeleton's section 5, replacing the withdrawn power figure.

## Waiting on AJ

- Merging PR #13, and when to freeze the windows: freezing is irreversible.
- Venue and authorship, fixed at Stage 1.
- Whether RQ1 is sharpened to "what unit carries the fingerprint, and what makes an organization
  one": the evidence now says an organization is detectable when it imposes one set of code
  conventions across its projects, which AOSP does and Qt does not. Recommended.
- Whether RQ2 is reframed as a defence evaluation of the personalized-adapter family.
- An acknowledgement for running a sole-authored paper on the lab's `fl-mlm` allocation.
