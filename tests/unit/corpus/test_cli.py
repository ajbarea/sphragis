"""The CLI's gates: HSRO before fetch, seal before the test window."""

from __future__ import annotations

from pathlib import Path

import pytest

from sphragis.corpus.cli import hsro_determination, main, require_hsro


def test_hsro_determination_reads_the_recorded_date(tmp_path: Path) -> None:
    record = tmp_path / "HSRO.md"
    record.write_text("# HSRO\n\nDetermination: 2026-10-01\nNot human subjects research.\n")
    assert hsro_determination(record) == "2026-10-01"


def test_hsro_determination_is_none_when_the_file_is_missing(tmp_path: Path) -> None:
    assert hsro_determination(tmp_path / "HSRO.md") is None


def test_hsro_determination_is_none_when_the_file_has_no_date(tmp_path: Path) -> None:
    record = tmp_path / "HSRO.md"
    record.write_text("# HSRO\n\nStatus: Determination: PENDING\n")
    assert hsro_determination(record) is None


def test_require_hsro_exits_when_there_is_no_determination(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        require_hsro(tmp_path / "HSRO.md")
    assert "HSRO" in str(excinfo.value)


def test_fetch_refuses_to_run_without_a_determination(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["fetch", "--org", "openstack", "--hsro", str(tmp_path / "HSRO.md")])


def test_the_committed_hsro_draft_keeps_the_fetch_gate_locked() -> None:
    draft = Path(__file__).resolve().parents[3] / "corpus" / "HSRO.md"
    assert draft.is_file(), f"the HSRO draft should be committed at {draft}"
    assert hsro_determination(draft) is None


def test_main_exits_for_an_unknown_stage() -> None:
    with pytest.raises(SystemExit):
        main(["nonsense"])


def test_main_reports_a_stage_with_no_body_yet(tmp_path: Path) -> None:
    record = tmp_path / "HSRO.md"
    record.write_text("Determination: 2026-10-01\n")
    assert main(["fetch", "--hsro", str(record)]) == 1
