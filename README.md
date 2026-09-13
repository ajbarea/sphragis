<div align="center">

# Sphragis

### Does an organization leave a learnable fingerprint in the code it reviews?

*The corpus, the measurement, and the experiment behind a pre-registered study of whether a
coding agent adapted to one organization's review history learns that organization's
conventions, and what a declared boundary protects once it can.*

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![uv](https://img.shields.io/badge/uv-package_manager-DE5FE9?style=flat-square)](https://docs.astral.sh/uv/)
[![Registered Report](https://img.shields.io/badge/MSR_2027-registered_report-B5179E?style=flat-square)](https://conf.researchr.org/track/msr-2027/msr-2027-registered-reports)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

</div>

---

## What is this?

A coding agent becomes useful once it knows how an organization writes software: its internal
APIs, its error-handling patterns, its naming rules, what its reviewers reject. None of that is
in public training data. Organizations would gain from pooling what generalizes, but pooling
risks exposing proprietary practice.

That whole direction rests on one unverified premise: **that there is something
organization-specific to learn in the first place.** Sphragis tests the premise before anything
is built on it.

The study is confirmatory and its pass rule is fixed in advance, in code, in
[`sphragis/measure/stats.py`](sphragis/measure/stats.py). It is written as a registered report
so a null result publishes: the gate exists in order to be allowed to fail.

## Why "Sphragis"

A *sphragis* is a seal: the mark pressed into wax that shows whose a thing is and holds it
closed. Classical poets used one to stamp their identity into a work, and Theognis set his on
his verses so no one else could pass them off as their own. This repository asks whether an
organization leaves the same kind of mark in the code it reviews, and what a declared boundary
protects once that mark can be learned.

The apparatus also seals, literally: the confirmatory test window is defined and hashed before
anyone may collect it.

## Layout

```text
sphragis/
  corpus/      fetch, scrub, build, dedup, split, freeze   the review corpus
  measure/     score, stats, contamination                 the instruments the gate reads
  provenance.py                                            commit, versions, platform
corpus/HSRO.md                                             human-subjects determination
docs/superpowers/specs/                                    the design of record
docs/superpowers/plans/                                    task-by-task execution plans
```

## Quick start

```bash
make sync          # install
make test          # the suite
make lint          # ruff format + ruff check + ty
```

```bash
uv run python -m sphragis.corpus fetch
# no HSRO determination in corpus/HSRO.md. Repository mining is human-subjects
# research; record the determination before collecting.
```

That refusal is the design. Collection cannot start before the determination is on file, and
the gate is a runtime check rather than a note in a document.

## Two invariants

**The measurement never needs a GPU.** No module under `sphragis/measure/` may import torch,
transformers, peft or datasets, and a test enforces it in a clean interpreter. The
outcome-neutral checks halt the study when they fail, and a halt condition that can only be
exercised on a cluster is one that never fires.

**Dependencies are pinned, not floated.** This is a paper artifact that has to reproduce years
from now. That is the opposite policy from a testbed that rides its framework's latest release,
which is why this is its own repository rather than a directory inside one.

## Status

Corpus construction and measurement are built and tested. The experiment, the pilot power
analysis and the model stack arrive with plan C. See [ROADMAP.md](ROADMAP.md).
