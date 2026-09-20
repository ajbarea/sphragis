"""The documentation site, held to the same standard as the report it links from.

A reviewer scoring replicability opens the site, not the repository, so a page that has
drifted from the apparatus is worse than no page. These tests are what makes the drift
visible on a push rather than on a read.

`harvest.py` lives at the repository root rather than under `scripts/`, which this file
puts on the import path. Its own machinery is exercised by asserting the site, so the
tests below check the properties a passing harvest does not: that the nav matches the
filesystem, that internal links resolve, and that the retired term stays retired.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"

sys.path.insert(0, str(ROOT))

import harvest  # noqa: E402


def _nav_targets(entries: object) -> list[str]:
    """Every page a nav entry points at, flattened out of the nested sections."""
    targets: list[str] = []
    if isinstance(entries, list):
        for entry in entries:
            targets += _nav_targets(entry)
    elif isinstance(entries, dict):
        for value in entries.values():
            targets += _nav_targets(value)
    elif isinstance(entries, str):
        targets.append(entries)
    return targets


@pytest.fixture(scope="module")
def config() -> dict:
    return tomllib.loads((ROOT / "zensical.toml").read_text())["project"]


def test_every_figure_resolves_to_its_artifact() -> None:
    """No page quotes a number the artifact behind it no longer produces."""
    verified, drifted, unbacked = harvest.check_claims()
    assert not drifted, "\n".join(drifted)
    assert not unbacked, "\n".join(unbacked)
    assert verified, "the claim table is empty, so this test asserts nothing"


def test_no_figure_goes_unasserted() -> None:
    """A figure added to a page without a claim behind it fails here, not silently."""
    assert not harvest.unclaimed(), "\n".join(harvest.unclaimed())


def test_artifact_index_is_current() -> None:
    """The index is generated; a committed copy that no longer matches is stale."""
    assert harvest.INDEX_PAGE.read_text() == harvest.render_index(), "run `make docs-index`"


def test_every_committed_result_is_in_the_index() -> None:
    """Nothing under datasets/results is missing from the page that indexes it.

    An artifact whose writer the repository never names is listed too, in its own section.
    What this catches is a file dropped from the page entirely, which is the failure the
    index cannot show by itself: an index is only complete against something.
    """
    page = harvest.INDEX_PAGE.read_text()
    missing = [name for name in harvest.tracked_results() if f"`{name}`" not in page]
    assert not missing, f"not listed in docs/artifacts.md: {missing}"


def test_nav_pages_exist(config: dict) -> None:
    for target in _nav_targets(config["nav"]):
        assert (DOCS / target).exists(), f"nav points at docs/{target}, which does not exist"


def test_every_page_is_in_the_nav(config: dict) -> None:
    """Except the design record, which is linked from prose rather than listed."""
    listed = set(_nav_targets(config["nav"]))
    pages = {str(path.relative_to(DOCS)) for path in DOCS.glob("*.md")}
    assert pages == listed, f"not in nav: {sorted(pages - listed)}"


def test_extra_css_exists(config: dict) -> None:
    for asset in config.get("extra_css", []) + config.get("extra_javascript", []):
        assert (DOCS / asset).exists(), f"zensical.toml lists {asset}, which does not exist"


def test_internal_links_resolve() -> None:
    """Every relative markdown link on an authored page points at a file that exists.

    Zensical's static output serves a page at `page/`, so a link written to `page.md` is
    rewritten on build and a link written to `page/` is not checkable from the source. The
    source convention is therefore `.md`, and this holds the pages to it.
    """
    broken = []
    for page in sorted(DOCS.glob("*.md")):
        if page.name in harvest.UNASSERTED - {harvest.INDEX_PAGE.name}:
            continue
        for match in re.finditer(
            r"\]\((?!https?://|#|mailto:)([^)#\s]+)(#[^)\s]*)?\)", page.read_text()
        ):
            target = match.group(1)
            if not (page.parent / target).exists():
                broken.append(f"{page.name} -> {target}")
    assert not broken, "\n".join(broken)


def test_retired_term_stays_retired() -> None:
    """The retired term stays out of the pages.

    "Fingerprint" means ownership verification in the current model literature. This
    study's referent is house style, organization-specific adaptation and source
    attribution, and the word was retired for it. The research log is exempt: it is the
    dated record, and the entries that used the old word were written under it.
    """
    used = [
        page.name
        for page in sorted(DOCS.glob("*.md"))
        if page.name != "research-log.md" and re.search(r"fingerprint", page.read_text(), re.I)
    ]
    used += [
        name
        for name in ("README.md", "ROADMAP.md")
        if re.search(r"fingerprint", (ROOT / name).read_text(), re.I)
    ]
    assert not used, f"retired term in: {used}"
