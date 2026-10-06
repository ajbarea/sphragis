"""A rules file: the comparator that asks whether declared conventions recover what adapters learn.

Coding agents take an organization's conventions from a rules file (AGENTS.md and its kin)
loaded into their context. This builds two per arm, by one fixed pipeline over the pinned
distiller (`model.DISTILLER_ID`), read by the evaluated model: distilled from a half's pool of
reviewed changes, or from the organization's written coding guide. A map pass lists the
conventions in each chunk of the source; a reduce pass merges the lists into one file under the
caps (`capped_file`). Design of record:
`docs/superpowers/specs/2026-10-05-rules-file-comparator-design.md`.

Every prompt here is fixed before any distillation runs, and a change to one is a change to the
comparator, recorded in the research log.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from sphragis.experiment.runner import comment_lines
from sphragis.experiment.training import MAX_SEQ_LENGTH

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
# itself is cut to the budget. A pool that fills fewer than three chunks is refused.
MIN_CITED_LISTS = 1
REDUCE_ANSWER_TOKENS = 2 * RULES_BUDGET
MIN_REVIEW_CHUNKS = 3
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
# so its merge keeps every rule and only the budget cuts.
REDUCE_REVIEWS = (
    "Below are numbered lists of coding conventions, each drawn from a different sample of one "
    "organization's code reviews.\n\n"
    "Merge them into one list for a contributor: merge rules that say the same thing into one, "
    "keep every rule, merged or not, and end each rule with the numbers of the lists it appears "
    "in, in brackets, as in '- Rule. [2, 7]'. " + _PARTICULAR + _RULE_FORMAT + "\n\n{lists}"
)

REDUCE_GUIDE = (
    "Below are lists of coding conventions, each drawn from a different part of one "
    "organization's written coding conventions.\n\n"
    "Merge them into one rules file for a contributor. Merge rules that say the same thing, and "
    "keep the rest in the order given. " + _RULE_FORMAT + "\n\n{lists}"
)

PROMPTS = {
    "reviews": (MAP_REVIEWS, REDUCE_REVIEWS),
    "guide": (MAP_GUIDE, REDUCE_GUIDE),
}


def review_text(row: Mapping[str, Any]) -> str:
    """One reviewed change as a map pass reads it: the comments, the code, the revision."""
    return (
        f"Review comments:\n{comment_lines(row)}\n\nCode:\n{row['before']}\n\n"
        f"Revised code:\n{row['after']}\n"
    )


def packed(
    texts: Sequence[str], *, length: Callable[[str], int], budget: int = CHUNK_TOKENS
) -> list[list[str]]:
    """`texts` packed in order into groups of at most `budget` by `length`.

    A text longer than the budget is a group of its own rather than being cut, since cutting a
    change or a guide section mid-way would hand the map pass half a convention.
    """
    out: list[list[str]] = []
    current: list[str] = []
    used = 0
    for text in texts:
        size = length(text)
        if current and used + size > budget:
            out.append(current)
            current, used = [], 0
        current.append(text)
        used += size
    if current:
        out.append(current)
    return out


def chunks(
    texts: Sequence[str], *, length: Callable[[str], int], budget: int = CHUNK_TOKENS
) -> list[str]:
    """`texts` packed in order into chunks of at most `budget`, one blank line apart."""
    return ["\n\n".join(group) for group in packed(texts, length=length, budget=budget)]


def review_chunks(sources: Sequence[str], *, length: Callable[[str], int]) -> list[list[str]]:
    """Reviewed changes packed into chunks, the header each gets counted at its widest, refused
    if fewer than `MIN_REVIEW_CHUNKS`, since recurrence across lists needs that many."""
    groups = packed(sources, length=lambda t: length(f"### Change {len(sources)}\n{t}"))
    if len(groups) < MIN_REVIEW_CHUNKS:
        raise ValueError(
            f"{len(groups)} chunks of reviews; recurrence across lists needs {MIN_REVIEW_CHUNKS}"
        )
    return groups


def recurring(
    lines: Sequence[str], *, lists: Sequence[Sequence[str]], length: Callable[[str], int]
) -> tuple[list[str], list[list[int]]]:
    """The merged rules grounded in `MIN_CITED_LISTS` or more of the `lists` they cite (each a
    non-empty map list, numbered from 1 as the merge saw them), most-cited first (ties in the
    merge's order), as a file within the caps; and the lists each is grounded in.

    A citation counts only where the merged rule shares wording with a rule of that list
    (`grounded`), so a rule the merge invented and labelled with a list number is dropped.
    """
    kept_rules = []
    for order, line in enumerate(lines):
        rule, numbers = cited(line, len(lists))
        backed = sorted(n for n in numbers if any(grounded(rule, r) for r in lists[n - 1]))
        if len(backed) >= MIN_CITED_LISTS:
            kept_rules.append((-len(backed), order, rule, backed))
    # A rule the merge wrote twice is kept once, at its best-cited copy.
    ranked, seen = [], set()
    for _, _, rule, backed in sorted(kept_rules):
        if rule not in seen:
            seen.add(rule)
            ranked.append((rule, backed))
    kept = capped_file([rule for rule, _ in ranked], length=length)
    return kept, [backed for _, backed in ranked[: len(kept)]]


def capped_file(rules: Sequence[str], *, length: Callable[[str], int]) -> list[str]:
    """At most `MAX_FILE_RULES` rules, in order, cut from the end until the file fits
    `RULES_BUDGET` by `length`: the caps every file is held to, mined or written."""
    kept = list(rules[:MAX_FILE_RULES])
    while kept and length("\n".join(kept)) > RULES_BUDGET:
        kept.pop()
    return kept


# Words too common in rules to tie one to another (only words of four or more letters are read).
# fmt: off
_COMMON = frozenset((
    "using", "used", "instead", "rather", "than", "with", "when", "from", "that", "this", "these",
    "those", "only", "must", "should", "avoid", "prefer", "always", "never", "code", "each",
    "such", "into", "over", "their", "which", "where", "there", "make", "sure", "file", "files",
    "line", "lines", "name", "names", "value", "values"
))
# fmt: on
_WORD = re.compile(r"[a-z][a-z0-9_]{3,}")
_TICKED = re.compile(r"`([^`]+)`")
# An identifier: a dotted or snake_case name, or a camelCase or PascalCase one.
_IDENTIFIER = re.compile(r"[A-Za-z]\w*[_.]\w[\w.]*|[a-z]+[A-Z]\w*|[A-Z][a-z0-9]+[A-Z]\w*")
# Ticked words a rule shares with many that say nothing of the convention.
_TRIVIAL = frozenset({"none", "true", "false", "self", "null", "this", "cls"})


def _names(text: str) -> set[str]:
    """The identifiers a rule names: ticked spans and identifier-shaped words, lower-cased,
    without the trivial ones."""
    found = {t.strip().lower() for t in _TICKED.findall(text)}
    found |= {w.lower() for w in _IDENTIFIER.findall(text)}
    return {n for n in found if len(n) >= 3 and n not in _TRIVIAL}


def grounded(rule: str, source: str) -> bool:
    """Whether a merged rule shares wording with a source rule: an identifier both name (ticked
    or identifier-shaped, beyond `None` and its kin), or two words of four or more letters
    beyond the common ones."""
    if _names(rule) & _names(source):
        return True
    words = set(_WORD.findall(rule.lower())) - _COMMON
    return len(words & (set(_WORD.findall(source.lower())) - _COMMON)) >= 2


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
    """A rules file from `sources` (reviewed changes or guide sections), and every step to it.

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
    if kind == "reviews":
        groups = review_chunks(sources, length=length)
        parts = [numbered(group) for group in groups]
    else:
        groups = packed(sources, length=length)
        parts = ["\n\n".join(group) for group in groups]

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
        lists = sum(1 for _, kept, _ in mapped if kept)
        if lists < MIN_REVIEW_CHUNKS:
            raise ValueError(
                f"{lists} lists hold a rule with its evidence; recurrence needs {MIN_REVIEW_CHUNKS}"
            )
    # Only lists that hold a rule go to the merge, numbered as it sees them, so no citation can
    # name an empty one.
    merge_chunks = [n for n, (_, lines, _) in enumerate(mapped, 1) if lines]
    merge_lists = [mapped[n - 1][1] for n in merge_chunks]
    joined = "\n\n".join(
        f"List {n}:\n" + "\n".join(lines) for n, lines in enumerate(merge_lists, 1)
    )
    if kind == "reviews":
        merged, lines, reduce_capped = answer(
            reduce_prompt.format(lists=joined), REDUCE_ANSWER_TOKENS
        )
        rules, recurrence = recurring(lines, lists=merge_lists, length=file_length)
    else:
        merged, lines, reduce_capped = answer(reduce_prompt.format(lists=joined), RULES_BUDGET)
        rules, recurrence = capped_file(lines, length=file_length), []
    if not rules:
        raise ValueError("the reduce pass listed no rules")
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
        # The chunk each list the merge saw came from, by the merge's numbering: list n is
        # chunk merge_chunks[n - 1] of map_lists and evidence.
        "merge_chunks": merge_chunks,
        # Per kept mined rule, the lists the merge cited for it.
        "recurrence": recurrence,
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
    """A fingerprint of everything a file's distillation depends on besides its sources and
    model: this module's source, which holds the prompts, every budget and the parsing. A file
    made under another is not this comparator's."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def pinned(guide: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A guide snapshot's pages as a distilled file records them: title, revision and hash."""
    return [{key: s[key] for key in ("title", "revision", "sha256")} for s in guide["sources"]]
