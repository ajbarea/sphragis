"""The label audit's blinding and check page: what a rater and a checker are shown."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


blind_tool = _load("label_audit_blind")
page_tool = _load("label_audit_page")
sample_tool = _load("label_audit_sample")


def _sheet(n: int = 6) -> list[dict]:
    return [
        {
            "id": f"qt:I{i}:src/a.cpp:1:{i}",
            "org": "qt",
            "window": "train",
            "half": "qt-a",
            "project": "qt/qtbase",
            "path": "src/a.cpp",
            "comments": [f"rename {i}"],
            "context_before": "",
            "before": "x",
            "after": "y",
            "context_after": "",
            "label": None,
        }
        for i in range(n)
    ]


def test_a_rater_sees_neither_the_organization_nor_the_example_id() -> None:
    items, key = blind_tool.blind(_sheet(), seed=1)
    for item in items:
        assert set(item) == {"item", *blind_tool.SHOWN}
        assert "qt" not in json.dumps({k: v for k, v in item.items() if k != "path"})
    assert sorted(key.values()) == sorted(r["id"] for r in _sheet())


def test_blinding_is_fixed_by_its_seed() -> None:
    assert blind_tool.blind(_sheet(), seed=1) == blind_tool.blind(_sheet(), seed=1)
    assert blind_tool.blind(_sheet(), seed=1)[1] != blind_tool.blind(_sheet(), seed=2)[1]


def _answers(items: list[dict], label: str = "valid") -> dict[str, dict]:
    return {i["item"]: {"label": label, "outside_names": False, "reason": ""} for i in items}


def test_no_item_text_can_close_the_page_script() -> None:
    items, _ = blind_tool.blind(_sheet(1), seed=1)
    items[0]["comments"] = ["see </script><script>alert(1)</script>"]
    page = page_tool.render(
        page_tool.page_items(items, _answers(items)), "<script>__DATA__</script>"
    )
    assert page.count("</script>") == 1


def test_a_label_outside_the_rubric_is_refused() -> None:
    items, _ = blind_tool.blind(_sheet(1), seed=1)
    with pytest.raises(SystemExit, match="not in the rubric"):
        page_tool.page_items(items, _answers(items, "looks fine"))


def test_the_template_holds_one_placeholder_and_the_rubric_labels() -> None:
    template = page_tool.TEMPLATE.read_text()
    assert template.count("__DATA__") == 1
    found = re.search(r"const LABELS = (\[.*?\]);", template)
    assert found, "the template declares no LABELS"
    labels = json.loads(found.group(1))
    assert tuple(labels) == tuple(sample_tool.LABELS)
    assert 'const RUBRIC = "v2";' in template
