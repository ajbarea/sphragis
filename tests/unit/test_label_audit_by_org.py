"""The label audit read on one organization's items reproduces the whole pooled reading first."""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("by_org", ROOT / "scripts" / "label_audit_by_org.py")
assert _spec is not None and _spec.loader is not None
by_org = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(by_org)

RUBRIC = ("valid", "partial", "non_actionable", "unrelated_rewrite", "context_dependent")


def _report(tmp_path: Path, *, human_on=("openstack", "qt")) -> Path:
    """A report in the shape `label_audit_agreement.py` writes: item-001 excluded, labels by
    example id in sorted neutral-id order, slips by neutral id."""
    rng = random.Random(3)
    neutral = [f"item-{n:03d}" for n in range(2, 82)]
    raters, flags, human, human_flags, labels = {"A": {}, "B": {}}, {"A": {}, "B": {}}, {}, {}, {}
    pick = lambda: rng.choices(RUBRIC, weights=(6, 2, 1, 1, 1))[0]  # noqa: E731
    for k, n in enumerate(neutral):
        org = "openstack" if k % 2 else "qt"
        raters["A"][n] = pick()
        raters["B"][n] = raters["A"][n] if rng.random() < 0.7 else pick()
        flags["A"][n], flags["B"][n] = rng.random() < 0.3, rng.random() < 0.3
        labels[n] = (f"{org}:I{k}:a.py:1:{k}", org)
        if org in human_on:
            human[n] = raters["A"][n] if rng.random() < 0.7 else pick()
            human_flags[n] = rng.random() < 0.3
    slips = {"label": {neutral[3], neutral[8]}, "outside_names": {neutral[5]}}
    pairs = by_org.readings(
        neutral, raters, flags, human=(human, human_flags) if human else None, slips=slips
    )
    report = {
        "excluded": ["item-001"],
        "labels": {
            eid: {
                **{r: {"label": raters[r][n], "outside_names": flags[r][n]} for r in raters},
                **(
                    {"human": {"label": human[n], "outside_names": human_flags[n]}}
                    if n in human
                    else {}
                ),
            }
            for n, (eid, _) in labels.items()
        },
        "human_slips": {f: sorted(v) for f, v in slips.items()},
        "pairs": pairs,
        "valid_rates": {"A": {"openstack": {"share": 0.5}, "qt": {"share": 0.4}}},
    }
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(report))
    return path


def _main(monkeypatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["label_audit_by_org", *argv])
    by_org.main()


def test_only_the_organizations_items_are_read(tmp_path, monkeypatch) -> None:
    out = tmp_path / "out.json"
    _main(monkeypatch, "--report", str(_report(tmp_path)), "--org", "openstack", "--out", str(out))
    read = json.loads(out.read_text())
    assert read["items"] == 40
    assert read["pairs"]["A~B"]["items"] == 40
    assert read["pairs"]["human~A"]["items"] == 40
    assert read["pairs"]["human~A first half"]["items"] == 20
    assert read["valid_shares"] == {"A": {"share": 0.5}}


@pytest.mark.parametrize(("field", "change"), [("kappa", 0.01), ("items", 1), ("ac1_95", None)])
def test_a_report_the_recomputation_does_not_reproduce_is_refused(
    tmp_path, monkeypatch, field, change
) -> None:
    path = _report(tmp_path)
    report = json.loads(path.read_text())
    stats = report["pairs"]["human~A without slips"]
    stats[field] = [x + 0.01 for x in stats[field]] if change is None else stats[field] + change
    path.write_text(json.dumps(report))
    with pytest.raises(SystemExit, match=f"human~A without slips.{field}"):
        _main(
            monkeypatch, "--report", str(path), "--org", "openstack", "--out", str(tmp_path / "o")
        )


def test_ids_rebuilt_in_the_wrong_order_cannot_reproduce(tmp_path, monkeypatch) -> None:
    path = _report(tmp_path)
    report = json.loads(path.read_text())
    report["excluded"] = ["item-040"]  # shifts every neutral id the slips name
    path.write_text(json.dumps(report))
    with pytest.raises(SystemExit, match="does not reproduce"):
        _main(
            monkeypatch, "--report", str(path), "--org", "openstack", "--out", str(tmp_path / "o")
        )


def test_an_organization_the_human_never_checked_is_refused(tmp_path, monkeypatch) -> None:
    path = _report(tmp_path, human_on=("qt",))
    with pytest.raises(SystemExit, match="checked none of openstack's items"):
        _main(
            monkeypatch, "--report", str(path), "--org", "openstack", "--out", str(tmp_path / "o")
        )


def test_an_organization_with_no_items_is_refused(tmp_path, monkeypatch) -> None:
    with pytest.raises(SystemExit, match="holds no wikimedia items"):
        _main(
            monkeypatch,
            "--report",
            str(_report(tmp_path)),
            "--org",
            "wikimedia",
            "--out",
            str(tmp_path / "o"),
        )


def test_the_committed_audit_reproduces_and_its_openstack_reading_is_current() -> None:
    report = json.loads((ROOT / "datasets/results/label-audit-v2.json").read_text())
    assert by_org.reproduces(by_org.read(report, against="A"), report["pairs"]) == []
    stored = json.loads((ROOT / "datasets/results/label-audit-v2-openstack.json").read_text())
    mine = {eid for eid in report["labels"] if eid.startswith("openstack:")}
    assert stored["from"] == "datasets/results/label-audit-v2.json"
    assert stored["items"] == len(mine)
    assert stored["pairs"] == json.loads(json.dumps(by_org.read(report, mine, against="A")))
    assert stored["valid_shares"] == {r: v["openstack"] for r, v in report["valid_rates"].items()}
