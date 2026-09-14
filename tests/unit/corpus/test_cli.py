"""Stage dispatch."""

from __future__ import annotations

import pytest

from sphragis.corpus.cli import STAGES, build_parser, main


def test_every_stage_is_accepted() -> None:
    parser = build_parser()
    for stage in STAGES:
        assert parser.parse_args([stage]).stage == stage


def test_main_exits_for_an_unknown_stage() -> None:
    with pytest.raises(SystemExit):
        main(["nonsense"])


def test_main_reports_a_stage_with_no_body_yet() -> None:
    assert main(["fetch"]) == 1


def test_defaults_are_set() -> None:
    args = build_parser().parse_args(["fetch"])
    assert args.org == "openstack"
    assert args.window == "pilot"
