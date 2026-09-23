"""Automated reviewers are recognised by account tag or by their own message templates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sphragis.corpus.automated import matched_bot

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


def test_the_registry_records_where_it_came_from() -> None:
    registry = json.loads(REGISTRY.read_text())
    assert all(len(v["commit"]) == 40 for v in registry["versions"])
    assert registry["source"].endswith("/git-hooks/sanitize-commit")
    assert len(registry["templates"]) > 50
    assert not any(t["pattern"].startswith(".+?") for t in registry["templates"])


@pytest.mark.parametrize(
    "text",
    [
        "E501: please wrap.\n\nAlso this function name is misleading",
        "Semicolon after the closing brace is redundant,\nand the whole block can go",
        "Pick-to entry '6.5' is not a valid branch in qtbase. Please drop it\n\nand fix the typo",
        "ABC123: not a flake8 code",
        "E5011: four digits",
        "C101: mccabe codes are C9",
    ],
)
def test_reviewer_prose_around_a_template_is_not_a_bot(text: str) -> None:
    assert matched_bot(text) is None


def test_several_complaints_on_one_line_are_one_bot_comment() -> None:
    assert matched_bot("Hint: Trailing whitespace\n\nHint: Leading tabs") == "Qt Sanity Bot"
    assert matched_bot("Hint: Semicolon after Q_OBJECT\n\nHint: Trailing whitespace") == (
        "Qt Sanity Bot"
    )


def test_a_bot_paragraph_followed_by_a_reviewer_one_is_not_a_bot() -> None:
    assert matched_bot("Hint: Leading tabs\n\nand please rename this variable") is None


def test_the_registry_spans_every_hook_version_in_the_corpus() -> None:
    registry = json.loads(REGISTRY.read_text())
    assert registry["since"] <= "2024-10-01"
    assert len(registry["versions"]) > 1
    patterns = {t["pattern"] for t in registry["templates"]}
    # Present only in hook versions live early in the corpus span.
    assert any("upstream" in p for p in patterns)


def test_the_digest_follows_templates_not_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    import sphragis.corpus.automated as automated

    data = automated._registries_data()
    base = automated.registry_digest()
    renamed = [{**r, "provenance": {"generated_at": "later"}} for r in data]
    monkeypatch.setattr(automated, "_registries_data", lambda: renamed)
    assert automated.registry_digest() == base
    changed = [data[0], data[1], {**data[2], "templates": [{"pattern": "X"}]}]
    monkeypatch.setattr(automated, "_registries_data", lambda: changed)
    assert automated.registry_digest() != base


@pytest.mark.parametrize("separator", ["\r", "\u2028", "\u2029", "\v", "\x85"])
def test_a_reviewer_sentence_after_any_line_break_is_not_swallowed_by_a_template(
    separator: str,
) -> None:
    from sphragis.corpus.automated import matched_bot

    assert matched_bot(f"E501: line too long{separator}I think we should refactor this") is None
    assert matched_bot(f"E501: line too long{separator}{separator}W291: trailing whitespace")
