"""Which part of a reference refinement each of its scored tokens belongs to.

$s$ averages the log-probability of every token of the reference hunk, so an adapter that has
only seen an organization's identifiers and APIs can lead on it without having learned any
convention (review panel item 5). Each target token is classed twice, by what it spells and by
what the revision did to it, and its log-probability is summed into its class and divided by the
example's whole token count. The classes' contributions add up to $s$ to within fp32 rounding
(the total is summed on the device, the parts in float64), so a contrast on $s$ splits into parts
in the same nats per token, with no example dropped and no input changed:

- *identifier* or *other*, by the lexer: a token that touches any character of a name (Pygments'
  `Token.Name`, every subtype) is an identifier; keywords, operators, literals, comments, prose and
  layout are other. A token straddling both counts as an identifier, so *other* is the
  conservative reading of what is left once names are taken out. A file no lexer claims is read
  as text, so its words are other, as prose in a known text format is.
- *changed* or *copied*: the reference is cut into words, runs of space and single symbols within
  each lexeme, aligned with the reviewed hunk's (`difflib`, no junk heuristic), and a token
  touching any piece outside an equal block is changed. Copied tokens are the prior code carried
  forward; a one-word fix inside a comment changes that word, not the comment.
- *end*: the end-of-sequence token, neither.

Naming conventions are conventions too, and they sit in identifier tokens, so *other* bounds what
is learned beyond vocabulary from below. A hunk is a fragment: one that opens inside a string or
docstring can be lexed with its code as string. Each row reports its lexer, its error characters
and `line_disagreement`, the share of its characters whose identifier class changes when each line
is lexed alone, which such a misreading raises.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from pygments.lexer import Lexer
from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
from pygments.token import Error, Name
from pygments.util import ClassNotFound

from sphragis.experiment.likelihood import COMPONENTS

#: Every class a target token takes, the end token last.
CELLS = ("identifier_changed", "identifier_copied", "other_changed", "other_copied", "end")

_OPTIONS = {"stripnl": False, "ensurenl": False, "startinline": True}

#: Suffixes Pygments resolves to the wrong language by filename alone: C++ lexes C headers too,
#: and qmake project files have no lexer (Pygments reads `.pro` as Prolog).
_BY_SUFFIX = {".h": "cpp", ".pro": "text", ".pri": "text", ".prf": "text"}

#: The pieces a lexeme is aligned by: a word, a run of space, or one other character.
_PIECE = re.compile(r"\w+|\s+|[^\w\s]")


def lexer_for(path: str | None) -> Lexer:
    """The lexer for a hunk's file, PHP read without its opening tag since hunks are fragments."""
    suffix = PurePosixPath(path or "").suffix.lower()
    if suffix in _BY_SUFFIX:
        return get_lexer_by_name(_BY_SUFFIX[suffix], **_OPTIONS)
    try:
        return get_lexer_for_filename(path or "", **_OPTIONS)
    except ClassNotFound:
        return get_lexer_by_name("text", **_OPTIONS)


def lexemes(text: str, lexer: Lexer) -> list[tuple[int, Any, str]]:
    """The text's lexemes with their offsets, refused unless they tile it exactly."""
    found = [(i, kind, value) for i, kind, value in lexer.get_tokens_unprocessed(text) if value]
    position = 0
    for start, _, value in found:
        if start != position or text[start : start + len(value)] != value:
            raise ValueError(f"{lexer.name} does not tile the text at offset {position}")
        position += len(value)
    if position != len(text):
        raise ValueError(f"{lexer.name} covers {position} of {len(text)} characters")
    return found


def _pieces(found: list[tuple[int, Any, str]]) -> list[tuple[int, str, bool]]:
    """Each lexeme cut into pieces: offset, text, and whether the lexeme is a name."""
    return [
        (start + m.start(), m.group(), kind in Name)
        for start, kind, value in found
        for m in _PIECE.finditer(value)
    ]


def _identifier_chars(found: list[tuple[int, Any, str]], length: int) -> list[bool]:
    marks = [False] * length
    for start, kind, value in found:
        if kind in Name:
            marks[start : start + len(value)] = [True] * len(value)
    return marks


def char_classes(before: str, after: str, path: str | None) -> tuple[list[str], dict[str, Any]]:
    """Each character of `after` as identifier or other, changed or copied; and how it was lexed."""
    lexer = lexer_for(path)
    found = lexemes(after, lexer)
    old = [piece for _, piece, _ in _pieces(lexemes(before, lexer))]
    new = _pieces(found)
    changed = [True] * len(new)
    matcher = difflib.SequenceMatcher(None, old, [piece for _, piece, _ in new], autojunk=False)
    for block in matcher.get_matching_blocks():
        changed[block.b : block.b + block.size] = [False] * block.size
    classes = [""] * len(after)
    for (start, piece, named), edited in zip(new, changed, strict=True):
        cell = f"{'identifier' if named else 'other'}_{'changed' if edited else 'copied'}"
        classes[start : start + len(piece)] = [cell] * len(piece)
    whole = _identifier_chars(found, len(after))
    by_line: list[bool] = []
    for line in after.splitlines(keepends=True):
        by_line += _identifier_chars(lexemes(line, lexer), len(line))
    return classes, {
        "lexer": lexer.name,
        "lex_error_chars": sum(len(value) for _, kind, value in found if kind in Error),
        "line_disagreement": (
            sum(a != b for a, b in zip(whole, by_line, strict=True)) / len(after) if after else 0.0
        ),
    }


def _merge(cells: set[str]) -> str:
    """A token over several characters: an identifier if any is, changed if any is."""
    kind = "identifier" if any(c.startswith("identifier") for c in cells) else "other"
    return f"{kind}_{'changed' if any(c.endswith('changed') for c in cells) else 'copied'}"


def target_cells(
    tokenizer: Any, row: Mapping[str, Any], target: Sequence[int]
) -> tuple[list[str], dict[str, Any]]:
    """The class of each token of `target`, the row's supervised target, the end token last.

    The reference is tokenized again with offsets, and its ids must be the target's before its
    end token, or the classes would be read against different tokens.
    """
    after = str(row["after"])
    encoded = tokenizer(after, add_special_tokens=False, return_offsets_mapping=True)
    if [*encoded["input_ids"], tokenizer.eos_token_id] != list(target):
        raise ValueError("the reference's offsets are not the supervised target's tokens")
    chars, lexed = char_classes(str(row["before"]), after, row.get("path"))
    cells = []
    for start, end in encoded["offset_mapping"]:
        span = chars[start:end]
        # A token inside one multi-byte character can carry an empty span: read that character.
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
