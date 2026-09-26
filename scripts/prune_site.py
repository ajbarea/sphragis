"""Remove every built page the nav does not name, so the nav is the list of what deploys.

Zensical builds every Markdown file under docs/ and has no `exclude_docs` yet (zensical#135),
so the research log and the design record would ship as pages nobody navigates to, and the
search index would fill with thousands of dated lines. This runs after `zensical build`: it
deletes the unlisted pages, drops them from `search.json` and `sitemap.xml`, and fails if a
page that stays still links to one it removed.

    uv run --no-sync --no-active python scripts/prune_site.py [--site site]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def nav_pages(entries: object) -> list[str]:
    """Every local page a nav entry points at; external links are not pages."""
    pages: list[str] = []
    if isinstance(entries, list):
        for entry in entries:
            pages += nav_pages(entry)
    elif isinstance(entries, dict):
        for value in entries.values():
            pages += nav_pages(value)
    elif isinstance(entries, str) and not re.match(r"https?://", entries):
        pages.append(entries)
    return pages


def built_dir(page: str) -> str:
    """The directory a source page builds to: `a/b.md` -> `a/b`, `index.md` -> ``."""
    stem = page.removesuffix(".md")
    if stem == "index" or stem.endswith("/index"):
        return stem.removesuffix("index").rstrip("/")
    return stem


def unlisted(nav: object, docs: Path = DOCS) -> list[str]:
    """Built directories for the source pages the nav leaves out, deepest first."""
    listed = {built_dir(page) for page in nav_pages(nav)}
    sources = [str(path.relative_to(docs)) for path in docs.rglob("*.md")]
    return sorted({built_dir(s) for s in sources} - listed, key=lambda d: -d.count("/"))


def prune(site: Path, removed: list[str]) -> list[str]:
    """Delete the pages, filter the indexes, and return any link left pointing at a removed page."""
    for directory in removed:
        target = site / directory
        if target.is_dir():
            shutil.rmtree(target)
    # A removed directory left empty (superpowers/ once plans/ and specs/ are gone) goes too.
    for directory in removed:
        parent = (site / directory).parent
        while parent != site and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent

    def is_removed(location: str) -> bool:
        path = location.split("#", 1)[0].strip("/")
        return any(path == d or path.startswith(d + "/") for d in removed)

    search = site / "search.json"
    if search.is_file():
        index = json.loads(search.read_text())
        index["items"] = [item for item in index["items"] if not is_removed(item["location"])]
        search.write_text(json.dumps(index, separators=(",", ":")))

    sitemap = site / "sitemap.xml"
    if sitemap.is_file():
        text = sitemap.read_text()
        for directory in removed:
            text = re.sub(
                rf"\s*<url>\s*<loc>[^<]*/{re.escape(directory)}/</loc>.*?</url>",
                "",
                text,
                flags=re.S,
            )
        sitemap.write_text(text)

    dangling = []
    for html in sorted(site.rglob("*.html")):
        page_dir = html.parent.relative_to(site)
        for href in re.findall(r'href="([^"#?]+)', html.read_text()):
            if re.match(r"[a-z]+:", href) or href.startswith("/"):
                continue
            resolved = (page_dir / href).as_posix()
            parts: list[str] = []
            for part in resolved.split("/"):
                if part == "..":
                    if parts:
                        parts.pop()
                elif part not in ("", "."):
                    parts.append(part)
            if is_removed("/".join(parts)):
                dangling.append(f"{html.relative_to(site)} -> {href}")
    return dangling


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=ROOT / "site")
    args = parser.parse_args()
    nav = tomllib.loads((ROOT / "zensical.toml").read_text())["project"]["nav"]
    removed = unlisted(nav)
    dangling = prune(args.site, removed)
    print(f"pruned {len(removed)} unlisted pages: {', '.join(removed) or 'none'}")
    if dangling:
        print("links to pruned pages:\n  " + "\n  ".join(dangling))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
