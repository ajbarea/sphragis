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


def test_the_committed_record_states_a_decision() -> None:
    record = Path(__file__).resolve().parents[3] / "corpus" / "HSRO.md"
    assert record.is_file(), f"the review record should be committed at {record}"
    assert hsro_determination(record) is not None, (
        "the record must state a determination date or DEFERRED, never nothing"
    )


def test_a_deferral_satisfies_the_gate(tmp_path: Path) -> None:
    record = tmp_path / "HSRO.md"
    record.write_text("**Status:** Determination: DEFERRED (2026-09-14, AJ)\n")
    assert hsro_determination(record) == "DEFERRED"
    assert require_hsro(record) == "DEFERRED"


def test_pending_is_not_a_decision_and_still_blocks(tmp_path: Path) -> None:
    record = tmp_path / "HSRO.md"
    record.write_text("**Status:** Determination: PENDING\n")
    with pytest.raises(SystemExit):
        require_hsro(record)


def test_main_exits_for_an_unknown_stage() -> None:
    with pytest.raises(SystemExit):
        main(["nonsense"])


def test_main_reports_a_stage_with_no_body_yet(tmp_path: Path) -> None:
    record = tmp_path / "HSRO.md"
    record.write_text("Determination: 2026-10-01\n")
    assert main(["fetch", "--hsro", str(record)]) == 1
