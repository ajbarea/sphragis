"""The label audit's blinding and check page: what a rater and a checker are shown."""

from __future__ import annotations

import importlib.util
import json
import math
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
        page_tool.page_items(items, _answers(items)),
        "<style>/*__STYLE__*/</style><script>__DATA__</script>",
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


# The second-rater page: same items and order, nothing revealed, each viewer's answers apart.

agreement_tool = _load("label_audit_agreement")
_SHOWN_KEYS = {"item", "path", "comments", "cb", "b", "a", "ca"}


def _page_data(page: str) -> list[dict]:
    found = re.search(r"const ITEMS = (\[.*?\]);\nconst LABELS", page, re.S)
    assert found, "the page embeds no ITEMS"
    return json.loads(found.group(1).replace("<\\/", "</"))


def _second_rater_page(items: list[dict], chosen: list[str]) -> str:
    return page_tool.render(
        page_tool.second_rater_items(items, chosen),
        page_tool.SECOND_RATER_TEMPLATE.read_text(),
        page_tool.STYLE.read_text(),
    )


def test_the_second_rater_page_carries_the_chosen_items_in_batch_order_and_nothing_else() -> None:
    items, _ = blind_tool.blind(_sheet(6), seed=1)
    chosen = [items[4]["item"], items[1]["item"], items[2]["item"]]
    data = _page_data(_second_rater_page(items, chosen))
    assert [d["item"] for d in data] == [i["item"] for i in items if i["item"] in chosen]
    assert all(set(d) == _SHOWN_KEYS for d in data)


def test_an_item_missing_from_the_batches_is_refused() -> None:
    items, _ = blind_tool.blind(_sheet(2), seed=1)
    with pytest.raises(SystemExit, match="the batches hold none"):
        page_tool.second_rater_items(items, ["item-099"])


def test_the_second_rater_page_reveals_no_rater_answer() -> None:
    """The page's data has no answer field, and its template reads none and shows no reveal."""
    template = page_tool.SECOND_RATER_TEMPLATE.read_text()
    assert not re.search(r"\bex\.(rater|outside|reason)\b", template)
    for word in ("rater A", "Reveal", "rater_a", "agrees with"):
        assert word not in template
    # The only answer the page shows is the invented practice item's.
    assert template.count("PRACTICE.label") == 1


def test_the_second_rater_builder_never_reads_a_rater(tmp_path: Path, monkeypatch) -> None:
    labels = tmp_path / "labels.json"
    labels.write_text("{}")
    argv = ["x", "--second-rater", "--batches", str(tmp_path), "--rater", str(labels)]
    argv += ["--report", str(labels), "--org", "openstack", "--out", str(tmp_path / "p.html")]
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit):
        page_tool.main()
    assert not (tmp_path / "p.html").exists()


def test_the_second_rater_page_reads_and_writes_only_the_viewers_own_answers() -> None:
    """Every store path the page builds sits under answers/<the viewer's id>."""
    template = page_tool.SECOND_RATER_TEMPLATE.read_text()
    calls = re.findall(r"db\.(?:doc|collection)\(([^)]*)\)", template)
    assert calls, "the page touches no store path"
    assert all(c.startswith('"answers/"+uid') for c in calls), calls


def test_both_templates_take_the_shared_style() -> None:
    for template in (page_tool.TEMPLATE, page_tool.SECOND_RATER_TEMPLATE):
        text = template.read_text()
        assert text.count("/*__STYLE__*/") == 1 and text.count("__DATA__") == 1
        found = re.search(r"const LABELS = (\[.*?\]);", text)
        assert found and set(json.loads(found.group(1))) == set(sample_tool.LABELS)
        assert 'const RUBRIC = "v2";' in text
    page = page_tool.render([], page_tool.TEMPLATE.read_text(), "body{color:red}")
    assert "body{color:red}" in page and "__STYLE__" not in page


# Reading the second raters back.

_ORDER = ("valid", "partial", "non_actionable")


def _report() -> dict:
    """A report in label_audit_agreement's shape: 12 items past item-001, the human on 8 of them."""
    labels = {}
    for n in range(2, 14):
        org = "openstack" if n % 3 else "qt"
        by = {
            "A": {"label": _ORDER[n % 3], "outside_names": n % 4 == 0},
            "B": {"label": _ORDER[(n // 2) % 3], "outside_names": n % 5 == 0},
        }
        if n <= 11:
            by["human"] = {"label": _ORDER[(n + 1) % 3], "outside_names": n % 2 == 0}
        labels[f"{org}:I{n}:a.py:1:{n}"] = by
    return {"excluded": ["item-001"], "labels": labels, "human_slips": {"label": ["item-004"]}}


def _answer(label: str, at: str, *, outside: bool = False, slips=(), rubric: str = "v2") -> dict:
    return {
        "rubric": rubric,
        "label": label,
        "outside_names": outside,
        "note": "",
        "slips": list(slips),
        "locked_at": at,
    }


def _export(root: Path, viewers: dict[str, dict[str, dict]]) -> Path:
    for viewer, answers in viewers.items():
        (root / "answers" / f"{viewer}.json").parent.mkdir(parents=True, exist_ok=True)
        (root / "answers" / f"{viewer}.json").write_text('{"rubric": "v2"}')
        for item, body in answers.items():
            path = root / "answers" / viewer / "items" / f"{item}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(body))
    return root


def test_checked_items_are_the_humans_items_of_one_organization() -> None:
    chosen = agreement_tool.checked_items(_report(), "openstack")
    # openstack is every n not divisible by 3; the human checked n <= 11.
    assert chosen == [f"item-{n:03d}" for n in range(2, 12) if n % 3]
    with pytest.raises(SystemExit, match="checked none"):
        agreement_tool.checked_items(_report(), "wikimedia")


def test_each_second_rater_is_read_apart_under_a_pseudonym(tmp_path: Path) -> None:
    export = _export(
        tmp_path,
        {
            "u_late": {"item-002": _answer("valid", "2026-11-12T10:00:00Z")},
            "u_early": {
                "item-002": _answer("partial", "2026-11-11T09:00:00Z"),
                "item-004": _answer("valid", "2026-11-11T09:05:00Z", slips=["label"]),
            },
        },
    )
    got = agreement_tool.read_second_raters(export, ["item-002", "item-004", "item-005"])
    assert list(got) == ["L1", "L2"]  # by first lock, not by id
    assert {i: a["label"] for i, a in got["L1"].items()} == {
        "item-002": "partial",
        "item-004": "valid",
    }
    assert {i: a["label"] for i, a in got["L2"].items()} == {"item-002": "valid"}


@pytest.mark.parametrize(
    ("body", "item", "message"),
    [
        (_answer("valid", "t", rubric="v1"), "item-002", "rubric"),
        (_answer("looks fine", "t"), "item-002", "not a locked answer"),
        ({**_answer("valid", "t"), "locked_at": None}, "item-002", "not a locked answer"),
        (_answer("valid", "t", slips=["reason"]), "item-002", "unknown slip"),
        (_answer("valid", "t"), "item-003", "not one of the page's items"),
    ],
)
def test_an_answer_outside_the_audit_is_refused(
    tmp_path: Path, body: dict, item: str, message: str
) -> None:
    export = _export(tmp_path, {"u_1": {item: body}})
    with pytest.raises(SystemExit, match=message):
        agreement_tool.read_second_raters(export, ["item-002"])


def test_the_second_rater_reading_pairs_every_reader_on_shared_items(tmp_path: Path) -> None:
    report = _report()
    items = agreement_tool.checked_items(report, "openstack")
    by_neutral = dict(
        zip(agreement_tool.neutral_ids(report), report["labels"].values(), strict=True)
    )
    # L1 copies the human on every item; L2 answers two items and slips on one.
    export = _export(
        tmp_path,
        {
            "u_a": {
                i: _answer(
                    by_neutral[i]["human"]["label"],
                    "2026-11-11T09:00:00Z",
                    outside=by_neutral[i]["human"]["outside_names"],
                )
                for i in items
            },
            "u_b": {
                items[0]: _answer("valid", "2026-11-12T09:00:00Z", slips=["outside_names"]),
                items[1]: _answer("partial", "2026-11-12T09:01:00Z"),
            },
        },
    )
    reading = agreement_tool.second_rater_report(report, "openstack", export)
    pairs = reading["pairs"]
    assert reading["items"] == len(items)
    assert pairs["human~L1"]["raw_agreement"] == 1.0
    assert pairs["human~L1"]["items"] == len(items)
    assert pairs["human~L1"]["raw_agreement_95"][1] == 1.0
    assert pairs["L1~L2"]["items"] == 2
    assert pairs["L1~L2 outside_names without slips"]["items"] == 1
    assert "L1~L2 without slips" not in pairs  # no label slip touches their shared items
    # The human's own slip is read without, as in the report.
    assert pairs["human~A without slips"]["items"] == len(items) - 1
    assert reading["second_raters"]["L2"] == {
        "answered": 2,
        "slips": {"label": [], "outside_names": [items[0]]},
    }
    assert set(reading["labels"]["L2"]) <= set(report["labels"])  # keyed by example id
    assert "u_a" not in json.dumps(reading) and "u_b" not in json.dumps(reading)


def test_with_no_second_rater_the_reading_reproduces_the_committed_openstack_pairs(
    tmp_path: Path,
) -> None:
    """On the committed report the human and model pairs reproduce label-audit-v2-openstack.json."""
    results = _SCRIPTS.parent / "datasets" / "results"
    report = json.loads((results / "label-audit-v2.json").read_text())
    recorded = json.loads((results / "label-audit-v2-openstack.json").read_text())["pairs"]
    pairs = agreement_tool.second_rater_report(report, "openstack", tmp_path)["pairs"]
    same = {"A~B": "A~B on checked items"}
    for name in ("human~A", "human~B", "human~A without slips", "human~B outside_names", "A~B"):
        theirs = recorded[same.get(name, name)]
        for field in ("items", "raw_agreement", "kappa", "ac1", "kappa_95", "ac1_95"):
            assert pairs[name][field] == pytest.approx(theirs[field], abs=1e-12), (name, field)


def test_a_second_rater_with_one_item_is_read_with_undefined_intervals(tmp_path: Path) -> None:
    """One item gives kappa no resample it is defined on: NaN, not a crash."""
    report = _report()
    first = agreement_tool.checked_items(report, "openstack")[0]
    export = _export(tmp_path, {"u_1": {first: _answer("valid", "2026-11-11T09:00:00Z")}})
    pair = agreement_tool.second_rater_report(report, "openstack", export)["pairs"]["human~L1"]
    assert pair["items"] == 1
    assert all(math.isnan(x) for x in pair["kappa_95"])
