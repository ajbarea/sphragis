"""Stage dispatch and the configuration the stages need."""

from __future__ import annotations

from pathlib import Path

import pytest

from sphragis.corpus.cli import GERRIT, STAGES, build_parser, main, require_salt


def test_every_stage_is_accepted() -> None:
    parser = build_parser()
    for stage in STAGES:
        assert parser.parse_args([stage]).stage == stage


def test_main_exits_for_an_unknown_stage() -> None:
    with pytest.raises(SystemExit):
        main(["nonsense"])


def test_both_organizations_have_a_gerrit_instance() -> None:
    assert GERRIT["openstack"] == "https://review.opendev.org"
    assert GERRIT["qt"] == "https://codereview.qt-project.org"


def test_org_choices_come_from_the_instance_table() -> None:
    # A typo in --org should fail at parse time rather than fetch from nowhere.
    with pytest.raises(SystemExit):
        build_parser().parse_args(["fetch", "--org", "openstak"])


def test_require_salt_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "s3cret")
    assert require_salt() == "s3cret"


def test_require_salt_exits_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without a salt the pseudonyms would not be stable across runs, so a corpus built
    # today would not compare with one built tomorrow.
    monkeypatch.delenv("SPHRAGIS_CORPUS_SALT", raising=False)
    with pytest.raises(SystemExit, match="SPHRAGIS_CORPUS_SALT"):
        require_salt()


def test_require_salt_rejects_an_empty_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "   ")
    with pytest.raises(SystemExit, match="SPHRAGIS_CORPUS_SALT"):
        require_salt()


def test_defaults_are_set() -> None:
    args = build_parser().parse_args(["fetch"])
    assert args.org == "openstack"
    assert args.root == Path("datasets/gerrit")
