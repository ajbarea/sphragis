"""Fetchers that scrub at ingestion, so the author filter can actually compare."""

from __future__ import annotations

import json
from typing import Any

from sphragis.corpus.fetchers import scrubbed_comment_fetcher, scrubbed_diff_fetcher
from sphragis.corpus.scrub import pseudonym

XSSI = ")]}'\n"
SALT = "test-salt"


def _transport(payload: object):
    def transport(url: str) -> tuple[int, dict[str, str], str]:
        return 200, {}, XSSI + json.dumps(payload)

    return transport


def test_comment_authors_come_back_pseudonymised_with_the_given_salt() -> None:
    payload = {
        "a.py": [
            {
                "patch_set": 1,
                "line": 3,
                "message": "fix it",
                "author": {"_account_id": 1000096, "name": "Alice", "email": "a@b.co"},
            }
        ]
    }
    fetch = scrubbed_comment_fetcher("https://g/", SALT, transport=_transport(payload))
    got = fetch(42)
    author = got["a.py"][0]["author"]
    assert author == {"_account_id": pseudonym(1000096, SALT)}
    assert "Alice" not in json.dumps(got)


def test_the_message_body_survives_scrubbing() -> None:
    payload = {"a.py": [{"patch_set": 1, "line": 1, "message": "use a literal"}]}
    fetch = scrubbed_comment_fetcher("https://g/", SALT, transport=_transport(payload))
    assert fetch(1)["a.py"][0]["message"] == "use a literal"


def test_a_scrubbed_comment_author_matches_a_scrubbed_change_owner() -> None:
    # The whole point: both sides pseudonymised with one salt compare correctly, which is
    # what the str-vs-int guard in build exists to enforce.
    from sphragis.corpus.build import is_reviewer_comment

    owner_raw = 1000096
    payload = {
        "a.py": [
            {"patch_set": 1, "line": 1, "message": "Done", "author": {"_account_id": owner_raw}}
        ]
    }
    fetch = scrubbed_comment_fetcher("https://g/", SALT, transport=_transport(payload))
    comment = fetch(1)["a.py"][0]
    assert is_reviewer_comment(comment, pseudonym(owner_raw, SALT)) is False
    assert is_reviewer_comment(comment, pseudonym(999, SALT)) is True


def test_the_diff_fetcher_leaves_code_untouched() -> None:
    # Scrubbing a diff would corrupt the code under study; only identities are stripped,
    # and a diff payload carries none.
    payload: dict[str, Any] = {"content": [{"a": ["x = 1  # a@b.co"], "b": ["x = 2"]}]}
    fetch = scrubbed_diff_fetcher("https://g/", transport=_transport(payload))
    assert fetch(1, 2, "a.py", 1)["content"][0]["a"] == ["x = 1  # a@b.co"]
