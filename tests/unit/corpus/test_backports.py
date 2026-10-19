"""The backport rule the bounds and the registered sensitivity share."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from sphragis.corpus.backports import (
    branches_by_change,
    merge_up_projects,
    only_backport,
    possibly_backport,
)


def _raw(root: Path, org: str, changes: list[dict]) -> None:
    raw = root / org / "raw"
    raw.mkdir(parents=True)
    with gzip.open(raw / "2025-01.ndjson.gz", "wt") as out:
        out.writelines(json.dumps(c) + "\n" for c in changes)


def test_a_change_is_only_a_backport_when_every_branch_it_sits_on_is_one(tmp_path: Path) -> None:
    _raw(
        tmp_path,
        "openstack",
        [
            {"project": "p", "change_id": "I1", "branch": "stable/2025.1"},
            {"project": "p", "change_id": "I1", "branch": "unmaintained/zed"},
            {"project": "p", "change_id": "I2", "branch": "stable/2025.1"},
            {"project": "p", "change_id": "I2", "branch": "master"},
            {"project": "q", "change_id": "I1", "branch": "master"},
        ],
    )
    branches = branches_by_change(tmp_path, "openstack")
    assert only_backport(branches[("p", "I1")]) and possibly_backport(branches[("p", "I1")])
    assert not only_backport(branches[("p", "I2")]) and possibly_backport(branches[("p", "I2")])
    # The same Change-Id in another project is another change.
    assert not possibly_backport(branches[("q", "I1")])
    assert not only_backport(set())


def test_release_maintenance_and_deployment_branches_are_backports() -> None:
    for name in (
        "master",
        "main",
        "production",
        "feature/mpu",
        "f/caracal",
        "stablekit",
        # Qt's development lines and upstream-tracking lines.
        "dev",
        "qds/dev",
        "134-based-refactor",
        "upstream-master",
        # The lines Qt WebEngine's Chromium fork develops on.
        "130-based",
        "122-based",
        "v3.8-based",
        "release_21.1.0-based",
        "refs/meta/config",
        "wip/figmastyle",
        "6",
    ):
        assert not possibly_backport({name}), name
    for name in (
        "stable/2025.1",
        "stable/release-3",
        "stable",
        "unmaintained/2023.1",
        "bugfix/12",
        "release_9",
        "release_1.0.0",
        "r/stx.10.0",
        "REL1_43",
        "fundraising/REL1_43",
        "wmf/1.45.0-wmf.21",
        "deploy/wmf/stable-3.10",
        "wmf_deploy",
        "deployment",
        "6.8",
        "6.8.1",
        "15.0",
        "lts-6.5",
        "lts-6.8.4",
        "qds/4.7",
        "qds/4.8.0",
        "qds-4.6",
        "kirkstone",
        "scarthgap",
        "styhead",
        "walnascar",
        "whinlatter",
        "wrynose",
        "dunfell",
        "2024.1",
    ):
        assert only_backport({name}), name


def test_a_project_that_merges_its_release_lines_up_is_read_as_merge_up(tmp_path: Path) -> None:
    """Qt Creator lands a change on its release line and merges the line up into master; a merge
    down into deployment, or into a feature branch, is not that model."""
    up = "Merge remote-tracking branch 'origin/18.0'"
    _raw(
        tmp_path,
        "qt",
        [
            {"project": "creator", "change_id": "I1", "branch": "master", "subject": up},
            {"project": "creator", "change_id": "I2", "branch": "18.0", "subject": "Fix a"},
            {"project": "fund", "change_id": "I3", "branch": "deployment",
             "subject": "Merge branch 'master' into deployment"},
            {"project": "swift", "change_id": "I4", "branch": "feature/mpu",
             "subject": "Merge remote-tracking branch 'origin/master' into feature/mpu"},
            {"project": "base", "change_id": "I5", "branch": "6.9",
             "subject": "Merge remote-tracking branch 'origin/6.8'"},
            {"project": "qbs", "change_id": "I6", "branch": "master",
             "subject": "Merge 2.5 into master"},
        ],
    )  # fmt: skip
    assert merge_up_projects(tmp_path, "qt") == frozenset({"creator", "qbs"})


def test_a_registered_set_is_read_whatever_a_later_window_shows() -> None:
    from sphragis.corpus.backports import MERGE_UP, registered_merge_up

    later = frozenset({"qt/qtbase"})
    assert registered_merge_up("qt", later) == MERGE_UP["qt"]
    assert registered_merge_up("openstack", later) == frozenset()
    assert registered_merge_up("apache", later) == later


def test_in_a_merge_up_project_a_release_change_is_the_original() -> None:
    assert only_backport({"18.0"}) and not only_backport({"18.0"}, merge_up=True)
    assert not possibly_backport({"18.0"}, merge_up=True)
    # Picked between lines under one Change-Id, it may still be a backport.
    assert possibly_backport({"18.0", "17.0"}, merge_up=True)
    assert not only_backport({"18.0", "17.0"}, merge_up=True)
