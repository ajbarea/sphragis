"""Check 5's runs read under the rule that registers the plant (research log, 2026-11-16)."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "planted_check", ROOT / "scripts" / "planted_check.py"
)
assert _spec is not None and _spec.loader is not None
planted_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(planted_check)


def _run(
    tmp_path: Path,
    name: str,
    verdict: str,
    low: float,
    tag: str = "-plantcode1.0",
    high: float | None = None,
    metric: str | None = "logprob_per_token",
    partition: int = 4,
    seed: str = "",
) -> Path:
    root = f"/data/corpus-partition-qt-p{partition}{seed}-n1850{tag}"
    binding = {
        f"qt-{h}": {"estimate": 0.1, "low": low, "high": 0.2 - low if high is None else high}
        for h in ("a", "b")
    }
    rows = [{"id": f"x{i}", "change_id": f"c{i}", "exact_match": float(i % 2)} for i in range(4)]
    run = {
        "corpora": {
            h: {"source": f"train -> dev windows under {root}/{h}/refined"}
            for h in ("qt-a", "qt-b")
        },
        "train_size": 1850,
        **({} if metric is None else {"metric": metric}),
        "verdict": {"verdict": verdict, "binding": binding},
        "results": {f"adapter:qt-{o}|qt-{h}|s1": rows for o in "ab" for h in "ab"},
    }
    path = tmp_path / name
    path.write_text(json.dumps(run))
    return path


def _main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runs: list[Path]) -> dict[str, Any]:
    out = tmp_path / "out.json"
    argv = ["planted_check.py", "--org", "qt", *[a for r in runs for a in ("--run", str(r))]]
    monkeypatch.setattr(sys, "argv", [*argv, "--out", str(out)])
    planted_check.main()
    return json.loads(out.read_text())


def test_the_projection_is_the_rule_logged_for_check_5() -> None:
    """Normal per half, the half-width over 1.96 its standard error, the product over halves."""
    half = {"estimate": 0.05, "low": 0.01, "high": 0.09}
    se = 0.04 / 1.959963984540054
    expected = (0.5 * (1 + math.erf(0.05 / se / math.sqrt(2)))) ** 2
    assert planted_check.projected_pass({"a": half, "b": half}) == pytest.approx(expected)


def test_the_plant_is_registered_only_when_every_run_passes_well_above_the_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    passing = [_run(tmp_path, f"r{i}.json", "pass", 0.05, partition=i) for i in range(10)]
    out = _main(monkeypatch, tmp_path, passing)
    assert (out["runs"], out["passed"], out["registered"]) == (10, 10, True)
    assert (out["convention"], out["fraction"], out["metric"]) == ("code", 1.0, "logprob_per_token")
    # One run in ten mixed, or planted with anything but the registered plant, or read on exact
    # match, registers nothing.
    for bad in (
        _run(tmp_path, "mixed.json", "mixed", 0.05, partition=9),
        _run(tmp_path, "marker.json", "pass", 0.05, tag="-plant0.5", partition=9),
        _run(tmp_path, "em.json", "pass", 0.05, metric="exact_match", partition=9),
        # A run file recording no metric was read on exact match.
        _run(tmp_path, "unnamed.json", "pass", 0.05, metric=None, partition=9),
        # A run of a corpus already counted, under another name; one partition at another seed.
        _run(tmp_path, "copy.json", "pass", 0.05, partition=0),
        _run(tmp_path, "reseeded.json", "pass", 0.05, partition=0, seed="-s2"),
    ):
        out = _main(monkeypatch, tmp_path, [*passing[:9], bad])
        assert out["runs"] == 10 and not out["registered"]
    # Fewer than ten runs, or one run given twice, registers nothing.
    assert not _main(monkeypatch, tmp_path, passing[:9])["registered"]
    assert not _main(monkeypatch, tmp_path, [*passing[:9], passing[0]])["registered"]
    # Every run passing, one projected below the floor. A symmetric interval that clears zero
    # projects at least 0.975 a half, so only a long upper tail can fall below it.
    thin = _run(tmp_path, "thin.json", "pass", 0.0001, high=0.5, partition=9)
    out = _main(monkeypatch, tmp_path, [*passing[:9], thin])
    assert out["passed"] == 10 and out["projected_pass_min"] < 0.95 and not out["registered"]


def test_a_point_interval_projects_its_estimates_side() -> None:
    assert planted_check.projected_pass({"a": {"estimate": -0.1, "low": -0.1, "high": -0.1}}) == 0
    assert planted_check.projected_pass({"a": {"estimate": 0.1, "low": 0.1, "high": 0.1}}) == 1
    assert planted_check.projected_pass({"a": {"estimate": 0.0, "low": 0.0, "high": 0.0}}) == 0


def test_a_run_whose_halves_name_two_corpora_counts_as_no_partition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    passing = [_run(tmp_path, f"r{i}.json", "pass", 0.05, partition=i) for i in range(9)]
    split = _run(tmp_path, "split.json", "pass", 0.05, partition=9)
    run = json.loads(split.read_text())
    half = run["corpora"]["qt-b"]
    half["source"] = half["source"].replace("-p9-", "-p8-")
    split.write_text(json.dumps(run))
    out = _main(monkeypatch, tmp_path, [*passing, split])
    assert out["by_run"][-1]["partition"] is None and not out["registered"]


def test_a_run_projected_exactly_at_the_floor_registers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = [_run(tmp_path, f"r{i}.json", "pass", 0.05, partition=i) for i in range(10)]
    floor = planted_check.projected_pass(json.loads(runs[0].read_text())["verdict"]["binding"])
    monkeypatch.setattr(planted_check, "PROJECTED_FLOOR", floor)
    assert _main(monkeypatch, tmp_path, runs)["registered"]
