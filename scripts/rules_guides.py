"""Snapshot each organization's written coding conventions, pinned, for the rules-file comparator.

OpenStack's are its `hacking` guidelines (`HACKING.rst`), at the latest commit that touched the
file. Wikimedia's are MediaWiki's coding conventions: the general page, the documentation page,
and the page for every language with a file in Wikimedia's training window, read from its first
admissible partition's corpus (both halves together are the organization). Each page is pinned
to a revision and hashed, so the written arm reproduces whatever the wikis do next.

    uv run --no-sync python scripts/rules_guides.py --results datasets/results \\
        --out datasets/rules
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sphragis.experiment.decomposition import halves
from sphragis.experiment.holdout import window_split
from sphragis.experiment.retrieval import adapter_run, first_partitions
from sphragis.provenance import provenance_header

USER_AGENT = "sphragis-research (ajb6289@rit.edu)"
MEDIAWIKI_API = "https://www.mediawiki.org/w/api.php"
MEDIAWIKI_LICENSE = "CC BY-SA 4.0 (mediawiki.org)"
OPENDEV_API = "https://opendev.org/api/v1/repos/openstack/hacking"
OPENDEV_LICENSE = "Apache-2.0 (openstack/hacking)"
# Language-independent pages, always included.
MEDIAWIKI_GENERAL = ("Manual:Coding conventions", "Manual:Coding conventions/Documentation")
# A file extension to the MediaWiki page for its language: every language page there is
# (2026-10-06), less Selenium, which is a test framework rather than a language.
MEDIAWIKI_PAGES = {
    "php": "Manual:Coding conventions/PHP",
    "js": "Manual:Coding conventions/JavaScript",
    "css": "Manual:Coding conventions/CSS",
    "less": "Manual:Coding conventions/CSS",
    "py": "Manual:Coding conventions/Python",
    "sql": "Manual:Coding conventions/Database",
    "svg": "Manual:Coding conventions/SVG",
    "vue": "Manual:Coding conventions/Vue",
    "java": "Manual:Coding conventions/Java",
    "lua": "Manual:Coding conventions/Lua",
    "pp": "Manual:Coding conventions/Puppet",
}

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--results", type=Path, required=True, help="the adapters' partition runs")
parser.add_argument("--out", type=Path, required=True)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def extensions(results: Path, org: str) -> Counter[str]:
    """File extensions of the organization's training-window rows, both halves together."""
    listing = json.loads((results / f"admissible-partitions-{org}.json").read_text())
    order, size = first_partitions(listing, org=org)
    _, _, root, _ = adapter_run(results, org=org, partition=order[0], order=order, size=size)
    counts: Counter[str] = Counter()
    for half in halves(org):
        for row in window_split(root, half)[0]:
            name = str(row.get("path") or "").rsplit("/", 1)[-1]
            if "." in name:
                counts[name.rsplit(".", 1)[-1].lower()] += 1
    return counts


def mediawiki(title: str) -> dict[str, Any]:
    """A MediaWiki page's current wikitext, pinned to its revision."""
    query = urllib.parse.urlencode(
        {
            "action": "query",
            "prop": "revisions",
            "titles": title,
            "rvprop": "ids|timestamp|content",
            "rvslots": "main",
            "format": "json",
            "formatversion": "2",
        }
    )
    page = json.loads(fetch(f"{MEDIAWIKI_API}?{query}"))["query"]["pages"][0]
    revision = page["revisions"][0]
    text = revision["slots"]["main"]["content"]
    return {
        "title": title,
        "url": f"https://www.mediawiki.org/w/index.php?title="
        f"{urllib.parse.quote(title)}&oldid={revision['revid']}",
        "revision": revision["revid"],
        "timestamp": revision["timestamp"],
        "license": MEDIAWIKI_LICENSE,
        "text": text,
    }


def hacking() -> dict[str, Any]:
    """OpenStack's HACKING.rst at the latest commit that touched it."""
    commit = json.loads(fetch(f"{OPENDEV_API}/commits?limit=1&path=HACKING.rst"))[0]
    sha = commit["sha"]
    url = f"https://opendev.org/openstack/hacking/raw/commit/{sha}/HACKING.rst"
    return {
        "title": "HACKING.rst",
        "url": url,
        "revision": sha,
        "timestamp": commit["commit"]["committer"]["date"],
        "license": OPENDEV_LICENSE,
        "text": fetch(url).decode(),
    }


def main() -> None:
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    present = extensions(args.results, "wikimedia")
    titles = list(MEDIAWIKI_GENERAL)
    for extension in sorted(present):
        page = MEDIAWIKI_PAGES.get(extension)
        if page and page not in titles:
            titles.append(page)
    guides = {
        "openstack": {"sources": [hacking()], "selection": "the hacking guidelines"},
        "wikimedia": {
            "sources": [mediawiki(title) for title in titles],
            "selection": "general and documentation pages, and one per language in the "
            "training window",
            "extensions": dict(present.most_common()),
        },
    }
    for org, guide in guides.items():
        for source in guide["sources"]:
            source["sha256"] = hashlib.sha256(source["text"].encode()).hexdigest()
            source["words"] = len(source["text"].split())
        guide |= {
            "org": org,
            "fetched_at": datetime.now(UTC).isoformat(),
            "provenance": provenance_header(),
        }
        path = args.out / f"guide-{org}.json"
        path.write_text(json.dumps(guide, indent=2, ensure_ascii=False) + "\n")
        print(org, [(s["title"], s["revision"], s["words"]) for s in guide["sources"]])
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
