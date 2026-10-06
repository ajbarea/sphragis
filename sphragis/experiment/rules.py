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

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sphragis.experiment.grid import EvalRun, run_id
from sphragis.experiment.runner import build_prompt, comment_lines

# The file budget, in tokens: one training example's length (training.MAX_SEQ_LENGTH), the
# context one retrieved shot takes at k = 1.
RULES_BUDGET = 2048
# A map chunk's source text, in tokens. It and the map prompt and answer fit the model's 32,768
# with room to spare; a pool of N rows is some ten to fourteen chunks (spec, measured 2026-10-06).
CHUNK_TOKENS = 16_384
# A map answer's budget: a list, not a file. A chunk holds some hundred and fifty reviewed changes,
# so a list may run long; an answer that reaches the budget is flagged and its cut line dropped.
MAP_ANSWER_TOKENS = 1024
# The model's own default system prompt (its chat template, read 2026-10-06). The rules arm
# keeps it and adds the file after it, so the arm differs from the base arm only by the file.
DEFAULT_SYSTEM = "You are Qwen, created by Alibaba Cloud. You are a helpful assistant."

_RULE_FORMAT = (
    "Write one imperative rule a line, each starting with '- '. Give rules only: no headings, "
    "no description of the project, no explanation."
)

MAP_REVIEWS = (
    "Below are changes from one software organization's code review. Each shows the comments "
    "reviewers left on a piece of code and the revised code the author wrote in answer.\n\n"
    "List the coding conventions these reviewers enforce: rules a contributor could follow when "
    "writing new code for this organization. Keep only rules that are specific (naming, "
    "formatting, idioms, error handling, logging, tests, documentation, API use) and that the "
    "revisions applied. " + _RULE_FORMAT + "\n\n{source}"
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
    "Below are lists of coding conventions, each drawn from a different sample of one "
    "organization's code reviews.\n\n"
    "Merge them into one rules file for a contributor. Keep a rule only if it appears, in any "
    "wording, in at least two of the lists. Merge rules that say the same thing, and drop rules "
    "about one particular function or file. Put the most often repeated rules first. "
    + _RULE_FORMAT
    + "\n\n{lists}"
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


def chunks(
    texts: Sequence[str], *, length: Callable[[str], int], budget: int = CHUNK_TOKENS
) -> list[str]:
    """`texts` packed in order into chunks of at most `budget` by `length`, one blank line apart.

    A text longer than the budget is a chunk of its own rather than being cut, since cutting a
    change or a guide section mid-way would hand the map pass half a convention.
    """
    out: list[str] = []
    current: list[str] = []
    used = 0
    for text in texts:
        size = length(text)
        if current and used + size > budget:
            out.append("\n\n".join(current))
            current, used = [], 0
        current.append(text)
        used += size
    if current:
        out.append("\n\n".join(current))
    return out


def rule_lines(text: str) -> list[str]:
    """The rules an answer lists: its lines starting with '- ', as the prompts ask."""
    return [line.strip() for line in text.splitlines() if line.strip().startswith("- ")]


def distil(
    sources: Sequence[str],
    *,
    kind: str,
    generate: Callable[[str, int], str],
    length: Callable[[str], int],
) -> dict[str, Any]:
    """A rules file from `sources` (reviewed changes or guide sections), and every step to it.

    `generate(prompt, max_new_tokens)` is the base model, greedy. The map lists and the reduce
    answer are kept, so the file can be read back to the source it came from.
    """
    if kind not in PROMPTS:
        raise ValueError(f"kind must be one of {sorted(PROMPTS)}, got {kind!r}")
    if not sources:
        raise ValueError("no sources to distil a rules file from")
    map_prompt, reduce_prompt = PROMPTS[kind]
    parts = chunks(sources, length=length)

    def answer(prompt: str, budget: int) -> tuple[str, list[str], bool]:
        # An answer that reached its budget was cut, so its last line may be half a rule.
        text = generate(prompt, budget)
        capped = length(text) >= budget
        lines = rule_lines(text)
        return text, lines[:-1] if capped and lines else lines, capped

    mapped = [answer(map_prompt.format(source=part), MAP_ANSWER_TOKENS) for part in parts]
    joined = "\n\n".join(
        f"List {n}:\n" + "\n".join(lines) for n, (_, lines, _) in enumerate(mapped, 1)
    )
    merged, rules, reduce_capped = answer(reduce_prompt.format(lists=joined), RULES_BUDGET)
    if not rules:
        raise ValueError("the reduce pass listed no rules")
    return {
        "kind": kind,
        "chunks": len(parts),
        "chunk_tokens": [length(part) for part in parts],
        "map_lists": [text for text, _, _ in mapped],
        # Which answers reached their budget: a capped map list lost its tail.
        "map_capped": [capped for _, _, capped in mapped],
        "reduce_answer": merged,
        "reduce_capped": reduce_capped,
        "rules": rules,
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
    return run_id(EvalRun(f"{condition}:{owner}", evaluated, None))


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
