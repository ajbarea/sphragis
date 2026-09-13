"""Gerrit REST paging, XSSI prefix, and retry."""

from __future__ import annotations

import json
from typing import Any

import pytest

from sphragis.corpus.gerrit import fetch_changes, parse_response

XSSI = ")]}'\n"


def _page(items: list[dict[str, Any]], more: bool) -> str:
    if items and more:
        items = [*items[:-1], {**items[-1], "_more_changes": True}]
    return XSSI + json.dumps(items)


def test_parse_response_strips_the_xssi_prefix() -> None:
    assert parse_response(XSSI + '{"a": 1}') == {"a": 1}


def test_parse_response_rejects_a_body_without_the_prefix() -> None:
    with pytest.raises(ValueError, match="XSSI"):
        parse_response('{"a": 1}')


def test_fetch_changes_pages_until_more_changes_is_absent() -> None:
    pages = [
        _page([{"id": "c1"}, {"id": "c2"}], more=True),
        _page([{"id": "c3"}], more=False),
    ]
    seen: list[str] = []

    def transport(url: str) -> tuple[int, dict[str, str], str]:
        seen.append(url)
        return 200, {}, pages[len(seen) - 1]

    changes, record = fetch_changes("https://g/", "status:merged", transport=transport, page_size=2)
    assert [c["id"] for c in changes] == ["c1", "c2", "c3"]
    assert "S=0" in seen[0] and "S=2" in seen[1]
    assert record["pages"] == 2 and record["count"] == 3
    assert record["query"] == "status:merged"
    assert record["started_at"] and record["finished_at"]


def test_fetch_changes_retries_on_429_and_honours_retry_after() -> None:
    replies = [(429, {"Retry-After": "7"}, ""), (200, {}, _page([{"id": "c1"}], more=False))]
    slept: list[float] = []

    def transport(url: str) -> tuple[int, dict[str, str], str]:
        return replies.pop(0)  # type: ignore[return-value]

    changes, record = fetch_changes("https://g/", "q", transport=transport, sleep=slept.append)
    assert [c["id"] for c in changes] == ["c1"]
    assert slept == [7.0]
    assert record["retries"] == 1


def test_fetch_changes_gives_up_after_the_retry_budget() -> None:
    def transport(url: str) -> tuple[int, dict[str, str], str]:
        return 503, {}, ""

    with pytest.raises(RuntimeError, match="503"):
        fetch_changes("https://g/", "q", transport=transport, sleep=lambda _: None)


def test_fetch_changes_does_not_retry_a_client_error() -> None:
    def transport(url: str) -> tuple[int, dict[str, str], str]:
        return 404, {}, ""

    with pytest.raises(RuntimeError, match="404"):
        fetch_changes("https://g/", "q", transport=transport, sleep=lambda _: None)
