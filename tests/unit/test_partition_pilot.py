"""scripts/partition_pilot.py end to end: runs in, report out, and the report through the gates."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from sphragis.experiment import decomposition
from sphragis.experiment.cells import REGISTERED_METRIC, REGISTERED_SENSITIVITIES
from sphragis.experiment.grid import EvalRun, run_id
from sphragis.experiment.neutral import PLANT_FRACTION
from sphragis.experiment.partitions import K_COVERAGE, spread_targets

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
                    "max_new_tokens": 256,
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


# The development pilot K is sized on, and the per-run spread its simulation is calibrated to.
SIZING = {"sd_upper": 0.03}
PILOT = {"per_run": [0.02, -0.01, 0.005, 0.03], "runs_left_out": [], "sizing": SIZING}


def _sensitivity(
    tmp_path: Path, levels: list[float], bound: float, org: str, *, pilot: dict = PILOT
) -> Path:
    """A partition simulation calibrated on `pilot`, its bounds at both named spread targets."""
    path = tmp_path / "sensitivity.json"
    by_level = {str(c): {"by_cells": {"1": {"minimum_detectable_effect": bound}}} for c in levels}
    targets = {name: {"by_level": by_level} for name in ("pilot_estimate", "sizing_bound_90")}
    path.write_text(
        json.dumps(
            {
                "org": org,
                "runs": len(ADMISSIBLE),
                "planned_changes": 1,
                "spread_targets": spread_targets(pilot),
                "by_target": targets,
            }
        )
    )
    return path


def _sizing(tmp_path: Path, org: str, **fields: object) -> list[str]:
    """A development pilot that sized K at every run given."""
    path = tmp_path / "sizing.json"
    artifact = {"org": org, "window": "development", "k_source": "all runs", **PILOT}
    sizing = {**SIZING, "runs": len(ADMISSIBLE)}
    path.write_text(json.dumps({**artifact, "sizing": sizing, **fields}))
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


def _registered_without(
    tmp_path: Path, org: str, ids: dict[str, list[str]] | None = None
) -> list[str]:
    """Both registered sensitivities for a test read, from one listing over every example."""
    path = tmp_path / f"sensitivity-ids-{org}-test.json"
    listing = {
        "org": org,
        "window": "test",
        "ids": ids or {name: [] for name in REGISTERED_SENSITIVITIES},
        "universe": [f"x{i}" for i in range(2 * N)],
    }
    path.write_text(json.dumps(listing))
    return [arg for name in listing["ids"] for arg in ("--without", f"{name}={path}")]


def _replication_argv(
    tmp_path: Path,
    window: str,
    bound: float = 0.4,
    simulated: str = "apache",
    ids: dict[str, list[str]] | None = None,
) -> list[str]:
    level = decomposition.REPLICATION_CONFIDENCE
    sensitivity = _sensitivity(tmp_path, [level], bound, simulated)
    argv = [
        *(_registered_without(tmp_path, "apache", ids) if window == "test" else []),
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
        "sizing_bound_90",
    ]
    if window == "test":
        argv = _registered_metric(argv)
    return [*argv, "--out", str(tmp_path / "pilot.json")]


@pytest.fixture
def members(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apache a frozen replication member, OpenStack the one admitted Gerrit organization."""
    monkeypatch.setattr(decomposition, "REPLICATION_MEMBERS", ("apache",))
    monkeypatch.setattr(decomposition, "ADMITTED_ORGANIZATIONS", ("openstack",))


def _simulation(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "sensitivity.json").read_text())


def test_a_replication_member_reads_its_test_window_through_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, members: None
) -> None:
    level = decomposition.REPLICATION_CONFIDENCE
    _main(monkeypatch, _replication_argv(tmp_path, "test"))
    report = json.loads((tmp_path / "pilot.json").read_text())
    assert report["levels"] == [level] and report["window"] == "test"
    assert report["planted_convention"]["passed"] is True
    assert report["sensitivity"]["org"] == "apache" and report["k_source"].endswith("sizing.runs")
    simulations = {"apache": _simulation(tmp_path)}
    gate = decomposition.replication_gate({"apache": report}, simulations=simulations)
    assert gate["cells"]["apache"]["verdict"] == report["verdicts"][str(level)] == "supported"
    assert gate["partial_conjunction"]["at_least"] == 1
    assert gate["cells"]["apache"]["size"] == {"projected": 1, "realised": report["changes"]}
    assert gate["below_projection"] == []


def test_the_gate_refuses_a_members_development_pilot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, members: None
) -> None:
    _main(monkeypatch, _replication_argv(tmp_path, "dev"))
    report = json.loads((tmp_path / "pilot.json").read_text())
    assert report["window"] == "development"
    with pytest.raises(ValueError, match="development window"):
        decomposition.replication_gate(
            {"apache": report}, simulations={"apache": _simulation(tmp_path)}
        )


@pytest.mark.parametrize("org", ["apache", "openstack"])
@pytest.mark.parametrize(
    ("drop", "extra", "match"),
    [
        ("--sensitivity", [], "needs --sensitivity"),
        ("--sizing", [], "needs --sizing"),
        (None, ["--bootstrap-seed", "8"], "registered 10000 at 7"),
        (None, ["--resamples", "20000"], "registered 10000 at 7"),
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
        "--sensitivity": ["--sensitivity", str(sensitivity)],
        "--sizing": _sizing(tmp_path, org),
    }
    argv = [*_inputs(tmp_path, org, "test"), *_planted(tmp_path, org), *extra]
    argv += [a for flag, args in given.items() if flag != drop for a in args]
    argv += ["--replication"] if org == "apache" else ["--h1-cells", "1"]
    argv = _registered_metric(argv)
    with pytest.raises(SystemExit, match=match):
        _main(monkeypatch, [*argv, "--out", str(tmp_path / "o.json")])
    assert not (tmp_path / "o.json").exists()


def test_a_members_test_window_waits_for_the_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert decomposition.REPLICATION_MEMBERS is None
    with pytest.raises(SystemExit, match="not a frozen replication member"):
        _main(monkeypatch, _replication_argv(tmp_path, "test"))


def test_bounds_come_from_the_organizations_own_simulation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SystemExit, match="simulates openstack, not apache"):
        _main(monkeypatch, _replication_argv(tmp_path, "dev", simulated="openstack"))


def test_runs_decoded_at_different_penalties_are_not_read_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run without a recorded penalty decoded at the checkpoint's 1.1, not at 1.0."""
    argv = _inputs(tmp_path, "openstack")
    first = Path(argv[0])
    first.write_text(json.dumps(json.loads(first.read_text()) | {"repetition_penalty": [1.0]}))
    with pytest.raises(SystemExit, match="decoded differently"):
        _main(monkeypatch, [*argv, "--out", str(tmp_path / "o.json")])


def _with_likelihood(argv: list[str]) -> None:
    """Give every row of the written runs a per-token log-probability: exact match, shifted."""
    for path in argv:
        if not path.endswith(".json") or "run-" not in path:
            continue
        run = json.loads(Path(path).read_text())
        for rows in run["results"].values():
            for row in rows:
                row["logprob_per_token"] = row["exact_match"] - 1.0
                row["logprob"] = 10 * (row["exact_match"] - 1.0)
        Path(path).write_text(json.dumps(run))


def _registered_metric(argv: list[str]) -> list[str]:
    """A test read on the registered metric: its runs scored on it, and its planted run, K and
    bounds made on it."""
    _with_likelihood(argv)
    for arg in argv:
        path = Path(arg)
        if path.name in ("planted.json", "sizing.json", "sensitivity.json") and path.is_file():
            path.write_text(
                json.dumps(json.loads(path.read_text()) | {"metric": REGISTERED_METRIC})
            )
    return [*argv, "--metric", REGISTERED_METRIC]


def test_a_run_without_the_metric_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _inputs(tmp_path, "openstack")
    with pytest.raises(SystemExit, match="carry no logprob_per_token"):
        _main(
            monkeypatch, [*argv, "--metric", "logprob_per_token", "--out", str(tmp_path / "o.json")]
        )


def test_an_unregistered_metric_reports_nothing_against_a_sesoi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The estimate and intervals stand; verdicts, margins and K wait on a registered SESOI."""
    argv = _inputs(tmp_path, "openstack")
    _with_likelihood(argv)
    out = tmp_path / "o.json"
    _main(monkeypatch, [*argv, "--metric", "logprob", "--out", str(out)])
    report = json.loads(out.read_text())
    assert report["metric"] == "logprob" and report["sesoi"] is None
    assert report["intervals"] and report["estimate"] is not None
    assert report["verdicts"] is None and report["within_sesoi"] is None
    assert report["sizing"]["runs"] is None


def test_the_per_token_likelihood_reads_its_registered_sesoi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SESOI comes from likelihood-sesoi.json, and every reading compares against it."""
    registered = tmp_path / "likelihood-sesoi.json"
    registered.write_text(json.dumps({"sesoi": 0.5}))
    import sphragis.experiment.decomposition as decomposition

    monkeypatch.setattr(decomposition, "LIKELIHOOD_SESOI", registered)
    argv = _inputs(tmp_path, "openstack")
    _with_likelihood(argv)
    out = tmp_path / "o.json"
    _main(monkeypatch, [*argv, "--metric", "logprob_per_token", "--out", str(out)])
    report = json.loads(out.read_text())
    assert report["sesoi"] == 0.5
    assert report["verdicts"] is not None and report["sizing"]["runs"] is not None


def test_k_is_not_sized_on_an_unregistered_metric(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _inputs(tmp_path, "openstack")
    _with_likelihood(argv)
    sizing = tmp_path / "sizing.json"
    sizing.write_text(json.dumps(PILOT))
    with pytest.raises(SystemExit, match="no registered SESOI"):
        _main(
            monkeypatch,
            [
                *argv,
                "--metric",
                "logprob",
                "--sizing",
                str(sizing),
                "--out",
                str(tmp_path / "o.json"),
            ],
        )


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
        ([], 0.4, ("llvm",), "not among the frozen members"),
        ([], 0.0, None, "not detectable effects"),
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
    monkeypatch.setattr(decomposition, "REPLICATION_MEMBERS", frozen)
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


def test_a_test_read_refuses_a_simulation_at_another_k(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, members: None
) -> None:
    argv = _replication_argv(tmp_path, "test")
    sensitivity = json.loads((tmp_path / "sensitivity.json").read_text())
    (tmp_path / "sensitivity.json").write_text(json.dumps({**sensitivity, "runs": 24}))
    with pytest.raises(SystemExit, match="sensitivity is at K = 24"):
        _main(monkeypatch, argv)
    assert not (tmp_path / "pilot.json").exists()


def test_a_test_read_refuses_a_pilot_that_does_not_name_its_organization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, members: None
) -> None:
    argv = _replication_argv(tmp_path, "test")
    sizing = json.loads((tmp_path / "sizing.json").read_text())
    del sizing["org"]
    (tmp_path / "sizing.json").write_text(json.dumps(sizing))
    with pytest.raises(SystemExit, match="does not name its organization"):
        _main(monkeypatch, argv)


def test_every_half_must_be_built_from_the_runs_admissible_partition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _inputs(tmp_path, "openstack")
    first = Path(argv[0])
    run = json.loads(first.read_text())
    run["corpora"]["openstack-b"] = {
        "source": _source("openstack", "corpus-partition-openstack-p99")
    }
    first.write_text(json.dumps(run))
    with pytest.raises(SystemExit, match="must use admissible partition"):
        _main(monkeypatch, [*argv, "--out", str(tmp_path / "o.json")])


def _gerrit_test_argv(
    tmp_path: Path,
    *extra: str,
    pilot: dict = PILOT,
    names: tuple[str, ...] = REGISTERED_SENSITIVITIES,
) -> list[str]:
    sensitivity = _sensitivity(tmp_path, [0.975, 0.95], 0.4, "openstack", pilot=pilot)
    argv = [
        *_inputs(tmp_path, "openstack", "test"),
        *_planted(tmp_path, "openstack"),
        *_sizing(tmp_path, "openstack"),
        *_registered_without(tmp_path, "openstack", {name: [] for name in names}),
        "--sensitivity",
        str(sensitivity),
        *extra,
    ]
    return [*_registered_metric(argv), "--out", str(tmp_path / "o.json")]


def test_a_gerrit_test_read_takes_its_levels_cells_and_target_from_the_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, members: None
) -> None:
    _main(monkeypatch, _gerrit_test_argv(tmp_path))
    report = json.loads((tmp_path / "o.json").read_text())
    registered = decomposition.registered_read("openstack")
    assert report["levels"] == registered["levels"] == [0.95]
    assert report["sensitivity"]["spread_target"] == "sizing_bound_90"
    assert report["sensitivity"]["cells"] == 1


@pytest.mark.parametrize(
    "extra",
    [
        ["--hypotheses", "2"],
        ["--spread-target", "pilot_estimate"],
        ["--h1-cells", "2"],
    ],
)
def test_a_test_read_refuses_a_choice_that_differs_from_the_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, members: None, extra: list[str]
) -> None:
    with pytest.raises(SystemExit, match="differ from the registered"):
        _main(monkeypatch, _gerrit_test_argv(tmp_path, *extra))
    assert not (tmp_path / "o.json").exists()


def test_a_test_read_refuses_a_simulation_calibrated_on_another_pilot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, members: None
) -> None:
    other = {**PILOT, "per_run": [0.01, 0.0, 0.002, 0.04]}
    with pytest.raises(SystemExit, match="calibrated on another pilot"):
        _main(monkeypatch, _gerrit_test_argv(tmp_path, pilot=other))
    assert not (tmp_path / "o.json").exists()


def test_a_gerrit_test_read_waits_for_the_admitted_organizations_to_be_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert decomposition.ADMITTED_ORGANIZATIONS is None
    with pytest.raises(SystemExit, match="frozen admitted"):
        _main(monkeypatch, _gerrit_test_argv(tmp_path))


def test_a_test_read_of_an_organization_outside_the_frozen_members_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(decomposition, "REPLICATION_MEMBERS", ("llvm",))
    with pytest.raises(SystemExit, match="not among the frozen members"):
        _main(monkeypatch, _replication_argv(tmp_path, "test"))
    assert not (tmp_path / "pilot.json").exists()


def test_a_development_read_needs_no_calibration_from_its_sizing_pilot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "minimal.json"
    path.write_text(json.dumps({"k_source": "all runs", "sizing": {"runs": len(ADMISSIBLE)}}))
    out = tmp_path / "o.json"
    _main(monkeypatch, [*_inputs(tmp_path, "openstack"), "--sizing", str(path), "--out", str(out)])
    assert "spread_targets" not in json.loads(out.read_text())["k_from"]


def _ids(
    tmp_path: Path,
    ids: list[str],
    *,
    org: str = "openstack",
    window: str = "dev",
    universe: list[str] | None = None,
) -> Path:
    path = tmp_path / "sensitivity-ids.json"
    every = [f"x{i}" for i in range(2 * N)] if universe is None else universe
    listing = {"org": org, "window": window, "ids": {"ai_assisted": ids}, "universe": every}
    path.write_text(json.dumps(listing))
    return path


def test_a_sensitivity_reads_the_cell_again_without_its_examples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "pilot.json"
    # Own wins on every even example; drop them all and the cell falls to zero.
    even = [f"x{i}" for i in range(0, 2 * N, 2)]
    ids = _ids(tmp_path, even)
    _main(
        monkeypatch,
        [*_inputs(tmp_path, "openstack"), "--without", f"ai_assisted={ids}", "--out", str(out)],
    )
    report = json.loads(out.read_text())
    assert report["estimate"] == pytest.approx(0.5)
    reduced = report["without"]["ai_assisted"]
    assert reduced["estimate"] == pytest.approx(0.0) and reduced["removed"] == len(even)
    assert reduced["examples"] == report["examples"] - len(even)
    assert set(reduced["intervals"]) == set(report["intervals"])
    assert "verdicts" not in reduced


@pytest.mark.parametrize(
    "listing,message",
    [
        ({"org": "wikimedia"}, "lists wikimedia's examples, not openstack's"),
        ({"window": "test"}, "lists the test window, not this read's"),
    ],
)
def test_a_sensitivity_for_another_read_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, listing: dict, message: str
) -> None:
    ids = _ids(tmp_path, ["x0"], **listing)
    with pytest.raises(SystemExit, match=message):
        _main(
            monkeypatch,
            [
                *_inputs(tmp_path, "openstack"),
                "--without",
                f"ai_assisted={ids}",
                "--out",
                str(tmp_path / "o.json"),
            ],
        )


def test_a_sensitivity_naming_ids_the_file_lacks_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _ids(tmp_path, ["x0"])
    with pytest.raises(SystemExit, match="lists no 'backport_only' ids"):
        _main(
            monkeypatch,
            [
                *_inputs(tmp_path, "openstack"),
                "--without",
                f"backport_only={ids}",
                "--out",
                str(tmp_path / "o.json"),
            ],
        )


def test_a_test_window_read_reports_its_sensitivities_through_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, members: None
) -> None:
    flagged = {"ai_assisted": ["x0", "x2"], "backport_only": []}
    _main(monkeypatch, _replication_argv(tmp_path, "test", ids=flagged))
    report = json.loads((tmp_path / "pilot.json").read_text())
    reduced = report["without"]["ai_assisted"]
    assert (reduced["listed"], reduced["removed"]) == (2, 2)
    assert {"p_one_sided", "bootstrap_se"} <= set(reduced)
    simulations = {"apache": _simulation(tmp_path)}
    gate = decomposition.replication_gate({"apache": report}, simulations=simulations)
    assert gate["cells"]["apache"]["verdict"] == "supported"
    assert gate["cells"]["apache"]["without"] == report["without"]


@pytest.mark.parametrize(
    "spec,message", [("ai_assisted", "give NAME=FILE"), ("=x.json", "give NAME=FILE")]
)
def test_a_malformed_sensitivity_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spec: str, message: str
) -> None:
    with pytest.raises(SystemExit, match=message):
        _main(
            monkeypatch,
            [*_inputs(tmp_path, "openstack"), "--without", spec, "--out", str(tmp_path / "o.json")],
        )


def test_a_sensitivity_named_twice_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _ids(tmp_path, ["x0"])
    twice = ["--without", f"ai_assisted={ids}", "--without", f"ai_assisted={ids}"]
    with pytest.raises(SystemExit, match="names 'ai_assisted' twice"):
        _main(
            monkeypatch,
            [*_inputs(tmp_path, "openstack"), *twice, "--out", str(tmp_path / "o.json")],
        )


def test_listed_ids_absent_from_the_runs_are_counted_not_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _ids(tmp_path, ["x0", "other-corpus-1", "other-corpus-2"])
    out = tmp_path / "o.json"
    _main(
        monkeypatch,
        [*_inputs(tmp_path, "openstack"), "--without", f"ai_assisted={ids}", "--out", str(out)],
    )
    reduced = json.loads(out.read_text())["without"]["ai_assisted"]
    assert (reduced["listed"], reduced["removed"]) == (3, 1)


def test_a_sensitivity_that_empties_a_half_is_reported_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _ids(tmp_path, [f"x{i}" for i in range(N)])
    out = tmp_path / "o.json"
    _main(
        monkeypatch,
        [*_inputs(tmp_path, "openstack"), "--without", f"ai_assisted={ids}", "--out", str(out)],
    )
    reduced = json.loads(out.read_text())["without"]["ai_assisted"]
    assert "unreadable" in reduced and reduced["listed"] == N


def test_a_listing_made_from_another_corpus_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _ids(tmp_path, ["x0"], universe=[f"x{i}" for i in range(N)])
    with pytest.raises(SystemExit, match=f"made from another corpus: {N} of the runs' examples"):
        _main(
            monkeypatch,
            [
                *_inputs(tmp_path, "openstack"),
                "--without",
                f"ai_assisted={ids}",
                "--out",
                str(tmp_path / "o.json"),
            ],
        )


def test_a_test_read_without_its_registered_sensitivities_is_refused_before_any_draw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, members: None
) -> None:
    with pytest.raises(SystemExit, match=r"needs --without for \['backport_only'\]"):
        _main(monkeypatch, _gerrit_test_argv(tmp_path, names=("ai_assisted",)))


def test_an_unregistered_sensitivity_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _ids(tmp_path, ["x0"])
    with pytest.raises(SystemExit, match="'tool_generated' is not a registered sensitivity"):
        _main(
            monkeypatch,
            [
                *_inputs(tmp_path, "openstack"),
                "--without",
                f"tool_generated={ids}",
                "--out",
                str(tmp_path / "o.json"),
            ],
        )


def test_a_test_read_on_another_metric_than_the_registered_one_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, members: None
) -> None:
    argv = _gerrit_test_argv(tmp_path)
    argv[argv.index("--metric") + 1] = "exact_match"
    with pytest.raises(SystemExit, match=r"\{'metric': 'exact_match'\} differ from the registered"):
        _main(monkeypatch, argv)
    assert not (tmp_path / "o.json").exists()


def test_a_test_read_takes_its_k_from_the_coverage_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, members: None
) -> None:
    """The coverage rule's K, with the pilot's spread targets its simulation is checked against."""
    argv = _gerrit_test_argv(tmp_path)
    coverage = {
        "kind": K_COVERAGE,
        "org": "openstack",
        "metric": REGISTERED_METRIC,
        "runs": len(ADMISSIBLE),
        "spread_targets": spread_targets(PILOT),
    }
    (tmp_path / "sizing.json").write_text(json.dumps(coverage))
    _main(monkeypatch, argv)
    report = json.loads((tmp_path / "o.json").read_text())
    assert report["k_source"] == f"{tmp_path / 'sizing.json'}: runs"
    assert report["k_from"]["runs"] == report["runs"] == len(ADMISSIBLE)
    assert report["k_from"]["spread_targets"] == spread_targets(PILOT)
    # A coverage artifact of another organization is no K for this one.
    (tmp_path / "sizing.json").write_text(json.dumps(coverage | {"org": "wikimedia"}))
    (tmp_path / "o.json").unlink()
    with pytest.raises(SystemExit, match="'wikimedia''s K, not openstack's"):
        _main(monkeypatch, argv)
