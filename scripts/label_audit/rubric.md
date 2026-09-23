# Label audit rubric, version 2

The rubric the raters and the human checker applied (research log, 2026-09-23). Version 1 folded
two questions into context_dependent, whether the request can be understood and whether the
rewrite needs names the item does not show; version 2 asks them separately.

The raters' copy used a corpus example as its worked example: item-001 of the v2 sample, which
is therefore excluded from every agreement figure. This copy replaces it with an invented one of
the same shape, so that no corpus text is committed.

Each item pairs a code hunk BEFORE a revision, the reviewer comment(s) anchored in that hunk, and the same hunk AFTER the author's next revision ("the rewrite"). Unchanged surrounding lines are context_before / context_after (may be empty).

You answer two separate questions per item.

QUESTION 1: the label. Is this a real instance of "the reviewer asked for a change, and the rewrite is the author's answer to it"? Exactly one of:
- valid: the comment asks for a change and the rewrite makes it. Extra edits alongside are fine.
- partial: the rewrite makes part of what was asked, or makes it in a clearly different way than the comment suggested.
- unrelated_rewrite: the requested change is absent; the hunk changed for some other reason.
- non_actionable: the comment asks for no change (praise, FYI, agreement, a question the author could answer in words), or the comment itself withdraws the request.
- context_dependent: you cannot tell what the comment is asking for from what is shown. For example "same here", "see above", "as discussed", a reference to other lines, other files, earlier patch sets or another thread, a bare "?" or "why?", "remove" without saying what, or only a link.

Rules for question 1:
1. Several comments on one item: judge the actionable requests together. All made: valid. Some: partial. None: unrelated_rewrite. Ignore purely non-actionable comments when others are actionable.
2. context_dependent is about understanding the REQUEST, not about writing the rewrite. If the request is clear from what is shown, the item is not context_dependent, even when the rewrite uses names defined elsewhere. That case is question 2.
3. A deletion the comment asked for is valid. A question the author answers by changing the code counts as a request.
4. Do not guess the organization, project or author; judge only what is shown.

QUESTION 2: outside_names, true or false, answered for every item whatever its label. Does the rewrite use a name or a value that appears nowhere in the item: not in the before hunk, the context, or the comment? Examples: a helper function, constant, module, class, config key, file name, version string or hash defined elsewhere. A name the comment itself spells out counts as shown. New local variable names and plain rewording do not count; ordinary library or language names do not count (open, len, std::move, os.path).

Worked example. Comment: "This string is compared against in other modules; please make it a constant." Before: status = "Service unavailable". After: status = errors.SERVICE_UNAVAILABLE. The request is clear and the rewrite makes it, so question 1 is valid. The module errors and the constant's name appear nowhere in the item, so question 2 is true.
