"""scripts/fetch_parents.py: the recorded queries and the parents kept from a listing."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "fetch_parents", ROOT / "scripts" / "fetch_parents.py"
)
assert _spec is not None and _spec.loader is not None
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


def test_the_recorded_queries_are_replayed_in_order() -> None:
    assert script.recorded_queries({"query": "q1"}) == ["q1"]
    assert script.recorded_queries({"listings": [{"query": "a"}, {"query": "b"}]}) == ["a", "b"]
    with pytest.raises(SystemExit, match="names no query"):
        script.recorded_queries({"listings": [{"query": "a"}, {"pages": 1}]})


def test_only_shas_and_parent_shas_are_kept() -> None:
    changes = [
        {
            "subject": "kept nowhere",
            "revisions": {
                "s1": {
                    "_number": 1,
                    "commit": {
                        "parents": [{"commit": "p1", "subject": "x"}],
                        "message": "Signed-off-by: someone",
                        "author": {"name": "A", "email": "a@x.org"},
                    },
                },
                "s2": {"_number": 2, "commit": {"parents": [{"commit": "p2"}, {"commit": "p3"}]}},
                "s3": {"_number": 3},
            },
        }
    ]
    assert script.patch_set_parents(changes) == {"s1": ["p1"], "s2": ["p2", "p3"], "s3": []}
