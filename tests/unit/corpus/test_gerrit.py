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


def _c(cid: str, second: int = 0) -> dict[str, Any]:
    return {"id": cid, "updated": f"2024-11-13 08:00:{second:02d}.000000000"}


def test_fetch_changes_pages_until_more_changes_is_absent() -> None:
    pages = [
        _page([_c("c1", 3), _c("c2", 2)], more=True),
        _page([_c("c3", 1)], more=False),
        _page([_c("c3", 1)], more=False),  # the truncation probe finds nothing unserved
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
    replies = [
        (429, {"Retry-After": "7"}, ""),
        (200, {}, _page([_c("c1")], more=False)),
        (200, {}, _page([_c("c1")], more=False)),
    ]
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


def _served_then_probed(served: list[dict[str, Any]], probe: list[dict[str, Any]]):
    asked: list[str] = []

    def transport(url: str) -> tuple[int, dict[str, str], str]:
        asked.append(url)
        return 200, {}, _page(served if len(asked) == 1 else probe, more=False)

    return transport, asked


def test_fetch_changes_refuses_a_query_the_server_cut_short() -> None:
    """chromium-review ends at 10,000 results with no `_more_changes`, mid-month."""
    served = [
        {"id": "c1", "updated": "2024-11-20 10:00:00.000000000"},
        {"id": "c2", "updated": "2024-11-13 08:28:51.000000000"},
    ]
    older = [served[1], {"id": "c3", "updated": "2024-11-13 08:27:51.000000000"}]
    transport, asked = _served_then_probed(served, older)
    with pytest.raises(RuntimeError, match="truncated"):
        fetch_changes("https://g/", "status:merged", transport=transport)
    from urllib.parse import unquote

    assert unquote(asked[1]).endswith('(status:merged) before:"2024-11-13 08:28:51 +0000"&n=2&S=0')


def test_fetch_changes_accepts_a_probe_that_finds_only_the_oldest_change_again() -> None:
    # Gerrit's before: includes its own second, so a complete query's probe returns the
    # oldest change it already served. Checked live against v8/v8 2025-10.
    served = [{"id": "c1", "updated": "2025-10-01 01:58:13.000000000"}]
    transport, asked = _served_then_probed(served, served)
    changes, record = fetch_changes("https://g/", "q", transport=transport)
    assert [c["id"] for c in changes] == ["c1"] and len(asked) == 2


def _capped_gerrit(changes: list[dict[str, Any]], cap: int, page: int):
    """A fake host that serves at most `cap` results and then drops `_more_changes`.

    Newest-updated first, as Gerrit orders them, and `before:` inclusive of its own second.
    Changes within one second come back in a fixed order, as a real index returns them.
    """
    from urllib.parse import parse_qs, urlsplit

    ordered = sorted(changes, key=lambda c: c["updated"], reverse=True)

    def transport(url: str) -> tuple[int, dict[str, str], str]:
        params = parse_qs(urlsplit(url).query)
        query, n, start = params["q"][0], int(params["n"][0]), int(params.get("S", ["0"])[0])
        rows = ordered
        if ' before:"' in query:
            bound = query.split(' before:"')[1][:19]
            rows = [c for c in rows if c["updated"][:19] <= bound]
        served = rows[: min(len(rows), cap)][start : start + min(n, page)]
        more = start + len(served) < min(len(rows), cap)
        return 200, {}, _page([dict(c) for c in served], more=more)

    return transport


def test_fetch_changes_refuses_a_cut_inside_a_run_of_one_second() -> None:
    """Cap 10, 12 matching, the last 7 sharing one second: the cut falls inside that second.

    A fixed five-result probe returned five served changes from that second and accepted
    10 of 12 as complete.
    """
    changes = [_c(f"n{i}", 59 - i) for i in range(5)] + [_c(f"s{i}", 0) for i in range(7)]
    transport = _capped_gerrit(changes, cap=10, page=4)
    with pytest.raises(RuntimeError, match="truncated"):
        fetch_changes("https://g/", "status:merged", transport=transport)


def test_fetch_changes_accepts_a_complete_query_ending_in_a_run_of_one_second() -> None:
    changes = [_c(f"n{i}", 59 - i) for i in range(3)] + [_c(f"s{i}", 0) for i in range(7)]
    transport = _capped_gerrit(changes, cap=10, page=4)
    served, record = fetch_changes("https://g/", "status:merged", transport=transport)
    assert len(served) == 10 and record["count"] == 10


def test_fetch_changes_refuses_changes_it_cannot_check() -> None:
    def transport(url: str) -> tuple[int, dict[str, str], str]:
        return 200, {}, _page([{"id": "c1"}], more=False)

    with pytest.raises(RuntimeError, match="without `updated`"):
        fetch_changes("https://g/", "q", transport=transport)
