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
