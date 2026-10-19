"""Which part of a reference refinement each of its scored tokens belongs to.

$s$ averages the log-probability of every token of the reference hunk, so an adapter that has
only seen an organization's identifiers and APIs can lead on it without having learned any
convention (review panel item 5). Each target token is classed twice, by what it spells and by
what the revision did to it, and its log-probability is summed into its class and divided by the
example's whole token count. The classes' contributions add up to $s$ to within fp32 rounding
(the total is summed on the device, the parts in float64), so a contrast on $s$ splits into parts
in the same nats per token, with no example dropped and no input changed:

- *identifier* or *other*, by the lexer: a token that touches any character of a name (Pygments'
  `Token.Name`, every subtype but the language's builtins, so configuration keys and markup roles
  count) is an identifier; keywords, builtins, operators, literals, comments, prose and layout are
  other. A token straddling both counts as an identifier, so *other* is the conservative reading
  of what is left once names are taken out. Only the languages these corpora hold are lexed
  (`LEXED`, with the suffixes Pygments misreads pinned in `_BY_SUFFIX`); any other file, and any
  row a lexer cannot tile, is read as text, so its words are other, as prose is.
- *changed* or *copied*: the reference's lines are aligned with the reviewed hunk's, and within
  each run of replaced lines its words, runs of space and single symbols are aligned with the old
  lines' (`difflib`, no junk heuristic), as a word diff inside a line diff; a token touching any
  piece outside an equal block is changed. Copied tokens are the prior code carried forward; a
  one-word fix inside a comment changes that word, not the comment.
- *end*: the end-of-sequence token, neither.

Naming conventions are conventions too, and they sit in identifier tokens, so *other* bounds what
is learned beyond vocabulary from below. A hunk is a fragment: one that opens inside a string or
docstring can be lexed with its code as string. Each row reports its lexer, its error characters
and `line_disagreement`, the share of its characters whose identifier class changes when each line
is lexed alone, which such a misreading raises; a sensitivity reading drops rows with either.
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

#: Suffixes Pygments resolves to the wrong language by filename alone, measured on these corpora:
#: C++ lexes C headers too; qmake files have no lexer (`.pro` reads as Prolog); OpenStack's `.inc`
#: are reStructuredText includes (POVRay otherwise), `.pl` and `.t` are Perl (Prolog, TADS 3),
#: `.sql` is not Transact-SQL, and MediaWiki's `.tpl` templates are not Smarty.
_BY_SUFFIX = {
    ".h": "cpp",
    ".pro": "text",
    ".pri": "text",
    ".prf": "text",
    ".inc": "rst",
    ".pl": "perl",
    ".t": "perl",
    ".sql": "sql",
    ".tpl": "text",
}

#: The languages lexed: those these corpora hold (and Qt's QML and CMake). A file Pygments
#: resolves to anything else is read as text rather than trusted to an unchecked lexer.
LEXED = frozenset(
    {
        "Bash",
        "C",
        "C++",
        "CMake",
        "CSS",
        "Debian Control file",
        "Diff",
        "Docker",
        "Gettext Catalog",
        "Go",
        "HTML",
        "INI",
        "JSON",
        "JSON5",
        "JSX",
        "Java",
        "JavaScript",
        "LessCss",
        "Lua",
        "Markdown",
        "PHP",
        "Perl",
        "Properties",
        "Puppet",
        "Python",
        "QML",
        "Ruby",
        "SQL",
        "Scala",
        "TOML",
        "Text only",
        "TypeScript",
        "Vue",
        "XML",
        "YAML",
        "YAML+Jinja",
        "reStructuredText",
    }
)

#: The pieces a lexeme is aligned by: a word, a run of space, or one other character.
_PIECE = re.compile(r"\w+|\s+|[^\w\s]")


def lexer_for(path: str | None) -> Lexer:
    """The lexer for a hunk's file, PHP read without its opening tag since hunks are fragments."""
    suffix = PurePosixPath(path or "").suffix.lower()
    if suffix in _BY_SUFFIX:
        return get_lexer_by_name(_BY_SUFFIX[suffix], **_OPTIONS)
    try:
        lexer = get_lexer_for_filename(path or "", **_OPTIONS)
    except ClassNotFound:
        return get_lexer_by_name("text", **_OPTIONS)
    return lexer if lexer.name in LEXED else get_lexer_by_name("text", **_OPTIONS)


def _named(kind: Any) -> bool:
    """A name the code chose, not one the language supplies."""
    return kind in Name and kind not in Name.Builtin


def _cell(named: bool, edited: bool) -> str:
    return f"{'identifier' if named else 'other'}_{'changed' if edited else 'copied'}"


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
        (start + m.start(), m.group(), _named(kind))
        for start, kind, value in found
        for m in _PIECE.finditer(value)
    ]


def _identifier_chars(found: list[tuple[int, Any, str]], length: int) -> list[bool]:
    marks = [False] * length
    for start, kind, value in found:
        if _named(kind):
            marks[start : start + len(value)] = [True] * len(value)
    return marks


def _changed(old: list[str], new: list[str]) -> list[bool]:
    """Which items of `new` lie outside every equal block of an alignment with `old`."""
    changed = [True] * len(new)
    for block in difflib.SequenceMatcher(None, old, new, autojunk=False).get_matching_blocks():
        changed[block.b : block.b + block.size] = [False] * block.size
    return changed


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    return starts


def char_classes(before: str, after: str, path: str | None) -> tuple[list[str], dict[str, Any]]:
    """Each character of `after` as identifier or other, changed or copied; and how it was lexed."""
    lexer, fallback = lexer_for(path), None
    try:
        found, old_found = lexemes(after, lexer), lexemes(before, lexer)
    except ValueError:
        fallback, lexer = lexer.name, get_lexer_by_name("text", **_OPTIONS)
        found, old_found = lexemes(after, lexer), lexemes(before, lexer)
    new, old = _pieces(found), _pieces(old_found)
    edited = [True] * len(new)
    # Lines first: a piece is copied only inside an equal run of lines, or matched within the
    # replaced run it belongs to, so a new line's spaces never pair with an unrelated old line.
    old_lines, new_lines = before.splitlines(keepends=True), after.splitlines(keepends=True)
    old_starts, new_starts = _line_starts(before), _line_starts(after)
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            continue
        lo, hi = new_starts[j1], new_starts[j2]
        mine = [k for k, (start, _, _) in enumerate(new) if lo <= start < hi]
        if tag == "equal":
            for k in mine:
                edited[k] = False
            continue
        if tag == "replace":
            olo, ohi = old_starts[i1], old_starts[i2]
            theirs = [piece for start, piece, _ in old if olo <= start < ohi]
            for k, changed in zip(mine, _changed(theirs, [new[k][1] for k in mine]), strict=True):
                edited[k] = changed
    classes = [""] * len(after)
    for (start, piece, named), changed in zip(new, edited, strict=True):
        classes[start : start + len(piece)] = [_cell(named, changed)] * len(piece)
    whole = _identifier_chars(found, len(after))
    by_line: list[bool] = []
    for line in new_lines:
        try:
            by_line += _identifier_chars(lexemes(line, lexer), len(line))
        except ValueError:
            by_line += [False] * len(line)
    return classes, {
        "lexer": lexer.name if fallback is None else f"text, {fallback} could not tile it",
        "lex_error_chars": sum(len(value) for _, kind, value in found if kind in Error),
        "line_disagreement": (
            sum(a != b for a, b in zip(whole, by_line, strict=True)) / len(after) if after else 0.0
        ),
    }


def lexer_unsure(row: Mapping[str, Any]) -> bool:
    """Whether a lexer may have misread a scored row: error characters, or an identifier class
    that changes when each line is lexed alone. A row without that record raises ValueError."""
    if "lex_error_chars" not in row or "line_disagreement" not in row:
        raise ValueError(f"row {row.get('id')!r} carries no lexer record")
    return bool(row["lex_error_chars"]) or bool(row["line_disagreement"])


def _merge(cells: set[str]) -> str:
    """A token over several characters: an identifier if any is, changed if any is."""
    return _cell(
        any(c.startswith("identifier") for c in cells), any(c.endswith("changed") for c in cells)
    )


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
