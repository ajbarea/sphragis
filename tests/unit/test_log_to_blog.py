"""The research log published as a journal, one post per dated entry."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("log_to_blog", ROOT / "scripts" / "log_to_blog.py")
assert _spec is not None and _spec.loader is not None
log_to_blog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(log_to_blog)

LOG = """# Log

intro

## Current focus

### First thing (2026-09-13)

It started. Here.

More detail.

### Undated follow-up

Inherits the date above.

### Registered before computing: the rule (2026-09-14, rerun 2026-09-15)

| a | b |
|---|---|

## Open bugs & findings

### Correction: the count was wrong (2026-09-14)

Fixed.
"""


def _field(post: Path, name: str) -> str:
    match = re.search(rf"^{name}: (.+)$", post.read_text(), re.M)
    assert match, f"{post.name} has no {name}"
    return match.group(1)


def test_every_entry_becomes_a_post_and_an_undated_one_takes_the_date_before() -> None:
    found = log_to_blog.entries(LOG)
    assert [(e.title, e.date) for e in found] == [
        ("First thing", "2026-09-13"),
        ("Undated follow-up", "2026-09-13"),
        ("Registered before computing: the rule", "2026-09-14"),
        ("Correction: the count was wrong", "2026-09-14"),
    ]
    assert "## Open bugs" not in found[2].body, "a section heading ends the entry before it"


def test_posts_keep_the_logs_order_within_a_day_and_have_unique_slugs(tmp_path: Path) -> None:
    written = log_to_blog.render(
        tmp_path, log_to_blog.entries(LOG + "\n### First thing (2026-09-13)\n\nagain\n")
    )
    posts = sorted(p for p in written if p.parent.name == "posts")
    dates = {_field(p, "date") for p in posts}
    assert len(dates) == len(posts), "same-day entries get distinct times, in log order"
    slugs = [_field(p, "slug") for p in posts]
    assert len(set(slugs)) == len(slugs)
    assert (tmp_path / "index.md").is_file()


def test_the_excerpt_is_the_first_paragraph_and_tables_are_not_split() -> None:
    assert log_to_blog.excerpt_split("One.\n\nTwo.") == "One.\n\n<!-- more -->\n\nTwo."
    assert log_to_blog.excerpt_split("| a |\n\nTwo.") == "| a |\n\nTwo."


def test_categories_follow_the_title() -> None:
    assert log_to_blog.categories("Registered before computing: x")[0] == "Registered"
    assert "Correction" in log_to_blog.categories("Correction: every successor is a rework")
    assert log_to_blog.categories("Something else entirely") == [log_to_blog.FALLBACK]


def test_a_quote_in_a_title_stays_valid_front_matter(tmp_path: Path) -> None:
    written = log_to_blog.render(
        tmp_path, log_to_blog.entries('### Half the "review comments" (2026-09-14)\n\nx\n')
    )
    text = next(p for p in written if p.parent.name == "posts").read_text()
    assert 'title: "Half the \\"review comments\\""' in text


def test_the_real_log_publishes_every_entry() -> None:
    text = log_to_blog.SOURCE.read_text()
    headings = re.findall(r"^### ", text, re.M)
    assert len(log_to_blog.entries(text)) == len(headings)
