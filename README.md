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
docs/superpowers/specs/                                    the design of record
docs/superpowers/plans/                                    task-by-task execution plans
```

## Quick start

```bash
make sync          # install
make test          # the suite
make lint          # ruff format + ruff check + ty
```

## Building a corpus

```bash
export SPHRAGIS_CORPUS_SALT=$(python -c "import secrets; print(secrets.token_hex(32))")

uv run python -m sphragis.corpus fetch  --org openstack --month 2024-10
uv run python -m sphragis.corpus build  --org openstack
uv run python -m sphragis.corpus dedup  --org openstack   # reports, writes nothing
uv run python -m sphragis.corpus split  --org openstack   # reports, writes nothing
uv run python -m sphragis.corpus freeze --org openstack
uv run python -m sphragis.corpus verify --org openstack
```

`dedup` and `split` deliberately only report, so the corpus can be inspected before
anything is committed. `freeze` is the single stage that writes windows to disk, and
`verify` re-derives every window's hash and fails on drift.

`fetch` and `build` are both resumable: an existing snapshot or an already-built month is
skipped unless `--overwrite` is passed. This matters more than it sounds. A month of
OpenStack is 24 requests; the same month of Qt is 387, because Qt caps a page at ten
changes regardless of what you ask for.

The salt is what makes the pseudonyms irreversible and stable. Without a stable salt a
corpus built today will not compare with one built tomorrow, so the commands refuse to run
without it.

## Developing the model code on a local GPU

The registered 7B needs ~17 GB and will not fit a typical desktop card, but the model code
can be developed against `Qwen2.5-Coder-1.5B` on anything with ~6 GB.

```bash
make gpu-local                                   # the experiment extra: cu130 torch on Linux
uv run --no-sync --extra experiment python -m sphragis.experiment.model \
    --smoke --model-id Qwen/Qwen2.5-Coder-1.5B-Instruct
```

`--no-sync` matters: the extra is not a default, so a plain `uv run` re-syncs it away.

## Running on the clusters

Jobs run on RIT Research Computing under the `fl-mlm` project account. TIGRIS (aarch64,
GH200 96 GB) is the default target; SPORC (x86_64, A100 40 GB, or H100 80 GB) is reached
through the same TIGRIS login and checkout. Both mount one `$HOME`, so uv and the venv are
built per machine type (`.venv-aarch64`, `.venv-x86_64`).

```bash
make deploy                                      # cluster checkout = this pushed commit
make cluster-env CLUSTER=sporc                   # once per machine type
make submit JOB=rq1 CLUSTER=sporc TIME=08:00:00 SBATCH_ARGS=--export=ALL,TAG=sporc-a100
make submit JOB=rq1 SBATCH_ARGS=--export=ALL,MODE=windows
```

`CLUSTER` is `tigris`, `sporc` or `sporc-h100`. Scripts keep their TIGRIS `#SBATCH` lines
and `submit` overrides them on the command line. Their `--time` values were measured on a
GH200, so pass `TIME` elsewhere. For `rq1`, a `TAG` gives a run on other hardware its own
result and adapter paths; the other scripts write fixed paths in `$HOME`. Every result a job script writes records its cluster, job and GPU,
including peak GPU memory, which training also logs as each adapter finishes: keep one result
set on one GPU type.

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
