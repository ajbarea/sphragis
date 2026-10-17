# How the dataset is built

Each example is a hunk of code at one patch set, the reviewer comments anchored inside it, and the
same hunk at the next patch set: what the developer wrote in answer to the review. The label is
never guessed, since the next patch set is the answer. Most of what a review host holds is not
such a pair, so most of it is removed, by rules fixed before any outcome was read.

## The rules, in order

**Collection.** Only merged changes, created on or after 2024-10-01, after the base model's
release. Each host is read at the pace its robots.txt or written terms set, by one client, and
every account is replaced by a salted pseudonym as the data arrives.

**Comments.** A comment is dropped when it sits on the commit message or on the patch set as a
whole (Gerrit's pseudo-files, not code anyone revised), when the change's own author wrote it (it can describe the edit it precedes), when a bot account wrote it (bots enforce
written rules, not tacit ones), when its whole text is an acknowledgement such as `Done` or
`LGTM`, when it has no line anchor, when no later patch set answered it, or when the next patch
set changed nothing around its line.

**Hunks.** A hunk is dropped when there is nothing on one side of the edit, or when it grew or
shrank past the size rules, since neither is a refinement a model can be asked to make.

**Bots.** A comment from an account the host tags as a bot is dropped when the comment is read.
OpenStack's months were collected before that tag was kept at ingestion, so there the rule could
not run; Wikimedia's build found no inline comment from a tagged account, since its bots post
change-level messages. Some bots comment inline under an ordinary account, so a comment is also
removed when its text matches a template read from the bot's own source (on Qt, its Sanity Bot,
flake8 output and its security-review integration); applied to OpenStack's examples, it found
none.

**Examples.** An example is dropped when its next patch set only rebased the change rather than
reworking it, or when its target was written by the reviewer (Gerrit's suggested edit or its
`Fix applied` reply, already in the prompt), or when only acknowledgements or a bot's template
text are left once those comments are removed. An example whose next patch set's kind was never
recorded is kept without the rebase check and counted.

**Duplicates.** Exact copies, MinHash near duplicates of the normalized before and after text, and
shared boilerplate are removed across the whole organization, so the same pair is never in two
windows, and a change found under two numbers keeps one.

**Fetch errors.** A change whose comments the host would not return, or a comment whose diff it
would not return, is dropped and counted rather than stopping the build.

**Windows.** Examples are split by when the change was created: pilot (2024-10), train (2024-11
to 2025-08) and development (2025-09 to 2025-10). The test window (2025-11 to 2026-10) is defined
and hashed now and fetched only after in-principle acceptance, so it is not counted here.

## What each rule removed

Counts from each organization's frozen build (`datasets/results/dataset-funnel.json`), each row
in its own unit. A comment removed at refinement may belong to an example then dropped for having
nothing else left, so those two steps overlap. A counter a build did not record reads as not
counted. Qt's column is its fetch under the 2026-10-15 grant, built skipping the changes whose
listing records no comment once its comment-count probe held (research log, 2026-10-17).

<!-- funnel:begin (scripts/dataset_funnel.py) -->
| | Unit | OpenStack | Wikimedia | Qt |
|---|---|---:|---:|---:|
| **Merged changes fetched** | changes | 25,191 | 114,857 | 52,892 |
| **Changes the build did not read** | | | | |
| their listing records no comment | changes | 0 | 0 | 27,858 |
| past a grant's monthly read cap | changes | 0 | 0 | 0 |
| **Changes lost before any comment was read** | | | | |
| their comments could not be fetched | changes | 8 | 0 | 0 |
| **Comments dropped** | | | | |
| on the commit message or the patch set as a whole | comments | 42,811 | 46,445 | 57,789 |
| written by the change's author | comments | 18,362 | 23,990 | 31,422 |
| from an account the host tags as a bot | comments | not counted | 0 | 39,068 |
| an acknowledgement (Done, LGTM, thanks, ok and the like) | comments | 2,047 | 3,357 | 2,352 |
| anchored to no line | comments | 86 | 250 | 167 |
| on the last patch set, with no revision after it | comments | 2,695 | 1,856 | 525 |
| its diff could not be fetched | comments | 13 | 0 | 0 |
| outside any hunk the next patch set changed | comments | 9,086 | 13,244 | 18,438 |
| **Hunks dropped as ill-posed** | | | | |
| nothing before or after the edit | hunks | 1,850 | 2,429 | 2,822 |
| grown or shrunk past the size rules | hunks | 332 | 352 | 344 |
| **Examples dropped at refinement** | | | | |
| next patch set only a rebase, not a rework | examples | 0 | 0 | 0 |
| target written by the reviewer (suggested edit, applied fix) | examples | 465 | 1,473 | 172 |
| **Comments removed at refinement** | | | | |
| a bot's template, matched to the bot's own source | comments | 0 | 1 | 392 |
| an acknowledgement | comments | 127 | 0 | 0 |
| **Examples left with no comment** | | | | |
| only acknowledgements were left | examples | 13 | 0 | 0 |
| only a bot's templates were left | examples | 0 | 0 | 264 |
| **Examples removed as duplicates** | | | | |
| exact copies | examples | 249 | 117 | 311 |
| near duplicates (MinHash) | examples | 179 | 69 | 211 |
| shared boilerplate | examples | 0 | 0 | 0 |
| one change in two places | examples | 0 | 0 | 0 |
| **Examples kept, pilot window** | examples | 532 | 582 | 1,147 |
| **Examples kept, train window** | examples | 4,006 | 4,184 | 7,493 |
| **Examples kept, dev window** | examples | 515 | 747 | 809 |
| Examples kept without the rebase check | examples | 0 | 0 | 0 |
| **Examples kept in all** | examples | 5,053 | 5,513 | 9,449 |
<!-- funnel:end -->

## What the rules cannot see

A sample of the kept examples was labeled by hand and by two model raters for whether the
rewrite answers its comment. On OpenStack about four in five do; the rest stay in the corpus and
are scored in every arm, so they dilute a contrast rather than favor one adapter
(`label-audit-v2-openstack.json`, research log 2026-10-15). Edits a rebase brought into the next
patch set also stay; the test window records Gerrit's rebase attribution so both hypotheses can
be read again without them.
