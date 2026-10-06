"""The backport rule the bounds and the registered sensitivity share."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from sphragis.corpus.backports import branches_by_change, only_backport, possibly_backport


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


def test_mediawiki_release_and_deployment_branches_are_backports() -> None:
    for name in (
        "REL1_43",
        "fundraising/REL1_42",
        "wmf/1.45.0-wmf.3",
        "deploy/wmf/x",
        "wmf_deploy",
    ):
        assert only_backport({name}), name
    for name in ("master", "main", "feature/x", "f/y"):
        assert not possibly_backport({name}), name
