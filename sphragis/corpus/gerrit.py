"""Gerrit REST access: XSSI-prefixed JSON, cursor paging, polite retry."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
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
