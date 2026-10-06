"""A rules file: the comparator that asks whether declared conventions recover what adapters learn.

Coding agents take an organization's conventions from a rules file (AGENTS.md and its kin)
loaded into their context. This builds two per arm, by one fixed pipeline over the base model:
distilled from a half's pool of reviewed changes, or from the organization's written coding
guide. A map pass lists the conventions in each chunk of the source; a reduce pass merges the
lists into one file under the budget. Design of record:
`docs/superpowers/specs/2026-10-05-rules-file-comparator-design.md`.

Every prompt here is fixed before any distillation runs, and a change to one is a change to the
comparator, recorded in the research log.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from sphragis.experiment.grid import conditioned
from sphragis.experiment.runner import build_prompt, comment_lines
from sphragis.experiment.training import MAX_SEQ_LENGTH, render_chat

# The file budget, in tokens: one training example's length (training.MAX_SEQ_LENGTH), the
# context one retrieved shot takes at k = 1.
RULES_BUDGET = MAX_SEQ_LENGTH
# A map chunk's source text, in tokens. It and the map prompt and answer fit the model's 32,768
# with room to spare; a pool of N rows is some ten to fourteen chunks (spec, measured 2026-10-06).
CHUNK_TOKENS = 16_384
# A map list's length, and the evidence a mined rule needs: a second smoke run (2026-10-06, job
# 223034) listed tools no organization's reviews had named ("use black", "use mypy"), ten of
# thirteen answers ran to their budget, and the merge kept 179 rules. A rule must cite two or
# more of its chunk's changes, checked here rather than asked of the model, and a file keeps at
# most forty, which fits its budget.
MAX_MAP_RULES = 15
MIN_CITED = 2
MAX_FILE_RULES = 40
# A merged rule is kept only if it cites two or more lists, counted here rather than asked of the
# model: a third smoke run (jobs 223605, 223606) applied "in two or more lists" to one half and
# not the other (52 rules and 1). Recurrence over lists reads as such only when there are more
# than two, so a pool that fills fewer chunks is refused. The merge's answer carries citations,
# so it gets twice a file's budget; the file itself is cut to the budget.
MIN_CITED_LISTS = 2
REDUCE_ANSWER_TOKENS = 2 * RULES_BUDGET
MIN_REVIEW_CHUNKS = 3
# A map answer's budget: a list, not a file. A chunk holds some hundred and fifty reviewed changes,
# so a list may run long; an answer that reaches the budget is flagged and its cut line dropped.
MAP_ANSWER_TOKENS = 1024
# The model's own default system prompt (its chat template, read 2026-10-06). The rules arm
# keeps it and adds the file after it, so the arm differs from the base arm only by the file.
DEFAULT_SYSTEM = "You are Qwen, created by Alibaba Cloud. You are a helpful assistant."

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
    lines: Sequence[str], *, lists: int, length: Callable[[str], int]
) -> tuple[list[str], list[list[int]]]:
    """The merged rules citing `MIN_CITED_LISTS` or more of the `lists`, most-cited first (ties
    in the merge's order), at most `MAX_FILE_RULES`, cut from the end to the file budget; and the
    lists each cites."""
    cited_rules = []
    for order, line in enumerate(lines):
        rule, numbers = cited(line, lists)
        if len(numbers) >= MIN_CITED_LISTS:
            cited_rules.append((-len(numbers), order, rule, sorted(numbers)))
    kept = sorted(cited_rules)[:MAX_FILE_RULES]
    while kept and length("\n".join(rule for _, _, rule, _ in kept)) > RULES_BUDGET:
        kept.pop()
    return [rule for _, _, rule, _ in kept], [numbers for _, _, _, numbers in kept]


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
) -> dict[str, Any]:
    """A rules file from `sources` (reviewed changes or guide sections), and every step to it.

    `generate(prompt, max_new_tokens)` is the base model, greedy: its answer, and whether
    decoding stopped at the budget. The map lists and the reduce
    answer are kept, so the file can be read back to the source it came from.
    """
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
        text, capped = generate(prompt, budget)
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
    joined = "\n\n".join(
        f"List {n}:\n" + "\n".join(lines) for n, (_, lines, _) in enumerate(mapped, 1)
    )
    if kind == "reviews":
        merged, lines, reduce_capped = answer(
            reduce_prompt.format(lists=joined), REDUCE_ANSWER_TOKENS
        )
        rules, recurrence = recurring(lines, lists=len(mapped), length=length)
    else:
        merged, rules, reduce_capped = answer(reduce_prompt.format(lists=joined), RULES_BUDGET)
        recurrence = []
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
        # Per kept mined rule, the lists the merge cited for it.
        "recurrence": recurrence,
        "rules": rules,
        "pipeline": pipeline(),
        "file": "\n".join(rules),
        "file_tokens": length("\n".join(rules)),
    }


def rules_system(rules_file: str) -> str:
    """The system turn of a rules arm: the model's default, then the rules file."""
    return f"{DEFAULT_SYSTEM}\n\nFollow these conventions of the organization:\n{rules_file}"


def rules_prompt(example: Mapping[str, Any], rules_file: str) -> list[dict[str, str]]:
    """A rules arm's prompt: the file in the system turn, the registered prompt as the user's."""
    return [
        {"role": "system", "content": rules_system(rules_file)},
        {"role": "user", "content": build_prompt(example)},
    ]


# A distilled file is conditioned on a half, as an adapter is; a written one on an organization.
DISTILLED = "rules-distilled"
WRITTEN = "rules-written"


def rules_key(condition: str, owner: str, evaluated: str) -> str:
    """`<condition>:<owner>|<evaluated>`: the adapter arms' key, the file's owner in the adapter's
    place, with no training seed."""
    return conditioned(condition, owner, evaluated)


def rules_arms(
    targets: Sequence[Mapping[str, Any]],
    files: Mapping[tuple[str, str], str],
    *,
    evaluated: str,
) -> dict[str, list[list[dict[str, str]]]]:
    """Every target's prompt under every rules file, keyed as `rules_key`.

    `files` maps (condition, owner) to the file: (`DISTILLED`, half) or (`WRITTEN`, organization).
    """
    return {
        rules_key(condition, owner, evaluated): [rules_prompt(t, text) for t in targets]
        for (condition, owner), text in files.items()
    }


def pipeline() -> str:
    """A fingerprint of everything a file's distillation depends on besides its sources and
    model: this module's source, which holds the prompts, every budget and the parsing. A file
    made under another is not this comparator's."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def pinned(guide: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A guide snapshot's pages as a distilled file records them: title, revision and hash."""
    return [{key: s[key] for key in ("title", "revision", "sha256")} for s in guide["sources"]]


def default_system_holds(tokenizer: Any) -> bool:
    """Whether the model's chat template, given no system turn, renders `DEFAULT_SYSTEM`: so a
    rules arm, which writes the default then the file, differs from the base arm by the file."""
    implicit = render_chat(tokenizer, "U")
    explicit = render_chat(
        tokenizer, [{"role": "system", "content": DEFAULT_SYSTEM}, {"role": "user", "content": "U"}]
    )
    return implicit == explicit
