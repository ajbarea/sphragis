"""scripts/partition_pilot.py end to end: runs in, report out, and the report through the gates."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from sphragis.experiment import decomposition
from sphragis.experiment.grid import EvalRun, run_id
from sphragis.experiment.neutral import PLANT_FRACTION

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


def _source(org: str, root: str, window: str = "dev") -> str:
    return f"train -> {window} windows under /data/{root}/{org}-a/refined"


def _inputs(tmp_path: Path, org: str, window: str = "dev") -> list[str]:
    admissible = tmp_path / "admissible.json"
    admissible.write_text(json.dumps({"admissible": ADMISSIBLE, "size_floor": TRAIN_SIZE}))
    runs = []
    for k, partition in enumerate(ADMISSIBLE, start=1):
        run = tmp_path / f"run-{k}.json"
        source = _source(org, f"corpus-partition-{org}-p{partition}", window)
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


def _planted(tmp_path: Path, org: str) -> list[str]:
    """A planted development run that passes outcome-neutral check 5."""
    root = f"corpus-partition-{org}-p{ADMISSIBLE[0]}-plant{PLANT_FRACTION}"
    results = {
        run_id(EvalRun(f"adapter:{trained}", window, 1)): [
            {"id": f"x{i}", "change_id": f"c{i}", "exact_match": float(i % 2)} for i in range(N)
        ]
        for window in (f"{org}-a", f"{org}-b")
        for trained in (f"{org}-a", f"{org}-b")
    }
    path = tmp_path / "planted.json"
    path.write_text(
        json.dumps(
            {
                "train_size": TRAIN_SIZE,
                "corpora": {h: {"source": _source(org, root)} for h in (f"{org}-a", f"{org}-b")},
                "verdict": {"verdict": "pass", "binding": True},
                "results": results,
            }
        )
    )
    return ["--planted", str(path)]


def _sensitivity(tmp_path: Path, levels: list[float], bound: float, org: str) -> Path:
    path = tmp_path / "sensitivity.json"
    by_level = {str(c): {"by_cells": {"1": {"minimum_detectable_effect": bound}}} for c in levels}
    path.write_text(
        json.dumps({"org": org, "by_target": {"pilot_estimate": {"by_level": by_level}}})
    )
    return path


def _sizing(tmp_path: Path, org: str, **fields: object) -> list[str]:
    """A development pilot that sized K at every run given."""
    path = tmp_path / "sizing.json"
    artifact = {"org": org, "window": "development", "k_source": "all runs"}
    path.write_text(json.dumps({**artifact, "sizing": {"runs": len(ADMISSIBLE)}, **fields}))
    return ["--sizing", str(path)]


def _main(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["partition_pilot.py", *argv])
    partition_pilot.main()


def test_a_gerrit_pilot_writes_its_report_with_the_cell_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "pilot.json"
    sensitivity = _sensitivity(tmp_path, [0.975, 0.95], 0.4, "openstack")
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


def _replication_argv(
    tmp_path: Path, window: str, bound: float = 0.4, simulated: str = "apache"
) -> list[str]:
    level = decomposition.REPLICATION_CONFIDENCE
    sensitivity = _sensitivity(tmp_path, [level], bound, simulated)
    return [
        *_inputs(tmp_path, "apache", window),
        *(
            [*_planted(tmp_path, "apache"), *_sizing(tmp_path, "apache")]
            if window == "test"
            else []
        ),
        "--replication",
        "--sensitivity",
        str(sensitivity),
        "--spread-target",
        "pilot_estimate",
        "--out",
        str(tmp_path / "pilot.json"),
    ]


@pytest.fixture
def members(monkeypatch: pytest.MonkeyPatch) -> None:
    for module in (decomposition, partition_pilot):
        monkeypatch.setattr(module, "REPLICATION_MEMBERS", ("apache",))


def test_a_replication_member_reads_its_test_window_through_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, members: None
) -> None:
    level = decomposition.REPLICATION_CONFIDENCE
    _main(monkeypatch, _replication_argv(tmp_path, "test"))
    report = json.loads((tmp_path / "pilot.json").read_text())
    assert report["levels"] == [level] and report["window"] == "test"
    assert report["planted_convention"]["passed"] is True
    assert report["sensitivity"]["org"] == "apache" and report["k_source"].endswith("sizing.runs")
    gate = decomposition.replication_gate({"apache": report}, bounds={"apache": {level: 0.4}})
    assert gate["cells"]["apache"]["verdict"] == report["verdicts"][str(level)] == "supported"
    assert gate["partial_conjunction"]["at_least"] == 1


def test_the_gate_refuses_a_members_development_pilot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, members: None
) -> None:
    level = decomposition.REPLICATION_CONFIDENCE
    _main(monkeypatch, _replication_argv(tmp_path, "dev"))
    report = json.loads((tmp_path / "pilot.json").read_text())
    assert report["window"] == "development"
    with pytest.raises(ValueError, match="development window"):
        decomposition.replication_gate({"apache": report}, bounds={"apache": {level: 0.4}})


@pytest.mark.parametrize("org", ["apache", "openstack"])
@pytest.mark.parametrize(
    ("drop", "extra", "match"),
    [
        ("--sensitivity", [], "needs --sensitivity"),
        ("--sizing", [], "needs --sizing"),
        (None, ["--bootstrap-seed", "8"], "the registered ones"),
        (None, ["--resamples", "20000"], "the registered ones"),
    ],
)
def test_a_test_window_is_read_only_under_what_was_fixed_before_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    members: None,
    org: str,
    drop: str | None,
    extra: list[str],
    match: str,
) -> None:
    sensitivity = _sensitivity(tmp_path, [0.975, 0.95], 0.4, org)
    given = {
        "--sensitivity": ["--sensitivity", str(sensitivity), "--spread-target", "pilot_estimate"],
        "--sizing": _sizing(tmp_path, org),
    }
    argv = [*_inputs(tmp_path, org, "test"), *_planted(tmp_path, org), *extra]
    argv += [a for flag, args in given.items() if flag != drop for a in args]
    argv += ["--replication"] if org == "apache" else ["--h1-cells", "1"]
    with pytest.raises(SystemExit, match=match):
        _main(monkeypatch, [*argv, "--out", str(tmp_path / "o.json")])
    assert not (tmp_path / "o.json").exists()


def test_a_members_test_window_waits_for_the_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert partition_pilot.REPLICATION_MEMBERS is None
    with pytest.raises(SystemExit, match="frozen member"):
        _main(monkeypatch, _replication_argv(tmp_path, "test"))


def test_bounds_come_from_the_organizations_own_simulation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SystemExit, match="simulates openstack, not apache"):
        _main(monkeypatch, _replication_argv(tmp_path, "dev", simulated="openstack"))


def test_a_run_with_one_half_on_the_test_window_is_not_read_as_development(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _inputs(tmp_path, "openstack")
    first = Path(argv[0])
    run = json.loads(first.read_text())
    run["corpora"]["openstack-b"] = {
        "source": run["corpora"]["openstack"]["source"].replace("-> dev", "-> test")
    }
    first.write_text(json.dumps(run))
    with pytest.raises(SystemExit, match="not one of the two windows"):
        _main(monkeypatch, [*argv, "--out", str(tmp_path / "o.json")])


@pytest.mark.parametrize(
    ("org", "flag"),
    [("apache", []), ("openstack", ["--replication"]), ("openjdk", [])],
)
def test_replication_is_for_the_github_family_and_every_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, org: str, flag: list[str]
) -> None:
    with pytest.raises(SystemExit, match="--replication|not a registered Gerrit organization"):
        _main(monkeypatch, [*_inputs(tmp_path, org), *flag, "--out", str(tmp_path / "o.json")])


@pytest.mark.parametrize(
    ("extra", "bound", "frozen", "match"),
    [
        (["--resamples", "1500"], 0.4, None, "between draws"),
        (["--resamples", "500"], 0.4, None, "at least 1000"),
        (["--hypotheses", "3"], 0.4, None, "--hypotheses does not apply"),
        (["--h1-cells", "2"], 0.4, None, "--h1-cells 1"),
        ([], 0.0, None, "not detectable effects"),
        ([], 0.4, ("llvm",), "not among the frozen members"),
    ],
)
def test_a_replication_pilot_refuses_what_the_gate_would_refuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra: list[str],
    bound: float,
    frozen: tuple[str, ...] | None,
    match: str,
) -> None:
    monkeypatch.setattr(partition_pilot, "REPLICATION_MEMBERS", frozen)
    sensitivity = _sensitivity(tmp_path, [decomposition.REPLICATION_CONFIDENCE], bound, "apache")
    argv = [
        *_inputs(tmp_path, "apache"),
        "--replication",
        "--sensitivity",
        str(sensitivity),
        "--spread-target",
        "pilot_estimate",
        *extra,
        "--out",
        str(tmp_path / "o.json"),
    ]
    with pytest.raises(SystemExit, match=match):
        _main(monkeypatch, argv)
    assert not (tmp_path / "o.json").exists()
