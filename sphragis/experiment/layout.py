"""Whether a revision changes only the layout of the reviewed hunk: what a formatter would do.

No standard flag exists for a formatting-only revision in code-review data (research log,
2026-10-18), so the rule is this study's, fixed before any reading without such examples. A
revision is layout-only when it keeps every character of the hunk other than whitespace and the
file's language reads the whitespace it changed as layout, judged by the language's own tokens
as structural diff tools judge it. Where the rule cannot tell, the revision is not layout-only:

- Python (`.py`, `.pyi`): both versions tokenize (`tokenize`) to the same tokens, line breaks
  inside brackets (`NL`) and the spacing between tokens aside. A string's characters, an f-string's
  literal text among them, an f-string with a `{x = }` field whole, and every block's indentation
  are compared exactly, so a reworded string, a re-indented block or a hunk moved into or out of
  a block is not layout. A comment is compared with its runs of whitespace collapsed.
- `FREE_FORM` languages, which read whitespace between tokens only as a separator: the same
  Pygments lexemes (`tokens.lexer_for`) once whitespace between them is dropped. Strings,
  preprocessor lines and embedded content are exact, other comments whitespace-collapsed, symbols
  compared a character at a time with a space kept where it keeps two from reading as one
  operator, and, where a line break can end a statement (`LINE_BREAKS`), line breaks kept. A hunk
  the lexer cannot tile, lexes with an error, or that holds markup (`MARKUP`: a `</` or `/>`) is
  not layout-only.
- Every other file (CSS, LESS, JSX, Scala, shell, Makefile, YAML, reStructuredText, Markdown,
  patches, templates, configuration and text): never, since whitespace there can be structure or
  content.

Each version is read in place, between the hunk's unchanged lines, so a block's indentation is
judged against the code around it and a hunk inside a string its context opens reads as string.
Where either version read in place leaves a string open to the end (`OPEN`), the hunk may sit
inside a string opened before its context, and it is not layout. Where the context reads in
neither version (a fragment opening inside a bracket or a block), the hunk is read alone, every
line's indentation then compared as written; where it reads in one only, the revision is not
layout. A hunk inside a string whose quotes both lie beyond its context lines reads as code.
"""

from __future__ import annotations

import io
import re
import tokenize
from collections.abc import Mapping
from typing import Any

from pygments.token import Comment, Error, Operator, Punctuation, String, Text

from sphragis.corpus.halves import suffix
from sphragis.experiment.tokens import lexemes, lexer_for

PYTHON = frozenset({".py", ".pyi"})

#: Pygments languages (`Lexer.name`) whose whitespace between tokens is only a separator. Not CSS
#: or LESS (a space between selectors means "descendant of"), JSX (its text is shown) or Scala
#: (Scala 3 reads indentation).
FREE_FORM = frozenset(
    {"C", "C++", "Go", "JSON", "JSON5", "Java", "JavaScript", "PHP", "QML", "SQL", "TypeScript"}
)
#: Of those, the ones in which a line break can end a statement (automatic semicolons).
LINE_BREAKS = frozenset({"Go", "JavaScript", "QML", "TypeScript"})
#: Of those, the ones whose files can hold markup, where spaces between tags are text.
MARKUP = frozenset({"JavaScript", "PHP", "TypeScript"})

_BREAK = "\n"
#: Whitespace a formatter moves. Any other space character, such as a no-break space, is content.
_SPACE = re.compile(r"[ \t\n\r\f\v]+")
#: Symbols that join a neighbour into one operator in some free-form language (`--`, `<<`, `->`,
#: `::`, `/*`, `...`); brackets, commas and semicolons join none.
_MERGING = frozenset("~!%^&*+-=|?:<>/.")
#: What a reader returns when the text it read leaves a string open to its end: the hunk may sit
#: inside a string its context lines do not open.
OPEN = "open"
# t-strings tokenize from Python 3.14.
_STRING_STARTS = {tokenize.FSTRING_START, getattr(tokenize, "TSTRING_START", -1)}
_STRING_ENDS = {tokenize.FSTRING_END, getattr(tokenize, "TSTRING_END", -1)}

Tokens = list[tuple[Any, str]]


def _collapsed(text: str) -> str:
    return _SPACE.sub(" ", text).strip(" ")


def _self_documenting(span: list[tokenize.TokenInfo]) -> bool:
    """Whether an f-string or t-string has a `{x = }` field, which prints its own spacing."""
    return any(
        a.type == tokenize.OP and a.string == "=" and b.type == tokenize.OP and b.string in "}!:"
        for a, b in zip(span, span[1:], strict=False)
    )


def _python_tokens(text: str) -> Tokens | str | None:
    source = text if text.endswith("\n") else text + "\n"
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError) as error:
        return OPEN if "string" in str(error).lower() else None
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    def offset(position: tuple[int, int]) -> int:
        return starts[position[0] - 1] + position[1]

    out: Tokens = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.type in _STRING_STARTS:
            depth, j = 1, i + 1
            while depth:
                depth += (tokens[j].type in _STRING_STARTS) - (tokens[j].type in _STRING_ENDS)
                j += 1
            if _self_documenting(tokens[i:j]):
                out.append((token.type, source[offset(token.start) : offset(tokens[j - 1].end)]))
                i = j
                continue
        if token.type == tokenize.COMMENT:
            out.append((token.type, _collapsed(token.string)))
        elif token.type == tokenize.NEWLINE:
            out.append((token.type, ""))
        elif token.type not in (tokenize.NL, tokenize.ENDMARKER):
            out.append((token.type, token.string))
        i += 1
    return out


def _markup(out: Tokens) -> bool:
    """Whether code tokens hold a tag's `</` or `/>`, as markup lexed as code does."""
    pairs = zip(out, out[1:], strict=False)
    return any((a, b) in (("<", "/"), ("/", ">")) for (_, a), (_, b) in pairs)


def _free_form_tokens(text: str, path: str) -> Tokens | str | None:
    lexer = lexer_for(path)
    # Ended by a line break, as a file is: some rules (PHP's heredoc) match only up to one.
    source = text if text.endswith("\n") else text + "\n"
    try:
        found = lexemes(source, lexer)
    except ValueError:
        return None
    out: Tokens = []
    spaced = False
    last = None
    for _, kind, value in found:
        if kind in Error:
            return None
        if kind in Text and not _SPACE.sub("", value):
            if lexer.name in LINE_BREAKS and _BREAK in value and out and out[-1][1] != _BREAK:
                out.append((Text, _BREAK))
            spaced = True
            continue
        last = kind
        # Pygments cuts runs of symbols where its rules happen to, one character at a time in C,
        # `:[` as one in JSON, so symbols are compared a character at a time.
        if (kind in Operator or kind in Punctuation) and not re.search(r"\w", value):
            pieces = [(Operator, character) for character in value]
        elif kind in Comment and kind not in Comment.Preproc and kind not in Comment.PreprocFile:
            # A preprocessor line is lexed whole as a comment; its strings and spacing are code.
            pieces = [(kind, _collapsed(value))]
        else:
            pieces = [(kind, value)]
        for piece in pieces:
            # A space that keeps two symbols from reading as one operator (`- -` and `--`).
            if spaced and out and out[-1][1][-1:] in _MERGING and piece[1][:1] in _MERGING:
                out.append((Text, " "))
            out.append(piece)
            spaced = False
        # A comment holding a line break ends a statement as the break would.
        if kind in Comment and lexer.name in LINE_BREAKS and _BREAK in value.rstrip("\n"):
            out.append((Text, _BREAK))
    while out and out[-1][1] == _BREAK:
        out.pop()
    if last is not None and last in String:
        return OPEN
    if lexer.name in MARKUP and _markup(out):
        return None
    return out


def language(path: str) -> str | None:
    """The rule `path` is judged by: `python`, a `FREE_FORM` language's name, or None."""
    if suffix(path) in PYTHON:
        return "python"
    name = lexer_for(path).name
    return name if name in FREE_FORM else None


def whitespace_only(before: Any, after: Any) -> bool:
    """Whether `after` differs from `before` and keeps every character but whitespace, of a hunk
    with characters to keep."""
    if not isinstance(before, str) or not isinstance(after, str) or before == after:
        return False
    kept = _SPACE.sub("", before)
    return bool(kept) and kept == _SPACE.sub("", after)


def _in_place(context_before: str, hunk: str, context_after: str) -> str:
    """The hunk between its unchanged lines, joined as the build split them."""
    return "\n".join(part for part in (context_before, hunk, context_after) if part)


def layout_only(
    before: Any, after: Any, path: str, context_before: str = "", context_after: str = ""
) -> bool:
    """Whether `after` differs from `before` in layout alone, by the rule for `path`'s language,
    judged in place between the hunk's unchanged lines where both versions read there."""
    if not whitespace_only(before, after):
        return False
    rule = language(path)
    if rule is None:
        return False

    def read(text: str) -> Tokens | str | None:
        return _python_tokens(text) if rule == "python" else _free_form_tokens(text, path)

    mine = read(_in_place(context_before, before, context_after))
    theirs = read(_in_place(context_before, after, context_after))
    # Either reading in place leaves a string open: the hunk may be inside one.
    if OPEN in (mine, theirs):
        return False
    if mine is not None and theirs is not None:
        return mine == theirs
    # A revision that reads in place where the hunk did not, or the reverse, is not layout; a
    # hunk whose context reads neither way is judged alone.
    if mine is not None or theirs is not None:
        return False
    mine, theirs = read(before), read(after)
    return isinstance(mine, list) and mine == theirs


def row_layout_only(row: Mapping[str, Any]) -> bool:
    """`layout_only` for a corpus row, in place between its context lines."""
    return layout_only(
        row["before"],
        row["after"],
        row["path"],
        row.get("context_before") or "",
        row.get("context_after") or "",
    )
