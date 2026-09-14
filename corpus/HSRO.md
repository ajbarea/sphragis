# Human-subjects review: deferred

**Status:** Determination: DEFERRED (2026-09-14, AJ)

`sphragis/corpus/cli.py` parses the line above. `DEFERRED` unlocks `fetch`, the same as a
date would. The gate exists so collection cannot start without this file saying something
deliberate; it does not exist to enforce a particular answer.

## The decision

Collection proceeds without prior review. Revisit when Dr. Reznik raises it, or before the
MSR submission, whichever comes first.

## Why this file still exists

MSR requires a compliance declaration at submission: *"If the research involves human
participants/subjects, the authors must adhere to the ACM Publications Policy on Research
Involving Human Participants and Subjects."* MSR 2026's wording adds that non-compliant
submissions are *"likely to be desk rejected by the PC Chairs without further review."*
That declaration has to be made either way, so the question returns at submission time
whether or not it is answered now. A dated record of a considered deferral is a different
thing to defend than silence.

## What the decision would need, if it is taken up

RIT routes this through the Human Subjects Research Office. Checked 2026-09-14:

- The category is **Exempt, item 4** (secondary analysis of publicly available,
  de-identified data), *not* "Excluded" — RIT's guidance is explicit that public
  de-identified secondary data is exempt research rather than outside their scope.
- Submission is through **Novelution** (`rit.novelution.com` → IRB → Create IRB Protocol).
  Email submissions stopped in December 2025.
- **CITI training is a prerequisite**, and certificates take 24-48h to appear in Novelution.
  That is the long pole, not the form.

## The substance, if a protocol is ever written

No interaction, no intervention, no recruitment. The unit of analysis is a **code change**,
not a person, and no result is reported at the level of an individual. Data is public
Gerrit review history from `review.opendev.org` and `codereview.qt-project.org`, over public
REST with no login and no scraping around an access control.

De-identification is enforced in code rather than promised: `sphragis/corpus/scrub.py` runs
inline inside `fetch`, before the first byte reaches disk, replacing every Gerrit account
object with a salted pseudonym, nulling the identity fields, and sweeping free text for
email addresses. The salt lives in `.env`, is never committed, and is not distributed, so
the pseudonyms are not reversible by anyone holding the corpus. No raw contributor identity
is ever written to disk, and that is a unit-tested property of the pipeline.

Release of derived data remains a separate decision, conditional on each project's code
licence and each Gerrit instance's terms of use.
