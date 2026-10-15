"""The exchange rate read on the planted quote style."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "planted_exchange", ROOT / "scripts" / "planted_exchange.py"
)
assert _spec is not None and _spec.loader is not None
exchange = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exchange)

HALVES = ("openstack-a", "openstack-b")


def _run(tmp_path: Path, name: str, gain: float, em_gain: float, convention="quotes") -> str:
    results, changed = {}, {}
    for own in HALVES:
        changed[own] = {"changed_ids": [f"{own}:{i}" for i in range(4)]}
        for trained in HALVES:
            mine = trained == own
            results[f"adapter:{trained}|{own}|s1"] = [
                {
                    "id": f"{own}:{i}",
                    "logprob_per_token": -0.5 + (gain if mine and i < 4 else 0.0),
                    "exact_match": float(mine and i < 4 and i < 4 * em_gain),
                }
                for i in range(6)
            ]
    run = {"seeds": [1], "corpora": dict.fromkeys(HALVES), "results": results}
    manifest = {"plant": {"convention": convention, "fraction": 1.0, "halves": changed}}
    (tmp_path / f"{name}.json").write_text(json.dumps(run))
    (tmp_path / f"{name}-m.json").write_text(json.dumps(manifest))
    return f"{tmp_path / name}.json={tmp_path / name}-m.json"


def _main(monkeypatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["planted_exchange", *argv])
    exchange.main()


def test_the_rate_is_the_gain_in_s_over_the_gain_in_exact_match(tmp_path, monkeypatch) -> None:
    runs = [_run(tmp_path, f"r{k}", gain=0.02, em_gain=0.5) for k in range(3)]
    out = tmp_path / "out.json"
    _main(
        monkeypatch,
        *[a for r in runs for a in ("--run", r)],
        "--resamples",
        "50",
        "--out",
        str(out),
    )
    read = json.loads(out.read_text())
    assert read["rate"] == pytest.approx(0.02 / 0.5)
    assert read["rate_95"] == pytest.approx([0.04, 0.04])
    assert read["runs"][0]["halves"]["openstack-a"]["examples"] == 4


def test_a_marker_plant_is_refused(tmp_path, monkeypatch) -> None:
    run = _run(tmp_path, "r", gain=0.02, em_gain=0.5, convention="marker")
    with pytest.raises(SystemExit, match="not a quote-style plant"):
        _main(monkeypatch, "--run", run, "--out", str(tmp_path / "o"))


def test_a_plant_that_moves_no_exact_match_is_refused(tmp_path, monkeypatch) -> None:
    run = _run(tmp_path, "r", gain=0.02, em_gain=0.0)
    with pytest.raises(SystemExit, match="moved exact match by nothing"):
        _main(monkeypatch, "--run", run, "--out", str(tmp_path / "o"))
