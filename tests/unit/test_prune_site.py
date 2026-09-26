"""The prune that makes the nav the list of what deploys."""

from __future__ import annotations

import importlib.util
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("prune_site", ROOT / "scripts" / "prune_site.py")
assert _spec is not None and _spec.loader is not None
prune_site = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prune_site)

NAV = [
    {"Home": "index.md"},
    {"Protocol": "protocol.md"},
    {"Log": "https://github.com/example/repo/blob/main/docs/log.md"},
]


def _docs(tmp: Path) -> Path:
    docs = tmp / "docs"
    (docs / "plans").mkdir(parents=True)
    for page in ("index.md", "protocol.md", "log.md", "plans/one.md"):
        (docs / page).write_text("# page\n")
    return docs


def _site(tmp: Path, protocol_links_to: str = "../") -> Path:
    site = tmp / "site"
    for directory in ("", "protocol", "log", "plans/one"):
        (site / directory).mkdir(parents=True, exist_ok=True)
        (site / directory / "index.html").write_text('<a href="../">up</a>')
    (site / "protocol" / "index.html").write_text(f'<a href="{protocol_links_to}">x</a>')
    (site / "assets").mkdir()
    (site / "assets" / "seal.svg").write_text("<svg/>")
    items = [
        {"location": loc}
        for loc in ("", "protocol/", "protocol/#gate", "log/", "log/#a", "plans/one/")
    ]
    (site / "search.json").write_text(json.dumps({"config": {}, "items": items}))
    urls = "".join(
        f"<url><loc>https://x/{d}</loc></url>" for d in ("", "protocol/", "log/", "plans/one/")
    )
    (site / "sitemap.xml").write_text(f"<urlset>{urls}</urlset>")
    return site


def test_the_nav_names_what_stays_and_external_links_are_not_pages(tmp_path: Path) -> None:
    assert prune_site.unlisted(NAV, _docs(tmp_path)) == ["plans/one", "log"]


def test_prune_removes_unlisted_pages_from_the_site_search_and_sitemap(tmp_path: Path) -> None:
    removed = prune_site.unlisted(NAV, _docs(tmp_path))
    site = _site(tmp_path)
    assert prune_site.prune(site, removed) == []
    assert (site / "index.html").is_file() and (site / "protocol" / "index.html").is_file()
    assert not (site / "log").exists() and not (site / "plans").exists(), "empty parents go too"
    assert (site / "assets" / "seal.svg").is_file(), "assets are not pages"
    locations = [i["location"] for i in json.loads((site / "search.json").read_text())["items"]]
    assert locations == ["", "protocol/", "protocol/#gate"]
    sitemap = (site / "sitemap.xml").read_text()
    assert "log/" not in sitemap and "plans/one/" not in sitemap and "protocol/" in sitemap


def test_a_link_left_pointing_at_a_pruned_page_is_reported(tmp_path: Path) -> None:
    removed = prune_site.unlisted(NAV, _docs(tmp_path))
    site = _site(tmp_path, protocol_links_to="../log/#a")
    assert prune_site.prune(site, removed) == ["protocol/index.html -> ../log/"]


def test_the_real_nav_keeps_the_research_log_and_design_record_off_the_site() -> None:
    nav = tomllib.loads((ROOT / "zensical.toml").read_text())["project"]["nav"]
    removed = prune_site.unlisted(nav)
    assert "research-log" in removed
    assert all(
        not d.startswith(("protocol", "registered", "outcome", "artifacts")) for d in removed
    )
    assert "" not in removed, "the landing page deploys"
