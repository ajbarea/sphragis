"""Automated reviewers are recognised by account tag or by their own message templates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sphragis.corpus.automated import is_automated, is_service_user, matched_bot

REGISTRY = Path(__file__).resolve().parents[3] / "sphragis/corpus/automated/qt-sanity-bot.json"


@pytest.mark.parametrize(
    ("text", "bot"),
    [
        ("Hint: Trailing whitespace", "Qt Sanity Bot"),
        ("Hint: Leading tabs", "Qt Sanity Bot"),
        ("Hint: WS-only change", "Qt Sanity Bot"),
        ("Hint: Flow control keywords must be followed by a single space", "Qt Sanity Bot"),
        ("Unresolved merge conflict", "Qt Sanity Bot"),
        ("Hint: Semicolon after Q_PROPERTY", "Qt Sanity Bot"),
        ("behaviour -> behavior? [*]", "Qt Sanity Bot"),
        ("occured -> occurred?", "Qt Sanity Bot"),
        ("Modifying security sensitive file.", "Qt QUIP-23 security review integration"),
        ("E402: module level import not at top of file", "flake8 lint output"),
        (
            "F405: 'PySide6' may be undefined, or defined from star imports: PySide6",
            "flake8 lint output",
        ),
    ],
)
def test_bot_messages_are_recognised(text: str, bot: str) -> None:
    assert matched_bot(text) == bot


@pytest.mark.parametrize(
    "text",
    [
        "typo",
        "same here",
        "Trailing whitespace here, and also rename the variable",
        "nit: line length",
        "L152 still says FutureWarning",
        "Why is this -> needed?",
        "Can you fix the occured -> occurred? typo and the other one",
        "Acknowledged",
        "",
    ],
)
def test_reviewer_messages_are_not_taken_for_bots(text: str) -> None:
    assert matched_bot(text) is None


def test_a_service_user_is_automated_whatever_it_writes() -> None:
    comment = {"author": {"_account_id": "abc", "tags": ["SERVICE_USER"]}, "message": "typo"}
    assert is_service_user(comment["author"])
    assert is_automated(comment)


def test_an_untagged_author_is_judged_by_the_message() -> None:
    assert not is_automated({"author": {"_account_id": "abc"}, "message": "typo"})
    assert is_automated({"author": {"_account_id": "abc"}, "message": "Hint: Leading tabs"})
    assert not is_service_user(None)


def test_the_registry_records_where_it_came_from() -> None:
    registry = json.loads(REGISTRY.read_text())
    assert registry["source_commit"] and len(registry["source_commit"]) == 40
    assert registry["source"].endswith("/git-hooks/sanitize-commit")
    assert len(registry["templates"]) > 50
    assert not any(t["pattern"].startswith(".+?") for t in registry["templates"])
