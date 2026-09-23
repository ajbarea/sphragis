"""Which review comments were written by a machine rather than a reviewer.

The study measures conventions reviewers enforce without writing them down. A lint bot enforces
written rules, and a host that runs one gives its adapters something to learn that another host's
never saw, so automated comments are excluded.

Identity comes first, at build: a comment whose author the host tags `SERVICE_USER` is dropped
there (`sphragis.corpus.build.is_service_user`). What a bot writes is judged here, by `refine`,
against the bots' own message templates, each registered with the source it was read from; that
covers hosts that do not expose the tag and corpora collected before it was kept:

- `qt-sanity-bot.json`, every complaint `git-hooks/sanitize-commit` in qt/qtrepotools posts, the
  union over every version of the hook in effect across the corpus span, extracted by
  `scripts/extract_bot_templates.py`;
- Qt's QUIP-23 review integration, which posts one fixed message on changes to files carrying a
  `Qt-Security` header (https://contribute.qt-project.org/quips/23);
- flake8 lint output posted inline on pyside/pyside-setup, "CODE: message" with the code families
  flake8's default checkers emit (pycodestyle E/W, pyflakes F, mccabe C9). The posting bot's own
  script is not published; the text is flake8's, and all 338 matches in the Qt corpus are on that
  one project.

A template matches a whole line, and a comment is automated only when every paragraph of it is:
the Sanity Bot joins the complaints it has for one line with a blank line, while a reviewer who
writes a sentence around "Trailing whitespace", or a paragraph after a bot's text, is not a bot.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import cache
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent

QUIP23: dict[str, Any] = {
    "bot": "Qt QUIP-23 security review integration",
    "source": "https://contribute.qt-project.org/quips/23",
    "templates": [{"pattern": re.escape("Modifying security sensitive file.")}],
    "prefixes": [],
}

FLAKE8: dict[str, Any] = {
    "bot": "flake8 lint output",
    "source": "https://flake8.pycqa.org/en/latest/user/error-codes.html",
    "templates": [{"pattern": r"(?:[EW]\d{3}|F\d{3}|C9\d{2}): [^\n]+"}],
    "prefixes": [],
}

_PARAGRAPH = re.compile(r"\n\s*\n")
_LINE_BREAKS = re.compile("\r\n|[\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]")


def _registries_data() -> list[dict[str, Any]]:
    return [json.loads((_HERE / "qt-sanity-bot.json").read_text()), QUIP23, FLAKE8]


@cache
def _registries() -> tuple[tuple[str, re.Pattern[str]], ...]:
    compiled = []
    for registry in _registries_data():
        prefixes = "|".join(re.escape(p) for p in registry.get("prefixes", []))
        lead = f"(?:{prefixes})?" if prefixes else ""
        body = "|".join(f"(?:{t['pattern']})" for t in registry["templates"])
        compiled.append((registry["bot"], re.compile(rf"{lead}(?:{body})")))
    return tuple(compiled)


def registry_digest() -> str:
    """A digest of what the registries match: bot, prefixes and patterns, nothing else.

    Provenance fields (when a registry was generated, by what) are left out, so regenerating a
    registry with the same templates does not mark every refinement stale.
    """
    content = [
        {
            "bot": r["bot"],
            "prefixes": r.get("prefixes", []),
            "patterns": sorted(t["pattern"] for t in r["templates"]),
        }
        for r in _registries_data()
    ]
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:12]


def _line_bot(paragraph: str) -> str | None:
    for bot, pattern in _registries():
        if pattern.fullmatch(paragraph):
            return bot
    return None


def matched_bot(text: str) -> str | None:
    """The registered bot that wrote this whole comment, or None.

    Every paragraph must match a template; the first paragraph's bot is reported.
    """
    # Every line break a template's "[^\n]" would otherwise read through becomes a newline, so a
    # reviewer's sentence after a bot line on a carriage return or a Unicode separator is not
    # swallowed into the bot's template.
    text = _LINE_BREAKS.sub("\n", text)
    paragraphs = [p.strip() for p in _PARAGRAPH.split(text.strip()) if p.strip()]
    if not paragraphs:
        return None
    bots = [_line_bot(p) for p in paragraphs]
    return bots[0] if all(bots) else None
