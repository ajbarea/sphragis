"""The rule digests whose drift would cost a network refetch or rebuild hold their pins.

`BUILD_RULES`, `FETCH_RULES` and `GITHUB_RULES` digest their source files' whole text, comments
included, so any edit to `build.py`, `notedb.py` or `github.py` marks every month built or fetched
under the old text as stale, and the loader then refuses it: a rebuild or refetch over the
network, weeks for the GitHub family. A comment edit to `github.py` did that twice (#102, then a
docs change reverted 2026-10-18). A deliberate rules change updates its pin here in the same
commit, with the rebuild it implies. `RULES_VERSION` is not pinned: a label change is re-refined
from disk, offline.

`github.py`'s comment on GitHub's Acceptable Use Policies predates the 2026-10-08 reading that
GitHub's confirmation is awaited (research log); it stays as written because the digest covers it.
"""

from __future__ import annotations

import pytest

from sphragis.corpus import rules

#: The digests the collected corpora carry: Qt's granted build and the GitHub family's months
#: (`<org>/examples/<month>.source.json`), and the git route's fetches.
PINNED = {
    "BUILD_RULES": "3d29d529390c1eea",
    "FETCH_RULES": "296c5a080b125562",
    "GITHUB_RULES": "56b5374b852d17d7",
}


@pytest.mark.parametrize(("name", "pin"), sorted(PINNED.items()))
def test_a_network_bound_digest_holds_its_pin(name: str, pin: str) -> None:
    assert getattr(rules, name) == pin, (
        f"{name} moved from {pin}: every month collected under it now reads as stale. Revert the "
        "edit to its source files, or update the pin here with the rebuild it implies."
    )
