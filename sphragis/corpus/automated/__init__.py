"""Which review comments were written by a machine rather than a reviewer.

The study measures conventions reviewers enforce without writing them down. A lint bot enforces
written rules, and a host that runs one gives its adapters something to learn that another host's
never saw, so automated comments are excluded before an example is built.

Identity from the source comes first: Gerrit tags service accounts `SERVICE_USER` on
`AccountInfo.tags`, and a comment whose author carries that tag is automated whatever it says.
Hosts that do not expose the tag, and corpora collected before it was kept, fall back to the
bots' own message templates, each registered with the source it was read from:

- `qt-sanity-bot.json`, every complaint `git-hooks/sanitize-commit` in qt/qtrepotools posts,
  extracted by `scripts/extract_bot_templates.py` at a pinned commit;
- Qt's QUIP-23 review integration, which posts one fixed message on changes to files carrying a
  `Qt-Security` header (https://contribute.qt-project.org/quips/23);
- flake8 lint output posted inline on pyside/pyside-setup, "CODE: message" with the code families
  flake8's default checkers emit (pycodestyle E/W, pyflakes F, mccabe C9). The posting bot's
  own script is not published; the text is flake8's, and 338 of the 339 matches in the Qt corpus
  are on that one project.

A template matches the whole comment, so a reviewer who writes a sentence around "Trailing
whitespace" is not taken for the bot.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent

SERVICE_USER = "SERVICE_USER"

QUIP23: dict[str, Any] = {
    "bot": "Qt QUIP-23 security review integration",
    "source": "https://contribute.qt-project.org/quips/23",
    "templates": [{"pattern": re.escape("Modifying security sensitive file.")}],
    "prefixes": [],
}


FLAKE8: dict[str, Any] = {
    "bot": "flake8 lint output",
    "source": "https://flake8.pycqa.org/en/latest/user/error-codes.html",
    "templates": [{"pattern": r"(?:[EW]\d{3}|F\d{3}|C9\d{2}): .+"}],
    "prefixes": [],
}


@cache
def _registries() -> tuple[tuple[str, re.Pattern[str]], ...]:
    registries: list[dict[str, Any]] = [
        json.loads((_HERE / "qt-sanity-bot.json").read_text()),
        QUIP23,
        FLAKE8,
    ]
    compiled = []
    for registry in registries:
        prefixes = "|".join(re.escape(p) for p in registry.get("prefixes", []))
        lead = f"(?:{prefixes})?" if prefixes else ""
        body = "|".join(f"(?:{t['pattern']})" for t in registry["templates"])
        compiled.append((registry["bot"], re.compile(rf"{lead}(?:{body})", re.DOTALL)))
    return tuple(compiled)


def registry_digest() -> str:
    """A digest of every registered template, so a changed registry is a changed rule."""
    import hashlib

    text = (_HERE / "qt-sanity-bot.json").read_text() + json.dumps([QUIP23, FLAKE8], sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def matched_bot(text: str) -> str | None:
    """The registered bot whose template this whole comment matches, or None."""
    stripped = text.strip()
    for bot, pattern in _registries():
        if pattern.fullmatch(stripped):
            return bot
    return None


def is_service_user(author: Mapping[str, Any] | None) -> bool:
    """Whether a comment's author is tagged a service account by the host."""
    return bool(author) and SERVICE_USER in (author.get("tags") or [])


def is_automated(comment: Mapping[str, Any]) -> bool:
    """A comment by a tagged service account, or one matching a registered bot template."""
    return (
        is_service_user(comment.get("author"))
        or matched_bot(str(comment.get("message", ""))) is not None
    )
