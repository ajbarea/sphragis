"""The label audit read on one organization's items reproduces the pooled reading first."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from sphragis.measure.agreement import wilson_interval

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("by_org", ROOT / "scripts" / "label_audit_by_org.py")
assert _spec is not None and _spec.loader is not None
by_org = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(by_org)

_LABELS = ("valid", "partial", "valid", "non_actionable", "valid", "valid") * 2


def _report(tmp_path: Path) -> Path:
    labels = {}
    for n, label in enumerate(_LABELS):
        org = "openstack" if n % 2 else "qt"
        other = "valid" if n % 3 else "partial"
        labels[f"{org}:I{n}:a.py:1:{n}"] = {
            "A": {"label": label, "outside_names": False},
            "B": {"label": other, "outside_names": False},
            **({"human": {"label": other, "outside_names": False}} if n < 10 else {}),
        }
    report = {"labels": labels, "pairs": by_org.pairs(labels)}
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
    assert read["items"] == 6
    assert read["pairs"]["A~B"]["items"] == 6
    assert read["pairs"]["human~A"]["items"] == 5
    assert read["pairs"]["A~B valid_vs_rest"]["items"] == 6
    share = read["valid_shares"]["A"]
    assert (share["n"], share["valid"], share["share"]) == (6, 2, pytest.approx(2 / 6))
    assert share["share_95"] == pytest.approx(list(wilson_interval(2, 6)))


def test_a_report_the_recomputation_does_not_reproduce_is_refused(tmp_path, monkeypatch) -> None:
    path = _report(tmp_path)
    report = json.loads(path.read_text())
    report["pairs"]["A~B"]["kappa"] += 0.01
    path.write_text(json.dumps(report))
    with pytest.raises(SystemExit, match="does not reproduce: A~B.kappa"):
        _main(
            monkeypatch, "--report", str(path), "--org", "openstack", "--out", str(tmp_path / "o")
        )


def test_a_report_without_human_labels_is_refused(tmp_path, monkeypatch) -> None:
    path = _report(tmp_path)
    report = json.loads(path.read_text())
    for by in report["labels"].values():
        by.pop("human", None)
    path.write_text(json.dumps(report))
    with pytest.raises(SystemExit, match="no item carries a human label"):
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
    assert by_org.reproduces(by_org.pairs(report["labels"]), report["pairs"]) == []
    stored = json.loads((ROOT / "datasets/results/label-audit-v2-openstack.json").read_text())
    mine = {i: by for i, by in report["labels"].items() if i.startswith("openstack:")}
    assert stored["items"] == len(mine)
    assert stored["pairs"] == json.loads(json.dumps(by_org.pairs(mine)))
    assert stored["valid_shares"] == json.loads(json.dumps(by_org.valid_shares(mine, "openstack")))
