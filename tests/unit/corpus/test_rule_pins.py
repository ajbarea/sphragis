"""The rule digests whose drift would cost a network refetch or rebuild hold their pins.

`BUILD_RULES`, `FETCH_RULES` and `GITHUB_RULES` digest their source files' whole text, comments
included (`rules.BUILD_SOURCES`, `FETCH_SOURCES`, `GITHUB_SOURCES`), so any edit to one of those
files marks every month built or fetched under the old text as stale, and the loader then refuses
it: a rebuild or refetch over the network, weeks for the GitHub family. `GITHUB_RULES` moved twice
under collected months: by a code change in #101, restored in #102, and by a comment edit in #107,
restored 2026-10-18. A deliberate rules change updates its pin here in the same commit, with the
rebuild it implies. `RULES_VERSION` is not pinned: a label change is re-refined from disk, offline.

`github.py`'s comment on GitHub's Acceptable Use Policies predates the 2026-10-08 reading that
GitHub's confirmation is awaited (research log); it stays as written because the digest covers it.
"""

from __future__ import annotations

import pytest

from sphragis.corpus import rules

#: `BUILD_RULES` as every Gerrit and GitHub month's `examples/<month>.source.json` records it,
#: `GITHUB_RULES` as every GitHub month's `raw/<month>.record.json` does. No month records
#: `FETCH_RULES` yet; its pin marks the code the git route's first fetch will record.
PINNED = {
    "BUILD_RULES": ("3d29d529390c1eea", rules.BUILD_SOURCES),
    "FETCH_RULES": ("5c939338684181c8", rules.FETCH_SOURCES),
    "GITHUB_RULES": ("56b5374b852d17d7", rules.GITHUB_SOURCES),
}


@pytest.mark.parametrize("name", sorted(PINNED))
def test_a_network_bound_digest_holds_its_pin(name: str) -> None:
    pin, sources = PINNED[name]
    assert getattr(rules, name) == pin, (
        f"{name} moved from {pin}: an edit to {', '.join(sources)} stales every month collected "
        "under it. Revert the edit, or update the pin here with the rebuild it implies."
    )
