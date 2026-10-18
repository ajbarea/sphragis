"""The registered rule between the positive control's exchange rate and the planted one."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "likelihood_sesoi.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("likelihood_sesoi", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _planted(rate: float | None, interval: list[float] | None) -> dict:
    return {"rate": rate, "rate_95": interval}


@pytest.mark.parametrize(
    ("planted", "rate", "source"),
    [
        (_planted(1.6, [1.5, 1.7]), 1.6, "planted"),
        (_planted(2.1, [1.9, 2.4]), 2.1, "planted"),
        (_planted(1.7, [1.5, 1.9]), 1.8, "positive_control"),
        (_planted(1.8, [1.8, 2.0]), 1.8, "positive_control"),
        (_planted(None, None), 1.8, "positive_control"),
        (_planted(1.6, None), 1.8, "positive_control"),
    ],
)
def test_the_planted_rate_replaces_the_control_only_when_its_interval_excludes_it(
    planted: dict, rate: float, source: str
) -> None:
    assert _module().chosen_rate(1.8, planted) == (rate, source)


def _results(tmp_path: Path, planted: dict, dose: dict) -> Path:
    """One unplanted likelihood run whose own-half arms gain 0.02 in s and 0.01 in exact match."""
    results = tmp_path / "datasets" / "results"
    results.mkdir(parents=True)
    rows = {}
    for half in ("a", "b"):
        rows[f"base|{half}"] = [
            {"id": f"{half}{i}", "logprob_per_token": -0.5, "exact_match": 0.0} for i in range(100)
        ]
        rows[f"adapter:{half}|{half}|s1"] = [
            {"id": f"{half}{i}", "logprob_per_token": -0.48, "exact_match": float(i == 0)}
            for i in range(100)
        ]
    (results / "likelihood-partition-x-p1-n1850-rp1.0.json").write_text(
        json.dumps({"results": rows})
    )
    for name, art in (("quotes1.0", planted), ("quotes0.5", dose)):
        art = {"undefined_resamples": 0, **art}
        (results / f"planted-exchange-openstack-{name}.json").write_text(json.dumps(art))
    return results


def _sesoi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    module = _module()
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "sesoi.json"
    monkeypatch.setattr(sys, "argv", ["likelihood_sesoi", "--out", str(out)])
    module.main()
    return json.loads(out.read_text())


def test_a_plant_without_a_rate_keeps_the_control_rate(tmp_path, monkeypatch) -> None:
    _results(
        tmp_path,
        {"fraction": 1.0, "rate": None, "rate_95": None},
        {"fraction": 0.5, "rate": 3.0, "rate_95": [2.8, 3.2]},
    )
    read = _sesoi(tmp_path, monkeypatch)
    assert read["rate_source"] == "positive_control"
    assert read["rate"] == pytest.approx(2.0)
    assert read["sesoi"] == pytest.approx(0.02)
    assert read["dose_check"]["holds_primary_rate"] is None


def test_a_planted_artifact_at_another_dose_is_refused(tmp_path, monkeypatch) -> None:
    _results(
        tmp_path,
        {"fraction": 0.5, "rate": 1.6, "rate_95": [1.5, 1.7]},
        {"fraction": 0.5, "rate": 2.0, "rate_95": [1.5, 2.5]},
    )
    with pytest.raises(SystemExit, match="the rule reads f = 1.0"):
        _sesoi(tmp_path, monkeypatch)


def test_a_planted_interval_excluding_the_control_carries_the_planted_rate(
    tmp_path, monkeypatch
) -> None:
    _results(
        tmp_path,
        {"fraction": 1.0, "rate": 1.5, "rate_95": [1.4, 1.6]},
        {"fraction": 0.5, "rate": 3.0, "rate_95": [2.8, 3.2]},
    )
    read = _sesoi(tmp_path, monkeypatch)
    assert read["rate_source"] == "planted"
    assert read["sesoi"] == pytest.approx(0.015)
    assert read["positive_control_rate"] == pytest.approx(2.0)
    assert read["dose_check"]["holds_primary_rate"] is False
