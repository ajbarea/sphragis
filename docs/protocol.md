---
title: Protocol
description: A pre-registered test of whether an organization's house style is learnable from the code it reviews.
---

# Protocol

**Can a model learn an organization's house style from the code it reviews?**
{ .hero-subtitle }

A coding agent becomes useful to an organization once it knows how that organization writes
software: its internal APIs, its error-handling patterns, its naming rules, what its
reviewers reject. Software engineering calls this tacit knowledge, and being unwritten is
exactly why it is absent from public training data. Organizations would gain from pooling
whatever part of it generalizes, but pooling risks exposing proprietary practice.

That tension motivates a research direction, and the direction rests on a premise nobody has
tested: that there is something organization-specific to learn in the first place. Prior work
establishes learnable structure at the repository and the contributor level. Whether an
*organization*, a set of repositories under one review culture, is a learnable unit is open.

Sphragis is the apparatus that tests the premise, as a pre-registered go/no-go gate rather
than as a system contribution. It is the code behind an MSR 2027 Registered Report, Stage 1.

## The question

**RQ1.** Is there measurable **organization-specific adaptation** in code review? That is:
does a LoRA adapter trained on one organization's review history outperform, on that
organization's held-out refinements, an adapter trained on a different organization's review
history?

The hypothesis is directional and stated per organization. For organization `O` with
counterpart `O'`:

```text
g(O) = EM(adapter_O  on O_test)
     - EM(adapter_O' on O_test)

H1: g(O) >  0
H0: g(O) <= 0
```

`EM` is exact match, aggregated per change. The hypothesis is tested independently for
OpenStack and for Qt.

Both outcomes are informative and neither is argued for. Support for `H1` in both
organizations establishes that an organization-level signal exists and is learnable, which is
the premise the wider direction requires. Failure to support it means the premise does not
hold at this unit of analysis under this design, and the report pre-commits what the
direction becomes in that case.

## What the question is not

The question is whether a model learns conventions it can *apply*, not whether a classifier
can separate organizations by any available signal. The evidence is therefore code hunks and
the inline review comments anchored inside them, and commit metadata is excluded by design. A
classifier given commit messages would very likely separate these organizations more easily,
and it would be answering a different question: source attribution rather than
organization-specific adaptation. A null here is consequently a null about
learnable-and-applicable conventions, which is the premise the direction actually rests on,
rather than a claim that the organizations are indistinguishable.

Source attribution is the study's second question, RQ2, and it asks what a declared boundary
protects once a house style can be learned.

## The gate

The pass rule is fixed in advance, in code, in
[`sphragis/experiment/decomposition.py`](https://github.com/ajbarea/sphragis/blob/main/sphragis/experiment/decomposition.py).

**H1 passes only when every admitted organization's cell is supported.** RQ1 claims that
organizations have a learnable house style, which is a generality claim, and a rule passing on
one organization does not support it. H1 and H2 share one family-wise level under Holm, read on
97.5% and 95% intervals. A cell is supported when its lower bound is strictly above zero, bounded when its
upper bound is below the effect the design detects, and inconclusive otherwise. The interval is
the crossed run-by-change bootstrap over K admissible partitions, and the estimand is pooled.
Each of those was chosen before the run that would have been decided by it, and each is
recorded with the evidence that chose it on [Registered decisions](registered-decisions.md).

Before the gate is read at all, the [outcome-neutral tests](outcome-neutral.md) have to pass.
They are checks on the apparatus rather than on the hypothesis, and a failure halts the study
and is reported as an apparatus failure rather than as a result.

## The corpus, and the seal

Public Gerrit instances mined for hunk-level refinement pairs anchored to inline reviewer
comments, deduplicated, pseudonymised at ingestion, and split into windows by change. H1's
confirmatory cells are Gerrit organizations (OpenStack and Wikimedia admitted; Qt and Chromium on
permission); GitHub organizations, collected through an adapter that shapes pull requests as
Gerrit changes, are a registered replication family read beside them ([registered
decisions](registered-decisions.md)). The table below is the original OpenStack and Qt pilot.

The scrub replaces Gerrit account objects and sweeps addresses out of review comment text. It
leaves the diff payload alone on purpose, because rewriting anything in the source that merely
looks like an address would alter the code the study measures. An address written *inside* a
config file or a DNS record therefore survives into the corpus, and the published artifacts are
redacted separately: `scripts/redact_identities.py` removes third-party addresses from
everything under `datasets/results/`, and a test scans every committed file for one.

| window | OpenStack | Qt |
|---|---|---|
| pilot | 532 | 1,146 |
| train | 4,006 | 7,494 |
| dev | 515 | 805 |
| **test** | **sealed** | **sealed** |
| total collected | 5,053 | 9,445 |

Corpus v2, frozen 2026-09-23 after the Stage 1 data audit: comments written by bots and one-click
"Acknowledged" replies are removed. The audit and what it changed are in the research log.

Corpus v3, frozen 2026-09-28: examples whose target the reviewer wrote, through Gerrit's "Suggest
edit" or its "Fix applied." reply, are removed, since the target is then already in the prompt
and the share of such examples grows from the training window to the development window.

The test window is defined and hash-sealed now and fetched only after in-principle
acceptance, no earlier than three months after the window's final month. Its content hash in
both frozen manifests is the hash over no content:

```text
openstack  e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
qt         e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

`fetch` refuses the sealed window and every month after it, so the seal is enforced by the
apparatus rather than by discipline.

## Where it stands

The confirmatory contrast has not been run. What exists is a dev-window reading, and the dev
window is the most censored window in the corpus, so it is a pre-registration estimate rather
than an unbiased preview.

OpenStack's H1 cell over K = 24 admissible partitions, one training seed each, on the
development window's 206 changes:

| Holm level | H1 estimate | interval | verdict |
|---|---|---|---|
| 97.5% | +0.0049 | [-0.0088, +0.0185] | inconclusive |
| 95% | +0.0049 | [-0.0073, +0.0166] | inconclusive |

The development window holds about a tenth of the test window's projected changes, so an inconclusive
reading here says the design needs the test window, not that the effect is absent.

## What is on this site

- [Registered decisions](registered-decisions.md): every choice fixed before the seal opens,
  with the measurement that chose it.
- [Outcome-neutral tests](outcome-neutral.md): what has to hold for the study to be
  interpretable at all, and the pilot evidence that each one can fire.
- [Artifact index](artifacts.md): every committed measurement, and the script that wrote it.
- [Research log](log/index.md): the dated record, including the readings that were
  withdrawn and why.

Every figure on this site is asserted against the artifact that produced it. `make
docs-harvest` fails on a number that has drifted from its measurement, and the test suite
runs the same check.
