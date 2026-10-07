"""A rules file: the comparator that asks whether declared conventions recover what adapters learn.

Coding agents take an organization's conventions from a rules file (AGENTS.md and its kin)
loaded into their context. This builds two per arm, by one fixed pipeline over the pinned
distiller (`model.DISTILLER_ID`), read by the evaluated model: distilled from a half's pool of
reviewed changes, or from the organization's written coding guide. A map pass lists the
conventions in each chunk of the source (a guide's, a page at a time); for reviews a reduce pass
merges the lists, and for a guide its pages' rules are taken a page at a time in turn
(`guide_file`), into one file under the caps (`capped_file`). Design of record:
`docs/superpowers/specs/2026-10-05-rules-file-comparator-design.md`.

Every prompt here is fixed before any distillation runs, and a change to one is a change to the
comparator, recorded in the research log.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Callable, Mapping, Sequence
from itertools import zip_longest
from pathlib import Path
from typing import Any

from sphragis.experiment.runner import comment_lines
from sphragis.experiment.training import MAX_SEQ_LENGTH, render_chat

# The file budget, in tokens: one training example's length (training.MAX_SEQ_LENGTH), the
# context one retrieved shot takes at k = 1.
RULES_BUDGET = MAX_SEQ_LENGTH
# A map chunk's source text, in the distiller's tokens. It and the map prompt and answer fit
# the distiller's context with room to spare; a pool of N rows is some ten to fourteen chunks
# (spec, measured 2026-10-06).
CHUNK_TOKENS = 16_384
# A map list's length, and the evidence a mined rule needs: a second smoke run (2026-10-06, job
# 223034) listed tools no organization's reviews had named ("use black", "use mypy"), ten of
# thirteen answers ran to their budget, and the merge kept 179 rules. A rule must cite two or
# more of its chunk's changes, checked here rather than asked of the model, and a file keeps at
# most forty, which fits its budget.
MAX_MAP_RULES = 15
MIN_CITED = 2
MAX_FILE_RULES = 40
# A merged rule is kept only if it cites a list, so the merge adds no rule the lists lacked, and
# the most-cited come first. Recurrence is the map's: a rule there stands on two or more of its
# chunk's changes. A merge left to apply "in two or more lists" applied it to one half and not
# the other (jobs 223605, 223606: 52 rules and 1), and counted here it left one to five rules a
# half (jobs 224850, 224851), since conventions that are a project's own seldom recur across
# chunks. The merge's answer carries citations, so it gets twice a file's budget; the file
# itself is cut to the budget. A half whose lists hold no rule with its evidence is refused.
MIN_CITED_LISTS = 1
REDUCE_ANSWER_TOKENS = 2 * RULES_BUDGET
# A map answer's budget: a list, not a file. A chunk holds some hundred and fifty reviewed changes,
# so a list may run long; an answer that reaches the budget is flagged and its cut line dropped.
MAP_ANSWER_TOKENS = 1024

# A first smoke run (2026-10-06, job 223030) mined mostly practice any project follows ("use
# meaningful variable names", "follow PEP 8"), so the review prompts ask for what is particular
# to this organization; amended before any arm was scored. A written guide is the organization's
# own declaration, so its prompts keep every rule it states.
_PARTICULAR = (
    "Leave out general good practice that any project would follow (meaningful names, PEP 8, "
    "writing tests, handling errors). Keep conventions particular to this organization, and "
    "name the identifiers, functions, libraries, file formats or wording each concerns. "
)

_RULE_FORMAT = (
    "Write one imperative rule a line, each starting with '- '. Give rules only: no headings, "
    "no description of the project, no explanation."
)

MAP_REVIEWS = (
    "Below are numbered changes from one software organization's code review. Each shows the "
    "comments reviewers left on a piece of code and the revised code the author wrote in "
    "answer.\n\n"
    "List the coding conventions these reviewers enforce: rules a contributor could follow when "
    "writing new code for this organization, about naming, formatting, idioms, logging, "
    "documentation, or the use of its own APIs and libraries, that the revisions applied. "
    + _PARTICULAR
    + f"List at most {MAX_MAP_RULES} rules, each shown by two or more of the changes, and end each "
    "rule with the numbers of the changes that show it in brackets, as in '- Rule. [3, 17]'. "
    + _RULE_FORMAT
    + "\n\n{source}"
)

MAP_GUIDE = (
    "Below is part of one software organization's written coding conventions.\n\n"
    "List the coding conventions it states: rules a contributor could follow when writing new "
    "code for this organization. Keep only rules that are specific (naming, formatting, "
    "idioms, error handling, logging, tests, documentation, API use). "
    + _RULE_FORMAT
    + "\n\n{source}"
)

# Mined rules are promoted on recurrence (a reviewer's call that repeats across changes), as
# industrial rule mining does (Qodo Rule Miner, 2026-07); a written guide states each rule once,
# so it has no merge (`guide_file`).
REDUCE_REVIEWS = (
    "Below are numbered lists of coding conventions, each drawn from a different sample of one "
    "organization's code reviews.\n\n"
    "Merge them into one list for a contributor: merge rules that say the same thing into one, "
    "keep every rule, merged or not, and end each rule with the numbers of the lists it appears "
    "in, in brackets, as in '- Rule. [2, 7]'. " + _PARTICULAR + _RULE_FORMAT + "\n\n{lists}"
)

PROMPTS = {
    "reviews": (MAP_REVIEWS, REDUCE_REVIEWS),
    # A guide states each rule once: no merge, its pages' lists are selected from in code
    # (`guide_file`).
    "guide": (MAP_GUIDE, None),
}


def paragraphs(text: str) -> list[str]:
    """A guide's paragraphs, blank-line separated, the units its chunks are packed from."""
    return [block.strip() for block in text.split("\n\n") if block.strip()]


def review_text(row: Mapping[str, Any]) -> str:
    """One reviewed change as a map pass reads it: the comments, the code, the revision."""
    return (
        f"Review comments:\n{comment_lines(row)}\n\nCode:\n{row['before']}\n\n"
        f"Revised code:\n{row['after']}\n"
    )


def packed(
    texts: Sequence[str],
    *,
    length: Callable[[str], int],
    budget: int = CHUNK_TOKENS,
    separator: int | None = None,
) -> list[list[str]]:
    """`texts` packed in order into groups of at most `budget` by `length`, the separator joining
    them (`chunks`, `numbered`) counted between each two.

    A text longer than the budget is a group of its own rather than being cut, since cutting a
    change or a guide section mid-way would hand the map pass half a convention.
    """
    out: list[list[str]] = []
    current: list[str] = []
    used = 0
    # The separator's own length, given apart when `length` counts more than a text's tokens.
    separator = length("\n\n") if separator is None else separator
    for text in texts:
        size = length(text)
        if current and used + separator + size > budget:
            out.append(current)
            current, used = [], 0
        used += separator + size if current else size
        current.append(text)
    if current:
        out.append(current)
    return out


def chunks(
    texts: Sequence[str], *, length: Callable[[str], int], budget: int = CHUNK_TOKENS
) -> list[str]:
    """`texts` packed in order into chunks of at most `budget`, one blank line apart."""
    return ["\n\n".join(group) for group in packed(texts, length=length, budget=budget)]


def review_chunks(sources: Sequence[str], *, length: Callable[[str], int]) -> list[list[str]]:
    """Reviewed changes packed into chunks, the header each gets counted at its widest."""
    return packed(
        sources,
        length=lambda t: length(f"### Change {len(sources)}\n{t}"),
        separator=length("\n\n"),
    )


def recurring(
    lines: Sequence[str], *, lists: Sequence[Sequence[str]], length: Callable[[str], int]
) -> tuple[list[str], list[list[int]]]:
    """The merged rules grounded in `MIN_CITED_LISTS` or more of the `lists` they cite (each a
    non-empty map list, numbered from 1 as the merge saw them), most-cited first (ties by where
    each rule's first grounded copy stands in the merge), as a file within the caps; and the
    lists each is grounded in.

    A citation counts only where a rule of that list supports the merged rule (`grounded`), so a
    rule the merge invented and labelled with a list number is dropped.
    """
    sources = [[content_words(r) for r in rules] for rules in lists]
    merged: dict[str, tuple[str, set[int], int]] = {}
    # A rule the merge wrote twice is kept once, as its first grounded copy reads and where that
    # copy stands, citing every list any copy is grounded in; copies are told apart by their
    # words, case, spacing and a closing period aside. The threshold holds for them together.
    for order, line in enumerate(lines):
        rule, numbers = cited(line, len(lists))
        words = content_words(rule)
        backed = {n for n in numbers if any(supports(words, w) for w in sources[n - 1])}
        if backed:
            same = _same_rule(rule)
            first, cites, at = merged.get(same, (rule, set(), order))
            merged[same] = (first, cites | backed, at)
    ranked = [
        (rule, sorted(cites))
        for rule, cites, _ in sorted(merged.values(), key=lambda r: (-len(r[1]), r[2]))
        if len(cites) >= MIN_CITED_LISTS
    ]
    kept = capped_file([rule for rule, _ in ranked], length=length)
    return kept, [backed for _, backed in ranked[: len(kept)]]


def _same_rule(rule: str) -> str:
    """A rule as copies of it compare: lower-cased, code too (as `content_words` reads it),
    spacing collapsed, no closing period."""
    return " ".join(rule.lower().split()).rstrip(" .")


def guide_file(
    pages: Sequence[Sequence[str]], *, length: Callable[[str], int]
) -> tuple[list[str], list[int]]:
    """A written guide's file from its pages' rules (each page's map lists, in order): copies
    kept once (`_same_rule`), then chosen a page at a time in turn, so the caps fall on every page
    alike rather than on the pages that come last, and written in the guide's order; and the page
    (from 1) each kept rule is from.

    A guide's merge kept every rule in source order, so the cap left Wikimedia's file holding
    only its first pages' rules (smoke job 226890, 2026-10-06); its language pages had none."""
    seen: set[str] = set()
    queues: list[list[tuple[str, int]]] = []
    for page, rules in enumerate(pages, 1):
        queue = []
        for rule in rules:
            if _same_rule(rule) not in seen:
                seen.add(_same_rule(rule))
                queue.append((rule, page))
        queues.append(queue)
    # The turns choose which rules the caps keep; the file then reads in the guide's own order,
    # each page's rules together.
    turns = [item for round_ in zip_longest(*queues) for item in round_ if item is not None]
    kept = set(capped_file([rule for rule, _ in turns], length=length))
    ordered = [(rule, page) for queue in queues for rule, page in queue if rule in kept]
    # The cut was made in turn order; the file in page order must fit the budget as well.
    rules = capped_file([rule for rule, _ in ordered], length=length)
    return rules, [page for _, page in ordered[: len(rules)]]


def capped_file(rules: Sequence[str], *, length: Callable[[str], int]) -> list[str]:
    """At most `MAX_FILE_RULES` rules, in order, cut from the end until the file fits
    `RULES_BUDGET` by `length`: the caps every file is held to, mined or written."""
    kept = list(rules[:MAX_FILE_RULES])
    while kept and length("\n".join(kept)) > RULES_BUDGET:
        kept.pop()
    return kept


# Function, imperative, connective and auxiliary words every rule uses, and the names every
# Python file holds (`self`, `None`), which say nothing of a rule's convention. Contractions are
# split off first (`_CONTRACTION`), so "don't" is "do".
# fmt: off
_FUNCTION = frozenset((
    "a", "an", "the", "to", "of", "in", "on", "for", "and", "or", "not", "no", "is", "are", "be",
    "by", "as", "at", "it", "its", "with", "from", "that", "this", "these", "those", "use",
    "using", "used", "instead", "rather", "than", "when", "which", "where", "there", "into",
    "over", "only", "must", "should", "always", "never", "avoid", "prefer", "make", "sure",
    "each", "such", "their", "do", "does", "any", "all", "new", "old", "more", "less", "eg", "ie",
    "through", "via", "if", "but", "can", "may", "per", "you", "your", "so", "then", "also",
    "them", "they", "we", "our", "one", "could", "would", "has", "have", "had", "were", "was",
    "need", "let", "will", "none", "self", "cls", "true", "false", "null",
))
# fmt: on
_TOKEN = re.compile(r"[a-z0-9_]+")
_CONTRACTION = re.compile(r"n't\b|'(?:s|re|ll|ve|d|m)\b")
# One shared word is not support: a merged rule is grounded only on two or more content words.
MIN_RULE_WORDS = 2


def content_words(rule: str) -> frozenset[str]:
    """A rule's content words: lower-cased word tokens, code and prose alike (identifier parts
    kept whole, so `oslo_log` is one; numbers kept, so 2 and 4 differ), without function words
    or single letters."""
    return frozenset(
        w
        for w in _TOKEN.findall(_CONTRACTION.sub(" ", rule.lower().replace("\u2019", "'")))
        if (len(w) > 1 or w.isdigit()) and w not in _FUNCTION
    )


def supports(words: frozenset[str], source: frozenset[str]) -> bool:
    """Whether a source rule's content words hold more than half of a merged rule's, which has
    at least `MIN_RULE_WORDS`."""
    return len(words) >= MIN_RULE_WORDS and 2 * len(words & source) > len(words)


def grounded(rule: str, source: str) -> bool:
    """Whether a merged rule is supported by a source rule (`supports`). On the smoke files
    (research log, 2026-10-06) every merged rule's best cited source holds more than half of its
    words, most all of them; a rule invented outright ("use black for formatting") shares at
    most half with any, and so does nearly every rule against the other organization's lists.
    The cases every review raised are pinned in `tests/unit/experiment/test_rules.py`.

    Support is lexical: a rule that inverts its source ("use print" from "not print"), or
    changes one value in it, shares its words and passes. None of the smoke merges does, read
    rule by rule; a polarity test was tried and refused faithful rules ("instead of" against
    "rather than")."""
    return supports(content_words(rule), content_words(source))


def numbered(group: Sequence[str]) -> str:
    """A chunk of reviewed changes, each headed by its number in the chunk, from 1."""
    return "\n\n".join(f"### Change {n}\n{text}" for n, text in enumerate(group, 1))


_CITED = re.compile(r"\s*\[([\d,\s]+)\]\s*\.?\s*$")


def cited(line: str, changes: int) -> tuple[str, set[int]]:
    """A rule line without its bracketed change numbers, and the numbers that name a change."""
    match = _CITED.search(line)
    if not match:
        return line, set()
    numbers = {int(n) for n in re.findall(r"\d+", match.group(1))}
    return line[: match.start()].rstrip(), {n for n in numbers if 1 <= n <= changes}


def rule_lines(text: str) -> list[str]:
    """The rules an answer lists: its lines starting with '- ', as the prompts ask."""
    return [line.strip() for line in text.splitlines() if line.strip().startswith("- ")]


def distil(
    sources: Sequence[str],
    *,
    kind: str,
    generate: Callable[[str, int], tuple[str, bool]],
    length: Callable[[str], int],
    file_length: Callable[[str], int] | None = None,
) -> dict[str, Any]:
    """A rules file from `sources` (reviewed changes, or a guide's pages), and every step to it.

    `generate(prompt, max_new_tokens)` is the distiller, greedy: its answer, and whether decoding
    stopped at the budget. `length` counts the distiller's tokens, for chunks; `file_length` the
    evaluated model's, which reads the file, for the file's budget (`length` when omitted). The
    map lists and the merge's answer are kept, so the file reads back to its sources.
    """
    file_length = file_length or length
    if kind not in PROMPTS:
        raise ValueError(f"kind must be one of {sorted(PROMPTS)}, got {kind!r}")
    if not sources:
        raise ValueError("no sources to distil a rules file from")
    map_prompt, reduce_prompt = PROMPTS[kind]
    # The page each chunk of a guide is from: a guide is mapped a page at a time, so no page sits
    # in another's capped tail.
    part_pages: list[int] = []
    if kind == "reviews":
        groups = review_chunks(sources, length=length)
        parts = [numbered(group) for group in groups]
    else:
        parts = []
        for page, text in enumerate(sources, 1):
            for part in chunks(paragraphs(text), length=length):
                parts.append(part)
                part_pages.append(page)

    def answer(prompt: str, budget: int) -> tuple[str, list[str], bool]:
        # An answer that reached its budget was cut, so its last line may be half a rule.
        raw, capped = generate(prompt, budget)
        text = answer_text(raw)
        lines = rule_lines(text)
        return text, lines[:-1] if capped and lines else lines, capped

    mapped = [answer(map_prompt.format(source=part), MAP_ANSWER_TOKENS) for part in parts]
    evidence: list[dict[str, int]] = []
    if kind == "reviews":
        # A mined rule stands on two or more of its chunk's changes, counted here.
        kept_lists = []
        for (text, lines, capped), group in zip(mapped, groups, strict=True):
            kept = []
            # The first MAX_MAP_RULES as listed: the limit is the prompt's, held here.
            for line in lines[:MAX_MAP_RULES]:
                rule, numbers = cited(line, len(group))
                if len(numbers) >= MIN_CITED:
                    kept.append(rule)
            kept_lists.append((text, kept, capped))
            evidence.append({"listed": len(lines), "kept": len(kept)})
        mapped = kept_lists
        if not any(kept for _, kept, _ in mapped):
            raise ValueError("no list holds a rule with its evidence")
    rule_pages: list[int] = []
    merge_chunks: list[int] | None = None
    if reduce_prompt is not None:
        # Only lists that hold a rule go to the merge, numbered as it sees them, so no citation
        # can name an empty one.
        merge_chunks = [n for n, (_, lines, _) in enumerate(mapped, 1) if lines]
        merge_lists = [mapped[n - 1][1] for n in merge_chunks]
        joined = "\n\n".join(
            f"List {n}:\n" + "\n".join(lines) for n, lines in enumerate(merge_lists, 1)
        )
        merged, lines, reduce_capped = answer(
            reduce_prompt.format(lists=joined), REDUCE_ANSWER_TOKENS
        )
        rules, recurrence = recurring(lines, lists=merge_lists, length=file_length)
    else:
        # No merge ran: none of its fields is recorded as if one had.
        merged, reduce_capped, recurrence = None, None, []
        by_page: list[list[str]] = [[] for _ in sources]
        for (_, lines, _), page in zip(mapped, part_pages, strict=True):
            by_page[page - 1].extend(lines)
        rules, rule_pages = guide_file(by_page, length=file_length)
    if not rules:
        raise ValueError("no rules for the file")
    return {
        "kind": kind,
        "chunks": len(parts),
        "chunk_tokens": [length(part) for part in parts],
        "map_lists": [text for text, _, _ in mapped],
        # Which answers reached their budget: a capped map list lost its tail.
        "map_capped": [capped for _, _, capped in mapped],
        # Per review chunk: rules listed, and kept for citing two or more of its changes.
        "evidence": evidence,
        "reduce_answer": merged,
        "reduce_capped": reduce_capped,
        # For reviews, the chunk each list the merge saw came from, by the merge's numbering:
        # list n is chunk merge_chunks[n - 1] of map_lists and evidence. None for a guide.
        "merge_chunks": merge_chunks,
        # Per kept mined rule, the lists the merge cited for it.
        "recurrence": recurrence,
        # For a guide: each chunk's page, and each kept rule's (from 1, in the guide's order).
        "part_pages": part_pages,
        "rule_pages": rule_pages,
        "rules": rules,
        "pipeline": pipeline(),
        "file": "\n".join(rules),
        # In the evaluated model's tokens: the context the file takes in the arm.
        "file_tokens": file_length("\n".join(rules)),
    }


_THINK = re.compile(r"<think>.*?</think>", re.S)


def answer_text(raw: str) -> str:
    """A distiller answer as the pipeline reads it: an empty think block, left by a template with
    thinking off, is not part of it."""
    return _THINK.sub("", raw).strip()


def distiller_signature(model: str, dtype: Any, template: Mapping[str, Any]) -> str:
    """What wrote a file: the distiller at its revision, its dtype, and the chat-template options
    it was prompted under (thinking off), so a file distilled under others is told apart."""
    return f"{model}|{dtype}|template={json.dumps(dict(template), sort_keys=True)}"


def pipeline() -> str:
    """A fingerprint of everything a file's distillation depends on besides its sources and the
    distiller (`distiller_signature`): this module's source, which holds the prompts, budgets,
    splitting and parsing; the code it renders reviews and chat turns with (`comment_lines`,
    `render_chat`); and the budget's value (`MAX_SEQ_LENGTH`). A file made under another is not
    this comparator's. The generator's own decoding code is pinned by the commit each file's
    provenance records."""
    parts = [
        Path(__file__).read_text(),
        inspect.getsource(comment_lines),
        inspect.getsource(render_chat),
        str(MAX_SEQ_LENGTH),
    ]
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def pinned(guide: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A guide snapshot's pages as a distilled file records them: title, revision and hash."""
    return [{key: s[key] for key in ("title", "revision", "sha256")} for s in guide["sources"]]
