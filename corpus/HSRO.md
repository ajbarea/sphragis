# HSRO determination request (DRAFT — not yet submitted)

**Status:** Determination: PENDING

`sphragis/corpus/cli.py` parses the line above. It unlocks `fetch` only when it reads
`Determination: YYYY-MM-DD`. Do not write a date here until RIT's Human Subjects Research
Office returns one.

---

## What is being requested

A **Not Human Subjects Research (NHSR)** determination, not an exempt-review application.

The likely basis: analysis of de-identified, publicly available data is generally held not
to constitute human subjects research. Every element of that test is met here by
construction, and the construction is enforced in code rather than promised in prose. The
office makes the determination; this document exists so it can make it quickly.

Mining software repositories is nonetheless treated as touching human subjects in this
field, because the repositories record developers' interactions with each other (Gold and
Krinke, *Ethics in the mining of software repositories*, EMSE). That is why a
determination is being sought at all rather than assumed.

## Study

Whether a language-model adapter trained on one organization's code review history learns
that organization's conventions rather than general review skill. Two organizations,
OpenStack and Qt.

No interaction with any person. No intervention. No recruitment. No survey, interview, or
observation of behavior arranged by the researcher. The data already exists and was
created by contributors in the ordinary course of public open-source development.

## Data

| | |
|---|---|
| Source | `review.opendev.org` (OpenStack) and `codereview.qt-project.org` (Qt) |
| Access | public REST, no login, no credential, no scraping around a control |
| Records | change metadata, patch sets, inline review comments, per-revision diffs |
| Window | changes created on or after 2024-10-01 |
| Unit of analysis | a code hunk and the review comments on it, paired with its rewrite |

The unit of analysis is a **code change**, not a person. No research question concerns any
individual, and no result is reported at the level of a person.

## De-identification, enforced in code

`sphragis/corpus/scrub.py` runs inline inside `fetch`, before the first byte reaches disk.
It replaces every Gerrit account object with a single salted pseudonym and nulls the
identity fields (`name`, `email`, `username`, `display_name`, `secondary_emails`,
`avatars`), and sweeps free text for email addresses. The salt lives in `.env`, is never
committed, and is not distributed, so the pseudonyms are not reversible by a recipient.

No raw contributor identity is ever written to disk. This is a unit-tested property of the
pipeline, not a handling procedure someone has to remember.

## Publication and release

- No contributor identity is published, in any form, at any stage.
- No result is reported at the level of an individual.
- Code and manifests are released. Release of derived data is a separate decision,
  conditional on each project's code license and each Gerrit instance's terms of use,
  and is not part of this request.

## What would change this request

If the study later analyzes individual reviewers (for example, reviewer behavior or
reviewer identification), it stops being about code changes and a new determination is
required. It does not currently do that, and the direction spec explicitly dropped the
client-inclusion question.

## To submit

1. Confirm the current RIT HSRO intake route and form for an NHSR determination.
2. Ask Dr. Reznik how the lab has handled repository data previously, so this matches lab
   precedent rather than inventing a route.
3. Attach the spec `docs/superpowers/specs/2026-09-13-gerrit-review-corpus-harness-design.md`
   if the office wants the pipeline detail.
4. On a determination, replace the status line above with `Determination: YYYY-MM-DD` and
   record the office's reference number below.

**Reference number:** _pending_
