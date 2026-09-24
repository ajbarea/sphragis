<div align="center">

# Sphragis

### Can a model learn an organization's house style from the code it reviews?

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
docs/                       the documentation site: the question, the registered
                            decisions, the outcome-neutral tests, the artifact index
docs/research-log.md                                       the dated record
docs/superpowers/specs/                                    the design of record
docs/superpowers/plans/                                    task-by-task execution plans
```

## Quick start

```bash
make sync          # install
make test          # the suite
make lint          # ruff format + ruff check + ty
make docs          # build the documentation site into site/
```

## Documentation

The site is what a reader who is not going to read the code needs: the question and the
gate, every decision that is fixed before the seal opens with the evidence that chose it,
the checks that have to pass before the gate is read at all, and an index of every
committed measurement with the script that wrote it.

| page | holds |
|---|---|
| `docs/index.md` | the question, the directional hypothesis, the pass rule, the seal |
| `docs/registered-decisions.md` | every registered choice, and the measurement behind it |
| `docs/outcome-neutral.md` | what must hold for the study to be interpretable |
| `docs/artifacts.md` | generated: every artifact under `datasets/results/` and its writer |
| `docs/research-log.md` | the dated record, superseded readings included |

`make docs-serve` renders it locally with live reload. It is not published: this repository
is private and the test window is sealed, so the workflow builds the site on every push and
the publish step waits on that decision.

Every figure on the site is asserted against the artifact that produced it. `make
docs-harvest` fails on a number that has drifted from its measurement or on a stale artifact
index, and the test suite runs the same check, so a page cannot quietly outlive the
apparatus.

## Building a corpus

```bash
export SPHRAGIS_CORPUS_SALT=$(python -c "import secrets; print(secrets.token_hex(32))")

uv run python -m sphragis.corpus fetch  --org openstack --month 2024-10
uv run python -m sphragis.corpus build  --org openstack
uv run python -m sphragis.corpus dedup  --org openstack   # reports, writes nothing
uv run python -m sphragis.corpus split  --org openstack   # reports, writes nothing
uv run python -m sphragis.corpus freeze --org openstack
uv run python -m sphragis.corpus verify --org openstack
uv run python -m sphragis.corpus verify --org openstack --reproduce   # reruns dedup and split
```

`dedup` and `split` deliberately only report, so the corpus can be inspected before
anything is committed. `freeze` is the single stage that writes windows to disk, and
`verify` re-derives every window's hash and fails on drift. With `--reproduce` it also reruns
dedup and split from the refined examples and compares each window with its frozen file byte for
byte, which catches a change to code the rule digests do not cover.

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
make submit JOB=rq1 CLUSTER=sporc TIME=08:00:00 SBATCH_ARGS=--export=ALL,RUN_TAG=sporc-a100
make submit JOB=rq1 SBATCH_ARGS=--export=ALL,MODE=windows
```

`CLUSTER` is `tigris`, `sporc` or `sporc-h100`. Scripts keep their TIGRIS `#SBATCH` lines
and `submit` overrides them on the command line. Their `--time` values were measured on a
GH200, so pass `TIME` elsewhere. Off TIGRIS every result and adapter path gets the cluster
name as a suffix, so a run there never overwrites a GH200 result; `RUN_TAG` names it instead.
`SEEDS` and `TRAIN_SIZE` add `-s<seeds>` and `-n<size>` after it in the scripts that take them,
so another seed or size never overwrites the default run. Every result a job script writes
records its cluster, job and GPU,
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
