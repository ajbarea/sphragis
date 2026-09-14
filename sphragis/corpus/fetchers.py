"""Fetchers that scrub identities at ingestion.

`fetch` scrubs the change payload, so a change's owner is a pseudonym by the time it
reaches disk. Comments have to be scrubbed with the *same salt* or the author filter in
`build` compares a pseudonym against a raw account id and silently matches nothing. That
happened; `is_reviewer_comment` now raises on the mismatch, and these fetchers are how the
mismatch is avoided rather than merely detected.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sphragis.corpus.gerrit import Transport, fetch_comments, fetch_diff
from sphragis.corpus.scrub import scrub


def scrubbed_comment_fetcher(
    base_url: str, salt: str, *, transport: Transport
) -> Callable[[int], Mapping[str, Sequence[Mapping[str, Any]]]]:
    """Comment fetcher whose authors are pseudonymised with ``salt``."""

    def fetch(change_number: int) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        return scrub(fetch_comments(base_url, change_number, transport=transport), salt)

    return fetch


def scrubbed_diff_fetcher(
    base_url: str, *, transport: Transport
) -> Callable[[int, int, str, int], Mapping[str, Any]]:
    """Diff fetcher.

    Deliberately does not scrub: a diff payload carries no account objects, and running
    the identity sweep over it would rewrite anything in the source that merely looks like
    an email address, corrupting the code the study measures.
    """

    def fetch(change_number: int, revision: int, path: str, base: int) -> Mapping[str, Any]:
        return fetch_diff(base_url, change_number, revision, path, base=base, transport=transport)

    return fetch
