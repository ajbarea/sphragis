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
by_org_tool = _load("label_audit_by_org")
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


def test_the_second_rater_builder_refuses_a_rater_file(tmp_path: Path, monkeypatch) -> None:
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


def _org_reading(report: dict) -> dict:
    """label_audit_by_org.py's reading of the fixture report on openstack."""
    mine = {eid for eid in report["labels"] if eid.startswith("openstack:")}
    return {"org": "openstack", "pairs": by_org_tool.read(report, mine, against="A")}


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
        (_answer("valid", "t", slips=[["label"]]), "item-002", "unknown slip"),
        ({**_answer("valid", "t"), "slips": None}, "item-002", "unknown slip"),
        ({**_answer("valid", "t"), "outside_names": "no"}, "item-002", "not a locked answer"),
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
    reading = agreement_tool.second_rater_report(report, _org_reading(report), export)
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
    # Model pairs are read once, on the checked items, under that name only.
    assert not [n for n in pairs if n.startswith("A~B") and "on checked items" not in n]
    assert "u_a" not in json.dumps(reading) and "u_b" not in json.dumps(reading)


def test_the_committed_report_reproduces_its_openstack_reading() -> None:
    """On the committed report every human pair, and the model raters' label pair on the
    checked items, reproduce label-audit-v2-openstack.json (the function refuses otherwise)."""
    results = _SCRIPTS.parent / "datasets" / "results"
    report = json.loads((results / "label-audit-v2.json").read_text())
    org_reading = json.loads((results / "label-audit-v2-openstack.json").read_text())
    pairs = agreement_tool.report_readers(report, org_reading)[2]
    recorded = org_reading["pairs"]
    humans = [n for n in pairs if n.startswith("human~")]
    assert len(humans) == 12 and all(n in recorded for n in humans)
    assert "A~B on checked items" in pairs and "A~B" not in pairs
    names = {n: n for n in [*humans, "A~B on checked items"]}
    assert agreement_tool.differences(pairs, recorded, names) == []


def test_a_reading_that_does_not_reproduce_the_organizations_is_refused(tmp_path: Path) -> None:
    report = _report()
    tampered = _org_reading(report)
    tampered["pairs"]["human~A"] = {**tampered["pairs"]["human~A"], "kappa": 0.123}
    with pytest.raises(SystemExit, match=r"does not reproduce: human~A\.kappa"):
        agreement_tool.second_rater_report(report, tampered, tmp_path)
    renamed = _org_reading(report)
    del renamed["pairs"]["A~B on checked items"]
    with pytest.raises(SystemExit, match="A~B on checked items: not recorded"):
        agreement_tool.second_rater_report(report, renamed, tmp_path)
    # A reading that lacks pairs the rebuild reads cannot vouch for them.
    thinned = _org_reading(report)
    thinned["pairs"] = {n: s for n, s in thinned["pairs"].items() if "without slips" not in n}
    with pytest.raises(SystemExit, match=r"human~A without slips: not recorded"):
        agreement_tool.second_rater_report(report, thinned, tmp_path)
    no_human = _org_reading(report)
    no_human["pairs"] = {n: s for n, s in no_human["pairs"].items() if "human" not in n}
    with pytest.raises(SystemExit, match="human~A: not recorded"):
        agreement_tool.second_rater_report(report, no_human, tmp_path)
    # A report whose human slips were lost cannot rebuild the readings without them.
    slipless = {**report, "human_slips": {}}
    with pytest.raises(SystemExit, match=r"human~A without slips: not rebuilt"):
        agreement_tool.second_rater_report(slipless, _org_reading(report), tmp_path)


def test_a_second_rater_with_one_item_is_read_with_undefined_intervals(tmp_path: Path) -> None:
    """One item gives kappa no resample it is defined on: NaN, not a crash."""
    report = _report()
    first = agreement_tool.checked_items(report, "openstack")[0]
    export = _export(tmp_path, {"u_1": {first: _answer("valid", "2026-11-11T09:00:00Z")}})
    pair = agreement_tool.second_rater_report(report, _org_reading(report), export)["pairs"][
        "human~L1"
    ]
    assert pair["items"] == 1
    assert all(math.isnan(x) for x in pair["kappa_95"])


def test_the_second_rater_builder_writes_the_checked_items_and_no_answer(
    tmp_path: Path, monkeypatch
) -> None:
    """Through main(): the report's checked items of one organization, shown fields only."""
    report = _report()
    rows = [
        {
            "item": f"item-{n:03d}",
            "path": "a.py",
            "comments": [f"c{n}"],
            "context_before": "",
            "before": "x",
            "after": "y",
            "context_after": "",
        }
        for n in range(1, 14)
    ]
    batches = tmp_path / "batches"
    batches.mkdir()
    (batches / "batch-1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp_path / "report.json").write_text(json.dumps(report))
    out = tmp_path / "page.html"
    argv = ["x", "--second-rater", "--batches", str(batches), "--report"]
    argv += [str(tmp_path / "report.json"), "--org", "openstack", "--out", str(out)]
    monkeypatch.setattr("sys.argv", argv)
    page_tool.main()
    data = _page_data(out.read_text())
    assert [d["item"] for d in data] == agreement_tool.checked_items(report, "openstack")
    assert all(set(d) == _SHOWN_KEYS for d in data)
    for answer in ("partial", "non_actionable"):  # labels in the report, never in the data
        assert answer not in json.dumps(data)


def test_the_store_rules_keep_each_raters_answers_to_them_and_the_owner() -> None:
    """The committed rules: only the owner reads answers; each viewer writes only their own."""
    rules = json.loads((page_tool.PAGES / "rater2-capabilities.json").read_text())["db"]["rules"]
    by_path = {r["path"]: r for r in rules}
    assert by_path["answers"] == {"path": "answers", "read": "owner", "write": "self"}
    assert by_path["answers/{self}"]["write"] == "interact"
    assert by_path[""]["write"] == "owner"


def test_an_empty_export_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="no answers/<viewer id>/items"):
        agreement_tool.read_second_raters(tmp_path, ["item-002"])


def test_pseudonyms_survive_a_later_export(tmp_path: Path) -> None:
    """A rater who starts later, with an earlier clock, renames nobody already named."""
    items = ["item-002", "item-004"]
    _export(tmp_path, {"u_first": {"item-002": _answer("valid", "2026-11-11T09:00:00Z")}})
    assert list(agreement_tool.read_second_raters(tmp_path, items)) == ["L1"]
    _export(tmp_path, {"u_second": {"item-002": _answer("partial", "2026-11-01T09:00:00Z")}})
    got = agreement_tool.read_second_raters(tmp_path, items)
    assert {k: v["item-002"]["label"] for k, v in got.items()} == {"L1": "valid", "L2": "partial"}


def test_an_undefined_interval_is_written_as_null() -> None:
    body = agreement_tool._nan_as_null({"kappa_95": [math.nan, math.nan], "ac1": 0.5})
    assert json.dumps(body, allow_nan=False) == '{"kappa_95": [null, null], "ac1": 0.5}'


def test_differences_read_two_undefined_values_as_equal() -> None:
    nan = math.nan
    ours = {
        "p": {
            "items": 3,
            "raw_agreement": 1.0,
            "kappa": nan,
            "kappa_95": [nan, nan],
            "ac1": 1.0,
            "ac1_95": [1.0, 1.0],
        }
    }
    theirs = {"p": {**ours["p"], "kappa": None, "kappa_95": [None, None]}}
    assert agreement_tool.differences(ours, theirs, {"p": "p"}) == []
    assert agreement_tool.differences(ours, {"p": {**theirs["p"], "kappa": 0.1}}, {"p": "p"})


def test_a_pseudonym_file_with_a_gap_names_nobody_twice(tmp_path: Path) -> None:
    _export(tmp_path, {"u_b": {"item-002": _answer("valid", "2026-11-11T09:00:00Z")}})
    (tmp_path / "pseudonyms.json").write_text('{"u_a": "L1", "u_c": "L3"}')
    agreement_tool.read_second_raters(tmp_path, ["item-002"])
    names = json.loads((tmp_path / "pseudonyms.json").read_text())
    assert names["u_b"] == "L4" and len(set(names.values())) == 3
    (tmp_path / "pseudonyms.json").write_text('{"u_a": "L1", "u_b": "L1"}')
    with pytest.raises(SystemExit, match="share a pseudonym"):
        agreement_tool.read_second_raters(tmp_path, ["item-002"])


def test_a_pair_raises_on_an_undefined_interval_unless_asked_not_to() -> None:
    """The pooled and per-organization readings keep main's refusal; the read-back tolerates it."""
    same = {"i1": "valid", "i2": "valid"}
    with pytest.raises(ValueError, match="undefined on every resample"):
        agreement_tool._pair(same, same, ["i1", "i2"], list(sample_tool.LABELS))
    pair = agreement_tool._pair(
        same, same, ["i1", "i2"], list(sample_tool.LABELS), undefined_ok=True
    )
    assert all(math.isnan(x) for x in pair["kappa_95"])


def test_differences_refuse_a_missing_interval_by_name() -> None:
    ours = {
        "p": {
            "items": 3,
            "raw_agreement": 1.0,
            "kappa": 0.2,
            "kappa_95": [0.1, 0.3],
            "ac1": 1.0,
            "ac1_95": [1.0, 1.0],
        }
    }
    theirs = {"p": {**ours["p"], "kappa_95": None}}
    assert agreement_tool.differences(ours, theirs, {"p": "p"}) == [
        "p.kappa_95: [0.1, 0.3] against None"
    ]
    short = {"p": {**ours["p"], "kappa_95": [0.1]}}
    assert agreement_tool.differences(ours, short, {"p": "p"}) == [
        "p.kappa_95: [0.1, 0.3] against [0.1]"
    ]


def _readback_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    report = _report()
    (tmp_path / "report.json").write_text(json.dumps(report))
    (tmp_path / "org.json").write_text(json.dumps(_org_reading(report)))
    first = agreement_tool.checked_items(report, "openstack")[0]
    export = _export(tmp_path / "export", {"u_1": {first: _answer("valid", "2026-11-11T09:00Z")}})
    return tmp_path / "report.json", tmp_path / "org.json", export


def test_the_read_back_cli_writes_strict_json_and_refuses_bad_arguments(
    tmp_path: Path, monkeypatch
) -> None:
    """Through main(): null for an undefined value, sources recorded, inputs never overwritten."""
    report, org, export = _readback_inputs(tmp_path)
    out = tmp_path / "out.json"
    base = ["x", "--from-report", str(report), "--second-raters", str(export)]

    def run(*extra: str) -> None:
        monkeypatch.setattr("sys.argv", [*base, *extra])
        agreement_tool.main()

    run("--org-reading", str(org), "--out", str(out))

    def no_nan(token: str) -> float:
        raise AssertionError(f"bare {token} in the output")

    written = json.loads(out.read_text(), parse_constant=no_nan)
    assert written["pairs"]["human~L1"]["kappa_95"] == [None, None]
    assert written["from"] == str(report) and written["org_reading"] == str(org)
    for extra in (["--out", str(out)], ["--org-reading", str(org), "--out", str(org)]):
        before = org.read_text()
        with pytest.raises(SystemExit):
            run(*extra)
        assert org.read_text() == before


def test_a_committed_path_is_recorded_from_the_repository_root() -> None:
    inside = agreement_tool.ROOT / "datasets" / "results" / "label-audit-v2.json"
    assert agreement_tool.relative(inside) == "datasets/results/label-audit-v2.json"


def test_the_read_back_output_passes_the_published_text_guard(tmp_path: Path) -> None:
    """Its pair names (second raters, questions on checked items) are keys the guard knows."""
    from sphragis.measure import withhold

    report, org, export = _readback_inputs(tmp_path)
    _export(export, {"u_2": {"item-002": _answer("partial", "2026-11-12T09:00Z", slips=["label"])}})
    reading = agreement_tool.second_rater_report(
        json.loads(report.read_text()), json.loads(org.read_text()), export
    )
    body = json.dumps(agreement_tool._nan_as_null(reading), allow_nan=False).encode()
    rel = "datasets/results/label-audit-v2-rater2.json"
    assert withhold.refusals(rel, body, [json.loads(body)]) == []
    assert any(" on checked items" in n and "valid_vs_rest" in n for n in reading["pairs"])
    assert any(n.startswith("L1~L2") for n in reading["pairs"])


def test_a_report_with_nothing_to_compare_is_refused(tmp_path: Path) -> None:
    report = _report()
    bare = {
        **report,
        "labels": {
            e: {"human": by["human"]} for e, by in report["labels"].items() if "human" in by
        },
    }
    with pytest.raises(SystemExit, match="nothing to compare"):
        agreement_tool.report_readers(bare, {"org": "openstack", "pairs": {}})
