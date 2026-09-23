"""Gerrit REST access: XSSI-prefixed JSON, cursor paging, polite retry."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

_XSSI_PREFIX = ")]}'"
_MAX_RETRIES = 5
_RETRYABLE = frozenset({429, 500, 502, 503, 504})

Transport = Callable[[str], tuple[int, dict[str, str], str]]


def parse_response(text: str) -> Any:
    """Strip Gerrit's XSSI guard and decode the JSON body."""
    if not text.startswith(_XSSI_PREFIX):
        raise ValueError("response is missing Gerrit's XSSI prefix")
    return json.loads(text[text.index("\n") + 1 :])


def _get(url: str, transport: Transport, sleep: Callable[[float], None], retries: list[int]) -> str:
    last_status = 0
    for attempt in range(_MAX_RETRIES):
        last_status, headers, body = transport(url)
        if last_status == 200:
            return body
        if last_status not in _RETRYABLE:
            raise RuntimeError(f"gerrit returned {last_status} for {url}")
        retries[0] += 1
        sleep(float(headers.get("Retry-After", 2**attempt)))
    raise RuntimeError(f"gerrit returned {last_status} for {url} after {_MAX_RETRIES} attempts")


def fetch_comments(
    base_url: str,
    change_number: int,
    *,
    transport: Transport,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, list[dict[str, Any]]]:
    """Inline review comments for one change, keyed by file path."""
    url = f"{base_url.rstrip('/')}/changes/{change_number}/comments"
    return parse_response(_get(url, transport, sleep, [0]))


def fetch_diff(
    base_url: str,
    change_number: int,
    revision: int,
    path: str,
    *,
    base: int,
    transport: Transport,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Gerrit's own diff of one file between two patch sets.

    Using the server's diff rather than re-diffing fetched file contents keeps the hunk
    boundaries identical to the ones the reviewer saw, and the file-content endpoint
    returns base64 whole files with no alignment information anyway.
    """
    encoded = quote(path, safe="")
    url = (
        f"{base_url.rstrip('/')}/changes/{change_number}/revisions/{revision}"
        f"/files/{encoded}/diff?base={base}"
    )
    return parse_response(_get(url, transport, sleep, [0]))


def created_on_or_after(changes: Sequence[Mapping[str, Any]], cutoff: str) -> list[dict[str, Any]]:
    """Keep only changes *created* on or after ``cutoff``.

    Gerrit's `after:` query operator filters on last update, not creation, so a bounded
    query admits changes created long before the window. Verified live on 2026-09-14:
    `after:2024-10-01 before:2024-10-03` returned a change created 2024-08-26. The
    post-cutoff contamination argument rests on creation date, so it is enforced here
    rather than delegated to the query.
    """
    return [dict(c) for c in changes if c["created"][:10] >= cutoff[:10]]


def _refuse_if_truncated(
    base_url: str,
    query: str,
    changes: Sequence[Mapping[str, Any]],
    transport: Transport,
    sleep: Callable[[float], None],
    retries: list[int],
) -> None:
    """Raise if the server stopped paging before ``query`` was exhausted.

    A missing `_more_changes` is not proof of the end. chromium-review stops at 10,000 results
    and drops the flag on the last page it serves: measured 2026-09-22, chromium/src's merged
    changes for 2024-11 ended at exactly 10,000, the last one updated on the 13th. Results come
    newest-updated first, so anything the query matches at or before the oldest second served
    that was not served means the tail was cut.

    `before:` includes its own second, and a cut can fall inside a run of changes sharing that
    second, so the probe reads until it has one more change than were served at that second,
    paging if the host serves fewer a page. An unserved change then always has room to appear
    rather than being crowded out by served ones.
    """
    if not changes:
        return
    missing_stamp = [c.get("id", c.get("_number")) for c in changes if "updated" not in c]
    if missing_stamp:
        raise RuntimeError(
            f"{base_url} returned changes without `updated` ({missing_stamp[:3]}), so whether "
            f"{query!r} was truncated cannot be checked"
        )
    oldest = min(str(c["updated"])[:19] for c in changes)
    at_oldest = sum(1 for c in changes if str(c["updated"])[:19] == oldest)
    seen = {c.get("id", c.get("_number")) for c in changes}
    probe = quote(f'({query}) before:"{oldest} +0000"')
    missing: list[Mapping[str, Any]] = []
    read = 0
    while not missing and read <= at_oldest:
        url = f"{base_url.rstrip('/')}/changes/?q={probe}&n={at_oldest + 1 - read}&S={read}"
        page = parse_response(_get(url, transport, sleep, retries))
        read += len(page)
        missing = [c for c in page if c.get("id", c.get("_number")) not in seen]
        if not page or not page[-1].get("_more_changes"):
            break
    if missing:
        raise RuntimeError(
            f"{base_url} stopped after {len(changes)} results for {query!r} but more match "
            f"at or before {oldest}: the server truncated the query. Narrow it (fewer projects "
            "or a shorter date range) rather than keep a partial snapshot."
        )


def fetch_changes(
    base_url: str,
    query: str,
    *,
    transport: Transport,
    page_size: int = 100,
    options: tuple[str, ...] = ("ALL_REVISIONS", "ALL_FILES", "DETAILED_ACCOUNTS"),
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Page through ``query``; return the changes and the record of how they were fetched."""
    started = datetime.now(UTC).isoformat()
    option_params = "".join(f"&o={opt}" for opt in options)
    changes: list[dict[str, Any]] = []
    retries = [0]
    pages = 0
    start = 0
    while True:
        url = (
            f"{base_url.rstrip('/')}/changes/?q={quote(query)}"
            f"&n={page_size}&S={start}{option_params}"
        )
        page = parse_response(_get(url, transport, sleep, retries))
        pages += 1
        changes.extend(page)
        if not page or not page[-1].get("_more_changes"):
            break
        start += len(page)
    _refuse_if_truncated(base_url, query, changes, transport, sleep, retries)
    record = {
        "base_url": base_url,
        "query": query,
        "options": list(options),
        "page_size": page_size,
        "pages": pages,
        "count": len(changes),
        "retries": retries[0],
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
    }
    return changes, record
