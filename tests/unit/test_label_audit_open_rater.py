"""The open-weight rater's prompt and answer parsing."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "open_rater", ROOT / "scripts" / "label_audit_open_rater.py"
)
assert _spec is not None and _spec.loader is not None
rater = importlib.util.module_from_spec(_spec)
sys.argv = ["label_audit_open_rater"]
_spec.loader.exec_module(rater)


def test_the_prompt_shows_only_the_fields_the_other_raters_saw() -> None:
    item = {
        "id": "qt:I1:a.cpp:1:2",
        "path": "a.cpp",
        "comments": ["fix"],
        "before": "x",
        "after": "y",
        "project": "qt/qtbase",
        "org": "qt",
    }
    text = rater.prompt("RUBRIC", item)
    assert text.startswith("RUBRIC") and '"path": "a.cpp"' in text
    assert "qtbase" not in text and "qt:I1" not in text


def test_an_answer_is_read_from_the_first_valid_json_object() -> None:
    assert rater.parse('Sure: {"label": "partial", "outside_names": true}') == {
        "label": "partial",
        "outside_names": True,
    }
    assert rater.parse('{"label": "great"} then {"label": "valid", "outside_names": false}') == {
        "label": "valid",
        "outside_names": False,
    }
    assert rater.parse("no json here") is None
    assert rater.parse('{"label": "valid", "outside_names": "no"}') is None
