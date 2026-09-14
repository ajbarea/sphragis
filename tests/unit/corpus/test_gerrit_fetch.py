"""Comment and diff endpoints, and the creation-date filter the query cannot do."""

from __future__ import annotations

import json

import pytest

from sphragis.corpus.gerrit import created_on_or_after, fetch_comments, fetch_diff

XSSI = ")]}'\n"


def _transport(payload: object, seen: list[str] | None = None):
    def transport(url: str) -> tuple[int, dict[str, str], str]:
        if seen is not None:
            seen.append(url)
        return 200, {}, XSSI + json.dumps(payload)

    return transport


def test_fetch_comments_returns_the_per_file_mapping() -> None:
    payload = {"a.py": [{"patch_set": 1, "line": 7, "message": "fix"}]}
    got = fetch_comments("https://g/", 123, transport=_transport(payload))
    assert got["a.py"][0]["line"] == 7


def test_fetch_diff_asks_for_the_previous_patch_set_as_base() -> None:
    seen: list[str] = []
    fetch_diff("https://g/", 123, 2, "pkg/a.py", base=1, transport=_transport({}, seen))
    url = seen[0]
    assert "/changes/123/revisions/2/files/" in url
    assert "base=1" in url


def test_fetch_diff_url_encodes_the_file_path() -> None:
    seen: list[str] = []
    fetch_diff("https://g/", 1, 2, "pkg/a b.py", base=1, transport=_transport({}, seen))
    assert "pkg%2Fa%20b.py" in seen[0]


def test_created_on_or_after_keeps_only_changes_created_in_the_window() -> None:
    # Gerrit's after: operator filters on LAST UPDATE, so a change created in August can
    # come back from an October query. Verified live 2026-09-14 against review.opendev.org.
    changes = [
        {"_number": 1, "created": "2024-08-26 08:41:08.000000000"},
        {"_number": 2, "created": "2024-10-02 11:00:00.000000000"},
        {"_number": 3, "created": "2025-01-05 09:00:00.000000000"},
    ]
    kept = created_on_or_after(changes, "2024-10-01")
    assert [c["_number"] for c in kept] == [2, 3]


def test_created_on_or_after_is_inclusive_of_the_cutoff_day() -> None:
    changes = [{"_number": 1, "created": "2024-10-01 00:00:00.000000000"}]
    assert len(created_on_or_after(changes, "2024-10-01")) == 1


def test_created_on_or_after_rejects_a_change_with_no_creation_date() -> None:
    with pytest.raises(KeyError):
        created_on_or_after([{"_number": 1}], "2024-10-01")
