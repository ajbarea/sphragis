"""Which part of a reference refinement each of its scored tokens belongs to.

$s$ averages the log-probability of every token of the reference hunk, so an adapter that has
only seen an organization's identifiers and APIs can lead on it without having learned any
convention (review panel item 5). Each target token is classed twice, by what it spells and by
what the revision did to it, and its log-probability is summed into its class and divided by the
example's whole token count. The classes' contributions then add up to $s$ exactly, so a contrast
on $s$ splits into parts in the same nats per token, with no example dropped and no input changed:

- *identifier* or *other*, by the lexer: a token that touches any character of a name (Pygments'
  `Token.Name`, every subtype) is an identifier; keywords, operators, literals, comments and
  layout are other. A token straddling both counts as an identifier, so *other* is the
  conservative reading of what is left once names are taken out.
- *changed* or *copied*: the reference's lexemes are aligned with the reviewed hunk's
  (`difflib`, no junk heuristic), and a token touching any lexeme outside an equal block is
  changed. Copied tokens are the prior code carried forward.
- *end*: the end-of-sequence token, neither.

Naming conventions are conventions too, and they sit in identifier tokens, so *other* bounds what
is learned beyond vocabulary from below. A hunk is a fragment, so the lexer can misread one that
opens inside a string or comment; each row reports its lexer and its error characters.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping, Sequence
from typing import Any

from pygments.lexer import Lexer, RegexLexer
from pygments.lexers import get_lexer_for_filename
from pygments.token import Error, Name, Text
from pygments.util import ClassNotFound

#: Every class a target token takes, the end token last.
CELLS = ("identifier_changed", "identifier_copied", "other_changed", "other_copied", "end")

#: The contributions a reading takes, each a sum of cells; identifier, other and end add up to s,
#: and so do changed, copied and end.
COMPONENTS: Mapping[str, tuple[str, ...]] = {
    "identifier": ("identifier_changed", "identifier_copied"),
    "other": ("other_changed", "other_copied"),
    "changed": ("identifier_changed", "other_changed"),
    "copied": ("identifier_copied", "other_copied"),
    "other_changed": ("other_changed",),
    "end": ("end",),
}

#: The per-example field a component's contribution is stored under, beside `logprob_per_token`.
COMPONENT_METRICS = tuple(f"logprob_per_token:{name}" for name in COMPONENTS)


class _Words(RegexLexer):
    """The fallback for a path no Pygments lexer claims: every word is a name."""

    name = "words"
    tokens = {"root": [(r"[A-Za-z_][A-Za-z0-9_]*", Name), (r"\s+", Text), (r".", Text)]}


def lexer_for(path: str | None) -> Lexer:
    """The lexer for a hunk's file, PHP read without its opening tag since hunks are fragments."""
    options = {"stripnl": False, "ensurenl": False, "startinline": True}
    try:
        return get_lexer_for_filename(path or "", **options)
    except ClassNotFound:
        return _Words(**options)


def lexemes(text: str, lexer: Lexer) -> list[tuple[int, Any, str]]:
    """The text's lexemes with their offsets, refused unless they cover it exactly."""
    found = [(i, kind, value) for i, kind, value in lexer.get_tokens_unprocessed(text) if value]
    if "".join(value for _, _, value in found) != text:
        raise ValueError(f"{lexer.name} does not cover the text exactly")
    return found


def char_classes(before: str, after: str, path: str | None) -> tuple[list[str], dict[str, Any]]:
    """Each character of `after` as identifier or other, changed or copied; and how it was lexed."""
    lexer = lexer_for(path)
    old = [value for _, _, value in lexemes(before, lexer)]
    new = lexemes(after, lexer)
    changed = [True] * len(new)
    matcher = difflib.SequenceMatcher(None, old, [value for _, _, value in new], autojunk=False)
    for block in matcher.get_matching_blocks():
        changed[block.b : block.b + block.size] = [False] * block.size
    classes = [""] * len(after)
    errors = 0
    for (start, kind, value), edited in zip(new, changed, strict=True):
        cell = f"{'identifier' if kind in Name else 'other'}_{'changed' if edited else 'copied'}"
        classes[start : start + len(value)] = [cell] * len(value)
        errors += len(value) if kind in Error else 0
    return classes, {"lexer": lexer.name, "lex_error_chars": errors}


def _merge(cells: set[str]) -> str:
    """A token over several characters: an identifier if any is, changed if any is."""
    kind = "identifier" if any(c.startswith("identifier") for c in cells) else "other"
    return f"{kind}_{'changed' if any(c.endswith('changed') for c in cells) else 'copied'}"


def target_cells(tokenizer: Any, row: Mapping[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """The class of each target token `build_supervised` makes for the row, the end token last.

    The reference is tokenized as training tokenizes it, with offsets; the ids must be the
    supervised target's, or the classes would be read against different tokens.
    """
    after = str(row["after"])
    encoded = tokenizer(after, add_special_tokens=False, return_offsets_mapping=True)
    plain = tokenizer(after, add_special_tokens=False)["input_ids"]
    if list(encoded["input_ids"]) != list(plain):
        raise ValueError("offsets changed the tokenization of the reference")
    chars, lexed = char_classes(str(row["before"]), after, row.get("path"))
    cells = []
    for start, end in encoded["offset_mapping"]:
        # A token inside one multi-byte character can carry an empty span: read that character.
        span = chars[start:end]
        if not span and chars:
            span = [chars[min(start, len(chars) - 1)]]
        cells.append(_merge(set(span)) if span else "other_copied")
    return [*cells, "end"], lexed


def split(token_logprobs: Sequence[float], cells: Sequence[str]) -> dict[str, Any]:
    """Each cell's summed log-probability and token count, and each component's contribution."""
    if len(token_logprobs) != len(cells):
        raise ValueError(f"{len(token_logprobs)} log-probabilities for {len(cells)} tokens")
    sums = dict.fromkeys(CELLS, 0.0)
    counts = dict.fromkeys(CELLS, 0)
    for value, cell in zip(token_logprobs, cells, strict=True):
        sums[cell] += value
        counts[cell] += 1
    total = len(cells)
    return {
        "cells": {cell: {"logprob": sums[cell], "tokens": counts[cell]} for cell in CELLS},
        **{
            f"logprob_per_token:{name}": sum(sums[c] for c in members) / total
            for name, members in COMPONENTS.items()
        },
    }
