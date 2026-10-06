"""Bounds on examples that may come from a backport of code older than the corpus."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("backports", ROOT / "scripts" / "backport_share.py")
assert _spec is not None and _spec.loader is not None
script = importlib.util.module_from_spec(_spec)
sys.argv = ["backport_share"]
_spec.loader.exec_module(script)


def test_an_example_is_only_backport_when_every_change_with_its_id_is() -> None:
    branches = {
        ("p", "I1"): {"stable/2024.2"},
        ("p", "I2"): {"master", "stable/2024.2"},
        ("p", "I3"): {"master"},
        ("q", "I4"): {"REL1_43", "wmf/1.44.0-wmf.16"},
    }
    rows = [{"project": p, "change_id": c} for p, c in branches] + [
        {"project": "p", "change_id": "missing"}
    ]
    out = script.bounds({"dev": rows}, branches)["dev"]
    assert out == {"examples": 5, "only_backport": 2, "possibly_backport": 3, "no_raw_change": 1}


def test_development_branches_are_not_backports() -> None:
    for name in ("master", "main", "production", "feature/mpu", "f/caracal", "stablekit"):
        assert not script.BACKPORT.match(name), name
    for name in (
        "stable/2025.1",
        "stable/release-3",
        "stable",
        "unmaintained/2023.1",
        "bugfix/12",
        "release_9",
        "r/stx.10.0",
        "REL1_43",
        "fundraising/REL1_43",
        "wmf/1.45.0-wmf.21",
        "deploy/wmf/stable-3.10",
        "wmf_deploy",
        "deployment",
    ):
        assert script.BACKPORT.match(name), name


def test_the_ai_flag_reads_project_and_change_id_from_the_trailer_artifact() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sensitivity_ids", Path(__file__).resolve().parents[2] / "scripts" / "sensitivity_ids.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    trailers = {"orgs": {"openstack": {"changes_ai": ["openstack/nova I1", "x/y z I2"]}}}
    assert module.flagged_changes(trailers, "openstack") == {
        ("openstack/nova", "I1"),
        ("x/y z", "I2"),
    }
