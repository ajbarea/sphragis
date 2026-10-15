# How the dataset is built

Each example is a hunk of code at one patch set, the reviewer comments anchored inside it, and the
same hunk at the next patch set: what the developer wrote in answer to the review. The label is
never guessed, since the next patch set is the answer. Most of what a review host holds is not
such a pair, so most of it is removed, by rules fixed before any outcome was read.

## The rules, in order

**Collection.** Only merged changes, created on or after 2024-10-01, after the base model's
release. Each host is read at the pace its robots.txt or written terms set, by one client, and
every account is replaced by a salted pseudonym as the data arrives.

**Comments.** A comment is dropped when it sits on a file that is not code, when the change's own
author wrote it (it can describe the edit it precedes), when a bot account wrote it (bots enforce
written rules, not tacit ones), when its whole text is an acknowledgement such as `Done` or
`LGTM`, when it has no line anchor, when no later patch set answered it, or when the next patch
set changed nothing around its line.

**Hunks.** A hunk is dropped when there is nothing on one side of the edit, or when it grew or
shrank past the size rules, since neither is a refinement a model can be asked to make.

**Bots.** Most bots on these hosts post change-level messages, which are not inline comments,
and few accounts are tagged as bots. Some bots comment inline under an ordinary account, so a
comment is also removed when its text matches a template read from the bot's own source (on Qt,
its Sanity Bot, flake8 output and its security-review integration).

**Examples.** An example is dropped when its target was written by the reviewer (Gerrit's
suggested edit or its `Fix applied` reply, already in the prompt), or when only acknowledgements
or a bot's template text are left once those comments are removed.

**Duplicates.** Exact copies, MinHash near duplicates of the normalized before and after text, and
shared boilerplate are removed across the whole organization, so the same pair is never in two
windows.

**Windows.** Examples are split by when the change was created: pilot (2024-10), train (2024-11
to 2025-08) and development (2025-09 to 2025-10). The test window (2025-11 to 2026-10) is defined
and hashed now and fetched only after in-principle acceptance, so it is not counted here.

## What each rule removed

Counts from each organization's frozen build (`datasets/results/dataset-funnel.json`). Comment
rows count comments, hunk rows count hunks, and the rest count examples. Qt joins the table once
its fetch under its 2026-10-15 grant is built.

<!-- funnel:begin (scripts/dataset_funnel.py) -->
| | OpenStack | Wikimedia |
|---|---:|---:|
| **Merged changes fetched** | 25,191 | 114,857 |
| **Inline comments dropped** | | |
| on a file that is not code | 42,811 | 46,445 |
| written by the change's author | 18,362 | 23,990 |
| from an account the host tags as a bot | 0 | 0 |
| an acknowledgement (Done, LGTM, Acknowledged) | 2,047 | 3,357 |
| anchored to no line | 86 | 250 |
| on the last patch set, with no revision after it | 2,695 | 1,856 |
| outside any hunk the next patch set changed | 9,086 | 13,244 |
| lost to a fetch error | 21 | 0 |
| **Hunks dropped as ill-posed** | | |
| nothing before or after the edit | 1,850 | 2,429 |
| grown or shrunk past the size rules | 332 | 352 |
| **Comments removed at refinement** | | |
| a bot's template, matched to the bot's own source | 0 | 1 |
| an acknowledgement inside a kept example | 127 | 0 |
| **Examples dropped at refinement** | | |
| target written by the reviewer (suggested edit, applied fix) | 465 | 1,473 |
| only acknowledgements left | 13 | 0 |
| only a bot's template left | 0 | 0 |
| revision not a rework of the change | 0 | 0 |
| revision of unknown kind | 0 | 0 |
| an address left in the text | 0 | 0 |
| **Examples removed as duplicates** | | |
| exact copies | 249 | 117 |
| near duplicates (MinHash) | 179 | 69 |
| shared boilerplate | 0 | 0 |
| one change in two places | 0 | 0 |
| **Examples kept, pilot window** | 532 | 582 |
| **Examples kept, train window** | 4,006 | 4,184 |
| **Examples kept, dev window** | 515 | 747 |
| **Examples kept in all** | 5,053 | 5,513 |
<!-- funnel:end -->

## What the rules cannot see

A sample of the kept examples was labeled by hand and by two model raters for whether the
rewrite answers its comment. On OpenStack about four in five do; the rest stay in the corpus and
are scored in every arm, so they dilute a contrast rather than favor one adapter
(`label-audit-v2-openstack.json`, research log 2026-10-15). Edits a rebase brought into the next
patch set also stay; the test window records Gerrit's rebase attribution so both hypotheses can
be read again without them.
