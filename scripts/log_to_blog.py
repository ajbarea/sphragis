"""Publish the research log as a dated journal: one post per entry, generated at build time.

`docs/research-log.md` stays the one file the log is written in. Each `### Title (YYYY-MM-DD)`
entry becomes a post under `docs/log/posts/` for Zensical's blog plugin, which lists them newest
first with monthly archives and category pages. An entry without a date in its title takes the
date of the entry before it, and entries sharing a day keep the log's order.

    uv run --no-sync --no-active python scripts/log_to_blog.py [--out docs/log]
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "research-log.md"
OUT = ROOT / "docs" / "log"

ENTRY = re.compile(r"^### (.+)$", re.M)
DATE = re.compile(r"\((\d{4}-\d{2}-\d{2})[^)]*\)\s*$")

#: First match wins for the primary category; every match is listed.
CATEGORIES = (
    ("Registered", re.compile(r"^Registered\b", re.I)),
    ("Correction", re.compile(r"\b(correct|retract|withdr[ae]w|wrong|bug|mistak|revers)", re.I)),
    (
        "Corpus",
        re.compile(
            r"\b(corpus|fetch|gerrit|notedb|scrub|dedup|collect|robots|label|audit|manifest|window|seal)",
            re.I,
        ),
    ),
    (
        "Measurement",
        re.compile(
            r"\b(agreement|contrast|interval|seed|power|kappa|ac1|leak|attack|adapter|gate|placebo|detect|rank|fdlora|feddpa|reading)",
            re.I,
        ),
    ),
)
FALLBACK = "Apparatus"


class Entry(NamedTuple):
    title: str
    date: str
    body: str


def entries(text: str) -> list[Entry]:
    """The log's entries in file order, each with its own or its predecessor's date."""
    heads = list(ENTRY.finditer(text))
    found: list[Entry] = []
    last_date = ""
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[head.end() : end]
        body = re.split(r"^## ", body, maxsplit=1, flags=re.M)[0].strip()
        raw = head.group(1).strip()
        match = DATE.search(raw)
        date = match.group(1) if match else last_date
        if not date:
            continue
        last_date = date
        title = DATE.sub("", raw).strip() if match else raw
        found.append(Entry(title, date, body))
    return found


def categories(title: str) -> list[str]:
    hits = [name for name, rule in CATEGORIES if rule.search(title)]
    return hits or [FALLBACK]


def slugify(title: str) -> str:
    words = re.sub(r"[`*_]", "", title).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", words).strip("-")
    if len(slug) > 60:
        slug = slug[:60].rsplit("-", 1)[0]
    return slug or "entry"


def excerpt_split(body: str) -> str:
    """The body with the blog's excerpt marker after its first paragraph."""
    parts = re.split(r"\n\s*\n", body, maxsplit=1)
    if len(parts) == 1 or parts[0].lstrip().startswith(("|", "```", "-", "#")):
        return body
    return f"{parts[0]}\n\n<!-- more -->\n\n{parts[1]}"


def yaml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(out: Path, log: list[Entry]) -> list[Path]:
    """Write the posts and the journal's landing page; return the files written."""
    posts = out / "posts"
    if posts.exists():
        shutil.rmtree(posts)
    posts.mkdir(parents=True)
    written: list[Path] = []
    seen: dict[str, int] = {}
    per_day: dict[str, int] = {}
    for entry in log:
        slug = slugify(entry.title)
        seen[slug] = seen.get(slug, 0) + 1
        if seen[slug] > 1:
            slug = f"{slug}-{seen[slug]}"
        # Same-day entries keep the log's order: a later entry is a minute later.
        n = per_day.get(entry.date, 0)
        per_day[entry.date] = n + 1
        stamp = datetime.fromisoformat(entry.date) + timedelta(minutes=n)
        cats = "\n".join(f"  - {c}" for c in categories(entry.title))
        page = (
            f"---\ndate: {stamp.isoformat()}\nslug: {slug}\n"
            f"title: {yaml_string(entry.title)}\ncategories:\n{cats}\n---\n\n"
            f"# {entry.title}\n\n{excerpt_split(entry.body)}\n"
        )
        path = posts / f"{entry.date}-{slug}.md"
        path.write_text(page)
        written.append(path)
    index = out / "index.md"
    index.write_text(
        "---\ntitle: Research log\ndescription: The dated record of what was built, measured,"
        " found and corrected.\n---\n\n# Research log\n\n"
        "The dated record of what was built, measured, found and corrected, newest first."
        " Withdrawn and superseded readings stay, marked, so every correction can be audited."
        " Browse by month or by category in the sidebar.\n"
    )
    written.append(index)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    log = entries(SOURCE.read_text())
    written = render(args.out, log)
    print(f"wrote {len(written) - 1} posts and the index under {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
