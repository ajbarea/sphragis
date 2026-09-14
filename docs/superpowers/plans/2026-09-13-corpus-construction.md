# Gerrit Corpus Construction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build the staged pipeline that turns OpenStack and Qt Gerrit review history into a frozen, deduplicated, window-split corpus of code-refinement examples.

**Architecture:** Seven pure-Python modules under `sphragis/corpus/`, each one stage, each writing an immutable artifact plus a manifest fragment. Every stage is a pure function over data plus a thin IO shell, so every stage is testable without a network. The CLI (`python -m sphragis.corpus <stage>`) is the only place that touches disk or HTTP.

**Tech Stack:** Python 3.12-3.14, `uv`, `ruff`, `ty`, `pytest`. Standard library only for this plan; no new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-09-13-gerrit-review-corpus-harness-design.md`

## Scope

The spec covers three separable subsystems. This plan is the first; each produces working, testable software on its own.

| Plan | Covers | Spec sections |
|---|---|---|
| **A (this one)** | corpus construction: fetch, scrub, build, dedup, split, freeze, manifest, CLI | 1, 2, 7 |
| B | measurement: the metric ladder, the contamination battery, the statistics | 2 (`score`), 3, 6 |
| C | the experiment: 3 by 2 grid, seeds, TIGRIS submission, power analysis | 4, 5 |

Plan A is the blocker for both others and for every checklist item in `papers/org-fingerprint/STAGE1-SKELETON.md`.

## Global Constraints

- Python `>=3.12,<3.15`. No new runtime dependency; standard library only.
- `make lint` (ruff format check + ruff lint + ty) and `make test` must pass before every commit.
- Every module carries `from __future__ import annotations` and full type annotations, matching `sphragis/provenance.py`.
- No stage may contact a Gerrit server except `fetch`. Every other stage reads frozen artifacts.
- Identity stripping runs inside `fetch`, before the first byte is persisted. No raw contributor identity is ever written to disk.
- The salt lives in `.env` as `SPHRAGIS_CORPUS_SALT` and is never committed.
- Comments in code are execution help only. No "why we chose", no issue numbers, no narrating the edit.

---

## Spec deviation to record

**Dedup stage 3.** The spec says suffix array. A linear-time suffix array needs a C extension (`pydivsufsort`), and a pure-Python one is O(n^2 log n) on the concatenated corpus, which will not hold at full scale. This plan implements stage 3 as **corpus-wide shingle-frequency detection**: a k-gram appearing in more than `max_docs` distinct examples is repeated text, and an example whose repeated fraction exceeds a threshold is dropped. Same target (substrings recurring across otherwise distinct examples), pure standard library, and testable.

Task 5 includes the spec amendment. Do not implement stage 3 as specified and do not skip the amendment.


> **Status: executed.** Every task below is built, tested and merged. Kept as the record of how, not as a queue.

---

## File Structure

| File | Responsibility |
|---|---|
| `sphragis/corpus/__init__.py` | package marker, public re-exports |
| `sphragis/corpus/scrub.py` | salted pseudonymization; recursive identity stripping |
| `sphragis/corpus/manifest.py` | corpus manifest, window hashes, verification |
| `sphragis/corpus/gerrit.py` | Gerrit REST: XSSI prefix, paging, retry, rate limit |
| `sphragis/corpus/examples.py` | change JSON to refinement pairs via hunk diffing |
| `sphragis/corpus/dedup.py` | three-stage deduplication |
| `sphragis/corpus/split.py` | time windows, change-id grouping, test-window seal |
| `sphragis/corpus/cli.py` | `python -m sphragis.corpus <stage>`; the only IO shell |
| `sphragis/provenance.py` | **modify**: extract `provenance_header()` for reuse |

Tests mirror the module names under `tests/unit/corpus/`.

---

### Task 1: Identity scrubbing

First because it is pure, has no dependencies, and nothing else may run before it exists: the spec forbids persisting raw identity.

**Files:**
- Create: `sphragis/corpus/__init__.py`, `sphragis/corpus/scrub.py`
- Test: `tests/unit/corpus/test_scrub.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `pseudonym(value: object, salt: str) -> str` — 12 lowercase hex characters
  - `scrub(obj: Any, salt: str) -> Any` — recursive, returns a new structure

- [x] **Step 1: Write the failing test**

```python
"""Salted identity stripping at ingestion."""

from __future__ import annotations

from sphragis.corpus.scrub import pseudonym, scrub

SALT = "test-salt"


def test_pseudonym_is_stable_salted_and_short() -> None:
    assert pseudonym("alice@example.org", SALT) == pseudonym("alice@example.org", SALT)
    assert pseudonym("alice@example.org", SALT) != pseudonym("alice@example.org", "other")
    assert pseudonym("alice@example.org", SALT) != pseudonym("bob@example.org", SALT)
    token = pseudonym("alice@example.org", SALT)
    assert len(token) == 12 and all(c in "0123456789abcdef" for c in token)


def test_scrub_replaces_account_info_with_a_single_pseudonym() -> None:
    change = {
        "owner": {
            "_account_id": 1000096,
            "name": "Alice Example",
            "email": "alice@example.org",
            "username": "alice",
        }
    }
    out = scrub(change, SALT)
    assert out["owner"] == {"_account_id": pseudonym(1000096, SALT)}
    assert "alice" not in repr(out)


def test_scrub_redacts_emails_in_free_text() -> None:
    out = scrub({"message": "ping bob@example.org about this"}, SALT)
    assert "bob@example.org" not in out["message"]
    assert pseudonym("bob@example.org", SALT) in out["message"]


def test_scrub_preserves_structural_fields_and_does_not_mutate_input() -> None:
    change = {
        "change_id": "I1234",
        "project": "openstack/nova",
        "created": "2024-10-02 11:00:00.000000000",
        "revisions": {"abc": {"_number": 1}},
        "owner": {"_account_id": 7, "name": "Carol"},
    }
    out = scrub(change, SALT)
    assert out["change_id"] == "I1234"
    assert out["project"] == "openstack/nova"
    assert out["created"] == "2024-10-02 11:00:00.000000000"
    assert out["revisions"]["abc"]["_number"] == 1
    assert change["owner"]["name"] == "Carol"
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/corpus/test_scrub.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sphragis.corpus'`

- [x] **Step 3: Write minimal implementation**

`sphragis/corpus/__init__.py`:

```python
"""Gerrit review corpus construction."""
```

`sphragis/corpus/scrub.py`:

```python
"""Salted identity stripping, applied before any raw record reaches disk."""

from __future__ import annotations

import hashlib
import re
from typing import Any

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IDENTITY_KEYS = frozenset(
    {"name", "email", "username", "display_name", "secondary_emails", "avatars"}
)


def pseudonym(value: object, salt: str) -> str:
    """Stable 12-hex pseudonym for one identity value."""
    digest = hashlib.sha256(f"{salt}:{value}".encode())
    return digest.hexdigest()[:12]


def _scrub_text(text: str, salt: str) -> str:
    return _EMAIL.sub(lambda m: pseudonym(m.group(0), salt), text)


def scrub(obj: Any, salt: str) -> Any:
    """Recursively replace Gerrit account identities with salted pseudonyms."""
    if isinstance(obj, dict):
        if "_account_id" in obj:
            return {"_account_id": pseudonym(obj["_account_id"], salt)}
        return {k: (None if k in _IDENTITY_KEYS else scrub(v, salt)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(item, salt) for item in obj]
    if isinstance(obj, str):
        return _scrub_text(obj, salt)
    return obj
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/corpus/test_scrub.py -v`
Expected: 4 passed

- [x] **Step 5: Lint and commit**

```bash
make lint
git add sphragis/corpus/__init__.py sphragis/corpus/scrub.py tests/unit/corpus/test_scrub.py
git commit -m "feat(corpus): salted identity stripping for Gerrit records"
```

---

### Task 2: Corpus manifest

**Files:**
- Modify: `sphragis/provenance.py` — extract the shared header
- Create: `sphragis/corpus/manifest.py`
- Test: `tests/unit/corpus/test_manifest.py`, and `tests/test_provenance.py` must still pass unchanged

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `provenance.provenance_header() -> dict[str, Any]` — `generated_at`, `git`, `python`, `platform`, `packages`
  - `window_hash(example_ids: Iterable[str]) -> str` — sha256 hex over sorted ids
  - `corpus_manifest(*, org: str, windows: Mapping[str, Sequence[str]], stats: Mapping[str, Any]) -> dict[str, Any]`
  - `verify(manifest: Mapping[str, Any], windows: Mapping[str, Sequence[str]]) -> list[str]` — mismatch descriptions, empty when clean

- [x] **Step 1: Write the failing test**

```python
"""Corpus manifest: counts, hashes, and the verification that guards them."""

from __future__ import annotations

from sphragis.corpus.manifest import corpus_manifest, verify, window_hash

WINDOWS = {"pilot": ["b", "a"], "train": ["c"], "dev": [], "test": []}


def test_window_hash_is_order_independent_and_content_sensitive() -> None:
    assert window_hash(["a", "b"]) == window_hash(["b", "a"])
    assert window_hash(["a", "b"]) != window_hash(["a", "c"])


def test_corpus_manifest_records_counts_hashes_and_provenance() -> None:
    m = corpus_manifest(org="openstack", windows=WINDOWS, stats={"dropped_no_comment": 4})
    assert m["org"] == "openstack"
    assert m["counts"] == {"pilot": 2, "train": 1, "dev": 0, "test": 0}
    assert m["hashes"]["pilot"] == window_hash(["a", "b"])
    assert m["stats"]["dropped_no_comment"] == 4
    assert m["git"] and m["packages"] is not None and m["generated_at"]


def test_verify_is_clean_for_the_windows_it_was_built_from() -> None:
    assert verify(corpus_manifest(org="qt", windows=WINDOWS, stats={}), WINDOWS) == []


def test_verify_reports_count_and_hash_drift() -> None:
    m = corpus_manifest(org="qt", windows=WINDOWS, stats={})
    problems = verify(m, {**WINDOWS, "train": ["c", "d"]})
    assert len(problems) == 1 and "train" in problems[0]
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/corpus/test_manifest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sphragis.corpus.manifest'`

- [x] **Step 3: Write minimal implementation**

In `sphragis/provenance.py`, add above `run_manifest`:

```python
def provenance_header() -> dict[str, Any]:
    """The static provenance every manifest in this repo carries."""
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "git": {
            "commit": _git("rev-parse", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        },
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": _package_versions(),
    }
```

and rewrite `run_manifest` to use it:

```python
def run_manifest(*, run_config: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    """Capture a reproducibility manifest for one federated run."""
    return {
        **provenance_header(),
        "run_config": dict(run_config),
        "metrics": dict(metrics),
    }
```

`sphragis/corpus/manifest.py`:

```python
"""Corpus manifest: per-window counts and content hashes, and their verification."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from phalanx.provenance import provenance_header


def window_hash(example_ids: Iterable[str]) -> str:
    """Content hash over a window, independent of the order ids arrive in."""
    digest = hashlib.sha256()
    for example_id in sorted(example_ids):
        digest.update(example_id.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def corpus_manifest(
    *, org: str, windows: Mapping[str, Sequence[str]], stats: Mapping[str, Any]
) -> dict[str, Any]:
    """Capture counts, hashes and provenance for one organization's corpus."""
    return {
        **provenance_header(),
        "org": org,
        "counts": {name: len(ids) for name, ids in windows.items()},
        "hashes": {name: window_hash(ids) for name, ids in windows.items()},
        "stats": dict(stats),
    }


def verify(manifest: Mapping[str, Any], windows: Mapping[str, Sequence[str]]) -> list[str]:
    """Describe every way ``windows`` disagrees with ``manifest``; empty means clean."""
    problems: list[str] = []
    for name, ids in windows.items():
        expected_count = manifest["counts"].get(name)
        expected_hash = manifest["hashes"].get(name)
        if expected_hash is None:
            problems.append(f"{name}: absent from the manifest")
            continue
        actual_hash = window_hash(ids)
        if expected_count != len(ids) or expected_hash != actual_hash:
            problems.append(
                f"{name}: manifest says {expected_count} items ({expected_hash[:12]}), "
                f"found {len(ids)} ({actual_hash[:12]})"
            )
    return problems
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/corpus/test_manifest.py tests/test_provenance.py -v`
Expected: all passed. `test_provenance.py` is unchanged and must not need editing; if it does, `provenance_header` was extracted wrongly.

- [x] **Step 5: Lint and commit**

```bash
make lint
git add sphragis/provenance.py sphragis/corpus/manifest.py tests/unit/corpus/test_manifest.py
git commit -m "feat(corpus): manifest with per-window counts and content hashes"
```

---

### Task 3: Gerrit REST client

**Files:**
- Create: `sphragis/corpus/gerrit.py`
- Test: `tests/unit/corpus/test_gerrit.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `parse_response(text: str) -> Any` — strips Gerrit's XSSI prefix
  - `Transport = Callable[[str], tuple[int, dict[str, str], str]]` — `(url) -> (status, headers, body)`
  - `fetch_changes(base_url: str, query: str, *, transport: Transport, page_size: int = 100, sleep: Callable[[float], None] = time.sleep) -> tuple[list[dict], dict[str, Any]]` — `(changes, fetch_record)`

The transport seam is what keeps every test offline. The CLI supplies a real `urllib`-backed transport in Task 7.

- [x] **Step 1: Write the failing test**

```python
"""Gerrit REST paging, XSSI prefix, and retry."""

from __future__ import annotations

import json

import pytest

from sphragis.corpus.gerrit import fetch_changes, parse_response

XSSI = ")]}'\n"


def _page(items: list[dict], more: bool) -> str:
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
        return replies.pop(0)

    changes, record = fetch_changes("https://g/", "q", transport=transport, sleep=slept.append)
    assert [c["id"] for c in changes] == ["c1"]
    assert slept == [7.0]
    assert record["retries"] == 1


def test_fetch_changes_gives_up_after_the_retry_budget() -> None:
    def transport(url: str) -> tuple[int, dict[str, str], str]:
        return 503, {}, ""

    with pytest.raises(RuntimeError, match="503"):
        fetch_changes("https://g/", "q", transport=transport, sleep=lambda _: None)
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/corpus/test_gerrit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sphragis.corpus.gerrit'`

- [x] **Step 3: Write minimal implementation**

```python
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
        delay = float(headers.get("Retry-After", 2**attempt))
        sleep(delay)
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
        url = f"{base_url.rstrip('/')}/changes/?q={quote(query)}&n={page_size}&S={start}{option_params}"
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
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/corpus/test_gerrit.py -v`
Expected: 5 passed

- [x] **Step 5: Lint and commit**

```bash
make lint
git add sphragis/corpus/gerrit.py tests/unit/corpus/test_gerrit.py
git commit -m "feat(corpus): Gerrit REST client with paging and polite retry"
```

---

### Task 4: Refinement example construction

**Files:**
- Create: `sphragis/corpus/examples.py`
- Test: `tests/unit/corpus/test_examples.py`

**Interfaces:**
- Consumes: nothing at runtime.
- Produces:
  - `Hunk` — frozen dataclass with `before_start: int`, `before: tuple[str, ...]`, `after_start: int`, `after: tuple[str, ...]`
  - `changed_hunks(before: Sequence[str], after: Sequence[str]) -> list[Hunk]`
  - `build_examples(*, org: str, change: Mapping[str, Any], path: str, before: Sequence[str], after: Sequence[str], comments: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]` — `(examples, drop_counts)`

An example id is `f"{org}:{change_id}:{path}:{before_start}"`, which is stable across reruns and is what `window_hash` consumes.

- [x] **Step 1: Write the failing test**

```python
"""Refinement pairs: changed hunks, comment anchoring, and the drop rules."""

from __future__ import annotations

from sphragis.corpus.examples import build_examples, changed_hunks

CHANGE = {
    "change_id": "I1",
    "project": "openstack/nova",
    "created": "2024-10-02 11:00:00.000000000",
}
BEFORE = ["def f(x):", "    return x+1", "", "def g():", "    pass"]
AFTER = ["def f(x):", "    return x + 1", "", "def g():", "    pass"]


def test_changed_hunks_finds_only_the_changed_region() -> None:
    hunks = changed_hunks(BEFORE, AFTER)
    assert len(hunks) == 1
    assert hunks[0].before == ("    return x+1",)
    assert hunks[0].after == ("    return x + 1",)
    assert hunks[0].before_start == 2


def test_changed_hunks_is_empty_for_identical_content() -> None:
    assert changed_hunks(BEFORE, BEFORE) == []


def test_build_examples_attaches_a_comment_anchored_inside_the_hunk() -> None:
    comments = [{"line": 2, "message": "spaces around the operator"}]
    examples, drops = build_examples(
        org="openstack",
        change=CHANGE,
        path="nova/f.py",
        before=BEFORE,
        after=AFTER,
        comments=comments,
    )
    assert len(examples) == 1 and drops["no_anchored_comment"] == 0
    example = examples[0]
    assert example["before"] == "    return x+1"
    assert example["after"] == "    return x + 1"
    assert example["comments"] == ["spaces around the operator"]
    assert example["org"] == "openstack"
    assert example["project"] == "openstack/nova"
    assert example["path"] == "nova/f.py"
    assert example["created"] == "2024-10-02 11:00:00.000000000"
    assert example["id"] == "openstack:I1:nova/f.py:2"


def test_build_examples_drops_a_hunk_whose_comment_falls_outside_it() -> None:
    examples, drops = build_examples(
        org="openstack",
        change=CHANGE,
        path="nova/f.py",
        before=BEFORE,
        after=AFTER,
        comments=[{"line": 5, "message": "unrelated"}],
    )
    assert examples == [] and drops["no_anchored_comment"] == 1


def test_build_examples_drops_file_level_comments_with_no_line() -> None:
    examples, drops = build_examples(
        org="openstack",
        change=CHANGE,
        path="nova/f.py",
        before=BEFORE,
        after=AFTER,
        comments=[{"message": "looks fine"}],
    )
    assert examples == [] and drops["no_anchored_comment"] == 1
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/corpus/test_examples.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sphragis.corpus.examples'`

- [x] **Step 3: Write minimal implementation**

```python
"""Refinement pairs: a commented hunk at patch set n and its rewrite at n+1."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any


@dataclass(frozen=True)
class Hunk:
    """One changed region, as it reads before and after the rewrite."""

    before_start: int
    before: tuple[str, ...]
    after_start: int
    after: tuple[str, ...]


def changed_hunks(before: Sequence[str], after: Sequence[str]) -> list[Hunk]:
    """Every region that differs between two revisions of one file, 1-indexed."""
    matcher = SequenceMatcher(a=list(before), b=list(after), autojunk=False)
    return [
        Hunk(i1 + 1, tuple(before[i1:i2]), j1 + 1, tuple(after[j1:j2]))
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


def _anchored(hunk: Hunk, comments: Sequence[Mapping[str, Any]]) -> list[str]:
    end = hunk.before_start + max(len(hunk.before), 1) - 1
    return [
        str(c["message"])
        for c in comments
        if isinstance(c.get("line"), int) and hunk.before_start <= c["line"] <= end
    ]


def build_examples(
    *,
    org: str,
    change: Mapping[str, Any],
    path: str,
    before: Sequence[str],
    after: Sequence[str],
    comments: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Build the refinement examples for one file, with the reason each hunk was dropped."""
    examples: list[dict[str, Any]] = []
    drops: Counter[str] = Counter({"no_anchored_comment": 0})
    for hunk in changed_hunks(before, after):
        anchored = _anchored(hunk, comments)
        if not anchored:
            drops["no_anchored_comment"] += 1
            continue
        examples.append(
            {
                "id": f"{org}:{change['change_id']}:{path}:{hunk.before_start}",
                "org": org,
                "project": change.get("project"),
                "change_id": change["change_id"],
                "created": change.get("created"),
                "path": path,
                "before": "\n".join(hunk.before),
                "after": "\n".join(hunk.after),
                "comments": anchored,
            }
        )
    return examples, dict(drops)
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/corpus/test_examples.py -v`
Expected: 5 passed

- [x] **Step 5: Lint and commit**

```bash
make lint
git add sphragis/corpus/examples.py tests/unit/corpus/test_examples.py
git commit -m "feat(corpus): build refinement pairs from commented hunks"
```

---

### Task 5: Three-stage deduplication

**Files:**
- Create: `sphragis/corpus/dedup.py`
- Modify: `docs/superpowers/specs/2026-09-13-gerrit-review-corpus-harness-design.md` — record the stage-3 deviation
- Test: `tests/unit/corpus/test_dedup.py`

**Interfaces:**
- Consumes: example dicts from Task 4 (`id`, `before`, `after`, `org`).
- Produces:
  - `normalize(text: str) -> str`
  - `shingles(text: str, k: int = 5) -> frozenset[str]`
  - `jaccard(a: frozenset[str], b: frozenset[str]) -> float`
  - `dedup(examples: Sequence[Mapping[str, Any]], *, threshold: float = 0.8, k: int = 5, boilerplate_max_docs: int = 20, boilerplate_fraction: float = 0.5) -> tuple[list[dict[str, Any]], dict[str, int]]`

Removal buckets: `exact`, `near_duplicate`, `boilerplate`. The earliest occurrence by `created` is kept.

- [x] **Step 1: Write the failing test**

```python
"""Three-stage dedup: exact, near-duplicate, repeated boilerplate."""

from __future__ import annotations

from sphragis.corpus.dedup import dedup, jaccard, normalize, shingles


def _ex(example_id: str, before: str, after: str, created: str = "2024-10-01") -> dict:
    return {"id": example_id, "before": before, "after": after, "created": created, "org": "o"}


def test_normalize_collapses_whitespace_but_keeps_identifiers() -> None:
    assert normalize("  def  foo_bar( x ) :  ") == "def foo_bar( x ) :"
    assert "foo_bar" in normalize("def foo_bar(x):")


def test_jaccard_is_one_for_identical_and_zero_for_disjoint() -> None:
    assert jaccard(shingles("abcdefgh"), shingles("abcdefgh")) == 1.0
    assert jaccard(frozenset({"a"}), frozenset({"b"})) == 0.0


def test_dedup_removes_exact_duplicates_and_keeps_the_earliest() -> None:
    kept, removed = dedup(
        [
            _ex("late", "x = 1", "x = 2", created="2024-12-01"),
            _ex("early", "x = 1", "x = 2", created="2024-10-01"),
        ]
    )
    assert [e["id"] for e in kept] == ["early"]
    assert removed["exact"] == 1


def test_dedup_removes_near_duplicates_above_the_threshold() -> None:
    base = "the quick brown fox jumps over the lazy dog and then keeps running"
    kept, removed = dedup(
        [_ex("a", base, base + "!"), _ex("b", base + " x", base + "! x")], threshold=0.8
    )
    assert len(kept) == 1 and removed["near_duplicate"] == 1


def test_dedup_drops_examples_that_are_mostly_repeated_boilerplate() -> None:
    # threshold=0.99 keeps stage 2 out of the way so this isolates stage 3.
    header = "copyright 2024 the authors licensed under the apache license version two "
    examples = [_ex(f"h{i}", header + f"body number {i} " * 2, "after") for i in range(25)]
    kept, removed = dedup(
        examples, threshold=0.99, boilerplate_max_docs=20, boilerplate_fraction=0.5
    )
    assert removed["boilerplate"] == 25 and kept == []


def test_dedup_keeps_distinct_examples_untouched() -> None:
    kept, removed = dedup([_ex("a", "alpha beta gamma", "x"), _ex("b", "zeta eta theta", "y")])
    assert len(kept) == 2
    assert removed == {"exact": 0, "near_duplicate": 0, "boilerplate": 0}
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/corpus/test_dedup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sphragis.corpus.dedup'`

- [x] **Step 3: Write minimal implementation**

```python
"""Three-stage deduplication: exact, near-duplicate, repeated boilerplate."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Collapse whitespace; identifier style is left alone because it is the signal."""
    return _WHITESPACE.sub(" ", text).strip()


def shingles(text: str, k: int = 5) -> frozenset[str]:
    """Character k-grams of the normalized text."""
    normalized = normalize(text)
    if len(normalized) <= k:
        return frozenset({normalized}) if normalized else frozenset()
    return frozenset(normalized[i : i + k] for i in range(len(normalized) - k + 1))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity, defined as 0.0 when both sides are empty."""
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _pair_text(example: Mapping[str, Any]) -> str:
    return f"{normalize(str(example['before']))}\n{normalize(str(example['after']))}"


def dedup(
    examples: Sequence[Mapping[str, Any]],
    *,
    threshold: float = 0.8,
    k: int = 5,
    boilerplate_max_docs: int = 20,
    boilerplate_fraction: float = 0.5,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Remove duplicates, near-duplicates and boilerplate; keep the earliest occurrence."""
    removed: Counter[str] = Counter({"exact": 0, "near_duplicate": 0, "boilerplate": 0})
    ordered = sorted(examples, key=lambda e: (str(e.get("created") or ""), str(e["id"])))

    by_hash: dict[str, dict[str, Any]] = {}
    for example in ordered:
        key = hashlib.sha256(_pair_text(example).encode()).hexdigest()
        if key in by_hash:
            removed["exact"] += 1
            continue
        by_hash[key] = dict(example)
    stage1 = list(by_hash.values())

    kept: list[dict[str, Any]] = []
    signatures: list[frozenset[str]] = []
    for example in stage1:
        signature = shingles(_pair_text(example), k)
        if any(jaccard(signature, seen) >= threshold for seen in signatures):
            removed["near_duplicate"] += 1
            continue
        kept.append(example)
        signatures.append(signature)

    document_frequency: Counter[str] = Counter()
    for signature in signatures:
        document_frequency.update(signature)
    repeated = {s for s, n in document_frequency.items() if n > boilerplate_max_docs}

    survivors: list[dict[str, Any]] = []
    for example, signature in zip(kept, signatures, strict=True):
        share = len(signature & repeated) / len(signature) if signature else 0.0
        if share >= boilerplate_fraction:
            removed["boilerplate"] += 1
            continue
        survivors.append(example)
    return survivors, dict(removed)
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/corpus/test_dedup.py -v`
Expected: 6 passed

- [x] **Step 5: Record the spec deviation**

In the spec, replace the stage-3 bullet and the paragraph after it with:

```markdown
3. **Repeated substring.** Corpus-wide shingle frequency: a character k-gram appearing in
   more than `boilerplate_max_docs` distinct examples is repeated text. Catches boilerplate
   that recurs across otherwise distinct examples: license headers, generated bindings,
   translation catalogs, vendored code, requirements pins.

Stage 3 was specified as a suffix array and is implemented as shingle frequency. A
linear-time suffix array needs a C extension, and a pure-Python one is O(n^2 log n) over
the concatenated corpus, which will not hold at full scale. Shingle frequency finds the
same cross-document repetition at k-gram granularity with no new dependency. Revisit if
the boilerplate counts look wrong against a manual read of what it drops.

Stage 3 also differs from the text-corpus version in what it does with a hit. Text
pipelines excise the repeated substring from the document. Excising from a code hunk would
produce code that does not parse, so here a hit **drops the example** and records why,
above a boilerplate fraction threshold fixed in the Stage 1 report.
```

- [x] **Step 6: Lint and commit**

```bash
make lint
git add sphragis/corpus/dedup.py tests/unit/corpus/test_dedup.py \
        docs/superpowers/specs/2026-09-13-gerrit-review-corpus-harness-design.md
git commit -m "feat(corpus): three-stage dedup, with the stage-3 deviation recorded"
```

---

### Task 6: Time windows and the test-window seal

**Files:**
- Create: `sphragis/corpus/split.py`
- Test: `tests/unit/corpus/test_split.py`

**Interfaces:**
- Consumes: example dicts from Task 4 (`id`, `change_id`, `created`).
- Produces:
  - `assign_windows(examples: Sequence[Mapping[str, Any]], bounds: Mapping[str, tuple[str, str]]) -> dict[str, list[dict[str, Any]]]`
  - `straddling_changes(windows: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[str]`
  - `seal(definition: Mapping[str, Any]) -> dict[str, Any]`
  - `is_test_window_unlocked(seal_record: Mapping[str, Any]) -> bool`

A change is assigned whole, by its earliest example's `created`, so no change-id can straddle a boundary by construction. `straddling_changes` is the assertion that proves it.

- [x] **Step 1: Write the failing test**

```python
"""Time windows grouped by change, and the seal on the confirmatory window."""

from __future__ import annotations

from sphragis.corpus.split import assign_windows, seal, straddling_changes

BOUNDS = {
    "pilot": ("2024-10-01", "2024-11-01"),
    "train": ("2024-11-01", "2025-09-01"),
    "dev": ("2025-09-01", "2025-11-01"),
    "test": ("2025-11-01", "2026-09-01"),
}


def _ex(example_id: str, change_id: str, created: str) -> dict:
    return {"id": example_id, "change_id": change_id, "created": created}


def test_assign_windows_places_each_example_by_date() -> None:
    windows = assign_windows(
        [_ex("a", "I1", "2024-10-05"), _ex("b", "I2", "2025-01-05"), _ex("c", "I3", "2025-12-05")],
        BOUNDS,
    )
    assert [e["id"] for e in windows["pilot"]] == ["a"]
    assert [e["id"] for e in windows["train"]] == ["b"]
    assert [e["id"] for e in windows["test"]] == ["c"]
    assert windows["dev"] == []


def test_a_change_spanning_a_boundary_goes_whole_into_its_earliest_window() -> None:
    windows = assign_windows([_ex("a", "I1", "2024-10-28"), _ex("b", "I1", "2024-11-03")], BOUNDS)
    assert sorted(e["id"] for e in windows["pilot"]) == ["a", "b"]
    assert windows["train"] == []
    assert straddling_changes(windows) == []


def test_examples_outside_every_window_are_discarded() -> None:
    windows = assign_windows([_ex("old", "I0", "2024-01-01")], BOUNDS)
    assert all(w == [] for w in windows.values())


def test_seal_records_a_hash_and_a_timestamp_and_starts_locked() -> None:
    record = seal({"query": "status:merged", "after": "2025-11-01"})
    assert len(record["hash"]) == 64 and record["sealed_at"]
    assert record["accepted_at"] is None
    assert is_test_window_unlocked(record) is False


def test_seal_is_stable_for_the_same_definition_and_changes_with_it() -> None:
    a = seal({"query": "q", "after": "2025-11-01"})
    b = seal({"after": "2025-11-01", "query": "q"})
    c = seal({"query": "q", "after": "2025-12-01"})
    assert a["hash"] == b["hash"] != c["hash"]


def test_the_test_window_unlocks_only_once_an_acceptance_date_is_recorded() -> None:
    record = {**seal({"query": "q"}), "accepted_at": "2027-02-04"}
    assert is_test_window_unlocked(record) is True
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/corpus/test_split.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sphragis.corpus.split'`

- [x] **Step 3: Write minimal implementation**

```python
"""Time-ordered windows, grouped by change, plus the seal on the confirmatory window."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any


def assign_windows(
    examples: Sequence[Mapping[str, Any]], bounds: Mapping[str, tuple[str, str]]
) -> dict[str, list[dict[str, Any]]]:
    """Assign each change whole to the window holding its earliest example."""
    by_change: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for example in examples:
        by_change[str(example["change_id"])].append(example)

    windows: dict[str, list[dict[str, Any]]] = {name: [] for name in bounds}
    for change_examples in by_change.values():
        earliest = min(str(e["created"]) for e in change_examples)
        for name, (start, end) in bounds.items():
            if start <= earliest < end:
                windows[name].extend(dict(e) for e in change_examples)
                break
    return windows


def straddling_changes(windows: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[str]:
    """Every change id appearing in more than one window; must always be empty."""
    seen: dict[str, set[str]] = defaultdict(set)
    for name, examples in windows.items():
        for example in examples:
            seen[str(example["change_id"])].add(name)
    return sorted(change_id for change_id, names in seen.items() if len(names) > 1)


def seal(definition: Mapping[str, Any]) -> dict[str, Any]:
    """Hash and timestamp the test-window definition without collecting it."""
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"))
    return {
        "definition": dict(definition),
        "hash": hashlib.sha256(canonical.encode()).hexdigest(),
        "sealed_at": datetime.now(UTC).isoformat(),
        "accepted_at": None,
    }


def is_test_window_unlocked(seal_record: Mapping[str, Any]) -> bool:
    """True only once an in-principle acceptance date has been written into the seal."""
    return bool(seal_record.get("accepted_at"))
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/corpus/test_split.py -v`
Expected: 6 passed

- [x] **Step 5: Lint and commit**

```bash
make lint
git add sphragis/corpus/split.py tests/unit/corpus/test_split.py
git commit -m "feat(corpus): time windows grouped by change, with the test-window seal"
```

---

### Task 7: CLI and the verify target

**Files:**
- Create: `sphragis/corpus/cli.py`, `sphragis/corpus/__main__.py`
- Modify: `Makefile` — add `corpus-verify`
- Test: `tests/unit/corpus/test_cli.py`

**Interfaces:**
- Consumes: every module above.
- Produces:
  - `hsro_determination(path: Path) -> str | None` — the recorded date, or `None`
  - `require_hsro(path: Path) -> str` — raises `SystemExit` when absent
  - `main(argv: Sequence[str] | None = None) -> int`

- [x] **Step 1: Write the failing test**

```python
"""The CLI's gates: HSRO before fetch, seal before the test window."""

from __future__ import annotations

from pathlib import Path

import pytest

from sphragis.corpus.cli import hsro_determination, main, require_hsro


def test_hsro_determination_reads_the_recorded_date(tmp_path: Path) -> None:
    record = tmp_path / "HSRO.md"
    record.write_text("# HSRO\n\nDetermination: 2026-10-01\nNot human subjects research.\n")
    assert hsro_determination(record) == "2026-10-01"


def test_hsro_determination_is_none_when_the_file_is_missing(tmp_path: Path) -> None:
    assert hsro_determination(tmp_path / "HSRO.md") is None


def test_hsro_determination_is_none_when_the_file_has_no_date(tmp_path: Path) -> None:
    record = tmp_path / "HSRO.md"
    record.write_text("# HSRO\n\nStill waiting on the office.\n")
    assert hsro_determination(record) is None


def test_require_hsro_exits_when_there_is_no_determination(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        require_hsro(tmp_path / "HSRO.md")
    assert "HSRO" in str(excinfo.value)


def test_fetch_refuses_to_run_without_a_determination(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["fetch", "--org", "openstack", "--hsro", str(tmp_path / "HSRO.md")])


def test_main_exits_for_an_unknown_stage() -> None:
    with pytest.raises(SystemExit):
        main(["nonsense"])


def test_main_reports_a_stage_with_no_body_yet(tmp_path: Path) -> None:
    record = tmp_path / "HSRO.md"
    record.write_text("Determination: 2026-10-01\n")
    assert main(["fetch", "--hsro", str(record)]) == 1
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/corpus/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sphragis.corpus.cli'`

- [x] **Step 3: Write minimal implementation**

`sphragis/corpus/cli.py`:

```python
"""Corpus pipeline entry point: the one place that touches disk or the network."""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from pathlib import Path

_DETERMINATION = re.compile(r"Determination:\s*(\d{4}-\d{2}-\d{2})")

STAGES = ("fetch", "build", "dedup", "split", "freeze", "verify")


def hsro_determination(path: Path) -> str | None:
    """The determination date recorded in the HSRO file, if there is one."""
    if not path.is_file():
        return None
    match = _DETERMINATION.search(path.read_text())
    return match.group(1) if match else None


def require_hsro(path: Path) -> str:
    """Return the determination date, or exit; collection may not start without it."""
    date = hsro_determination(path)
    if date is None:
        raise SystemExit(
            f"no HSRO determination in {path}. Repository mining is human-subjects "
            "research; record the determination before collecting."
        )
    return date


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sphragis.corpus")
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--org", default="openstack")
    parser.add_argument("--window", default="pilot")
    parser.add_argument("--hsro", type=Path, default=Path("corpus/HSRO.md"))
    parser.add_argument("--root", type=Path, default=Path("datasets/gerrit"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.stage == "fetch":
        require_hsro(args.hsro)
    print(f"stage {args.stage!r} has no body yet; see the A2 plan")
    return 1
```

`sphragis/corpus/__main__.py`:

```python
from sphragis.corpus.cli import main

raise SystemExit(main())
```

In the `Makefile`, after the `test-cov` target:

```make
corpus-verify:             ## Re-derive the corpus manifest and fail on any mismatch
	uv run python -m sphragis.corpus verify
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/corpus/test_cli.py -v`
Expected: 6 passed

- [x] **Step 5: Run the whole suite and lint**

Run: `make lint && make test`
Expected: lint clean, all tests pass including the pre-existing suite.

- [x] **Step 6: Commit**

```bash
git add sphragis/corpus/cli.py sphragis/corpus/__main__.py Makefile tests/unit/corpus/test_cli.py
git commit -m "feat(corpus): CLI skeleton with the HSRO gate on fetch"
```

---

## After this plan

The stage bodies behind the CLI (real transport, artifact writing, wiring the stages together) land in Plan A2 once the HSRO determination is on file, because that is the first point at which `fetch` can legally run. Everything in this plan is buildable and testable before then, which is why it is sequenced first.

Plan B (measurement) and Plan C (experiment) follow. Plan C's pilot run is the one item on the Stage 1 checklist that needs all three.
