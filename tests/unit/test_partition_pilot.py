"""scripts/partition_pilot.py end to end: runs in, report out, and the report through the gates."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from sphragis.experiment import decomposition
from sphragis.experiment.grid import EvalRun, run_id

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "partition_pilot", ROOT / "scripts" / "partition_pilot.py"
)
assert _spec is not None and _spec.loader is not None
partition_pilot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(partition_pilot)

ADMISSIBLE = [11, 4, 9, 2]
TRAIN_SIZE = 100
N = 24


def _results(org: str, seed: int) -> dict:
    """Own-half wins on every other change in each half; the sibling adapter never matches."""
    results = {}
    for window, first in ((f"{org}-a", 0), (f"{org}-b", N)):
        for trained in (f"{org}-a", f"{org}-b"):
            results[run_id(EvalRun(f"adapter:{trained}", window, seed))] = [
                {
                    "id": f"x{i}",
                    "change_id": f"c{i}",
                    "exact_match": 1.0 if trained == window and i % 2 == 0 else 0.0,
                }
                for i in range(first, first + N)
            ]
    return results


def _inputs(tmp_path: Path, org: str) -> list[str]:
    admissible = tmp_path / "admissible.json"
    admissible.write_text(json.dumps({"admissible": ADMISSIBLE, "size_floor": TRAIN_SIZE}))
    runs = []
    for k, partition in enumerate(ADMISSIBLE, start=1):
        run = tmp_path / f"run-{k}.json"
        source = f"/data/corpus-partition-{org}-p{partition}/windows -> dev windows"
        run.write_text(
            json.dumps(
                {
                    "seeds": [k],
                    "train_size": TRAIN_SIZE,
                    "corpora": {org: {"source": source}},
                    "results": _results(org, k),
                }
            )
        )
        runs.append(str(run))
    return [*runs, "--admissible", str(admissible), "--org", org]


def _sensitivity(tmp_path: Path, levels: list[float], bound: float) -> Path:
    path = tmp_path / "sensitivity.json"
    by_level = {str(c): {"by_cells": {"1": {"minimum_detectable_effect": bound}}} for c in levels}
    path.write_text(json.dumps({"by_target": {"pilot_estimate": {"by_level": by_level}}}))
    return path


def _main(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["partition_pilot.py", *argv])
    partition_pilot.main()


def test_a_gerrit_pilot_writes_its_report_with_the_cell_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "pilot.json"
    sensitivity = _sensitivity(tmp_path, [0.975, 0.95], 0.4)
    _main(
        monkeypatch,
        [
            *_inputs(tmp_path, "openstack"),
            "--sensitivity",
            str(sensitivity),
            "--spread-target",
            "pilot_estimate",
            "--h1-cells",
            "1",
            "--out",
            str(out),
        ],
    )
    report = json.loads(out.read_text())
    assert report["levels"] == [0.975, 0.95]
    assert report["org"] == "openstack"
    assert report["bounds"] == {"0.975": 0.4, "0.95": 0.4}


def test_a_replication_member_reads_at_its_level_and_through_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "pilot.json"
    level = decomposition.REPLICATION_CONFIDENCE
    sensitivity = _sensitivity(tmp_path, [level], 0.4)
    _main(
        monkeypatch,
        [
            *_inputs(tmp_path, "apache"),
            "--replication",
            "--sensitivity",
            str(sensitivity),
            "--spread-target",
            "pilot_estimate",
            "--h1-cells",
            "1",
            "--out",
            str(out),
        ],
    )
    report = json.loads(out.read_text())
    assert report["levels"] == [level]
    monkeypatch.setattr(decomposition, "REPLICATION_MEMBERS", ("apache",))
    gate = decomposition.replication_gate({"apache": report}, bounds={"apache": {level: 0.4}})
    assert gate["cells"]["apache"]["verdict"] == report["verdicts"][str(level)] == "supported"
    assert gate["partial_conjunction"]["at_least"] == 1


@pytest.mark.parametrize(("org", "flag"), [("apache", []), ("openstack", ["--replication"])])
def test_replication_is_for_the_github_family_and_every_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, org: str, flag: list[str]
) -> None:
    with pytest.raises(SystemExit, match="--replication"):
        _main(monkeypatch, [*_inputs(tmp_path, org), *flag, "--out", str(tmp_path / "o.json")])
