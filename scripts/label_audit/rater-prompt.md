# Rater instructions

What each model rater was given, verbatim apart from `{scratch}`, the working directory, and
`{rater}`, the rater's own output directory. Rater A ran on Claude Opus 5.5 and rater B on Claude
Sonnet 5, each in a fresh context with no other instructions, and neither could see the other's
labels, the key or the human's checks.

The paragraph forbidding delegation was added for rater B's second run. Its first run split the
batches across four copies of itself and merged their files by hand; those labels were lost with
the machine's temporary directory before they were committed, and the run was repeated as one
rater doing all of its own labelling. Rater A labelled alone in one run.

---

You are an independent annotator in a label-quality audit of a code-review dataset. Work alone and carefully; your labels are compared with another annotator's and with a human's.

First read the rubric, which is the whole of your instructions for judging:
{scratch}/audit-v2/rubric-v2.md

Then read the four item files, in order, and label every item:
{scratch}/audit-v2/batches/batch-1.jsonl (then batch-2.jsonl, batch-3.jsonl, batch-4.jsonl in the same directory). Each line is one JSON item: item, path, comments, context_before, before, after, context_after. Read with the Read tool or a short python print; read long files in parts.

Read ONLY the rubric and these four files. Do not open anything else in that scratch directory, the repository around it, or its parents: other annotators' labels and answer keys live there, and reading them voids your work. No web access.

Do all of the labelling yourself, in this one conversation. Do not start agents, forks, subagents or background tasks, and do not split the work: every label must be your own judgement of the item.

Output: one JSON object at
{scratch}/audit-v2/raters/{rater}/labels.json
mapping every item id to {"label": <one of valid, partial, unrelated_rewrite, non_actionable, context_dependent>, "outside_names": <true|false>, "reason": <at most 20 words, no code quoted>}. Rewrite the file after each batch with everything so far. When done, validate with python (384 keys, labels from the five, outside_names boolean). Reply with only: the count per label, the count of outside_names true, and up to 10 hardest item ids. Include no code or comment text from the items in your reply.
