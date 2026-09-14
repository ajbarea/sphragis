"""Immutable raw snapshots and the records that make them replayable."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from sphragis.corpus.storage import read_snapshot, snapshot_path, write_snapshot


def test_snapshot_path_is_org_and_month_scoped(tmp_path: Path) -> None:
    p = snapshot_path(tmp_path, "openstack", "2024-10")
    assert p == tmp_path / "openstack" / "raw" / "2024-10.ndjson.gz"


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    rows = [{"_number": 1, "created": "2024-10-02"}, {"_number": 2, "created": "2024-10-03"}]
    path = write_snapshot(tmp_path, "openstack", "2024-10", rows, record={"query": "q"})
    assert path.exists()
    assert read_snapshot(path) == rows


def test_a_snapshot_is_gzipped_ndjson_one_object_per_line(tmp_path: Path) -> None:
    rows = [{"a": 1}, {"a": 2}, {"a": 3}]
    path = write_snapshot(tmp_path, "qt", "2024-11", rows, record={})
    lines = gzip.decompress(path.read_bytes()).decode().strip().split("\n")
    assert len(lines) == 3
    assert [json.loads(line)["a"] for line in lines] == [1, 2, 3]


def test_the_fetch_record_is_written_beside_the_snapshot(tmp_path: Path) -> None:
    path = write_snapshot(tmp_path, "qt", "2024-11", [{"a": 1}], record={"query": "status:merged"})
    record = json.loads(path.with_suffix("").with_suffix(".record.json").read_text())
    assert record["query"] == "status:merged"
    assert record["rows"] == 1
    assert record["git"]["commit"], "the record carries provenance"


def test_writing_twice_refuses_rather_than_overwriting(tmp_path: Path) -> None:
    # Raw snapshots are immutable: experiments replay from them, so a silent overwrite
    # would change history under results that already cite it.
    write_snapshot(tmp_path, "qt", "2024-11", [{"a": 1}], record={})
    with pytest.raises(FileExistsError, match="immutable"):
        write_snapshot(tmp_path, "qt", "2024-11", [{"a": 2}], record={})


def test_overwrite_is_possible_only_when_asked_for_explicitly(tmp_path: Path) -> None:
    write_snapshot(tmp_path, "qt", "2024-11", [{"a": 1}], record={})
    path = write_snapshot(tmp_path, "qt", "2024-11", [{"a": 2}], record={}, overwrite=True)
    assert read_snapshot(path) == [{"a": 2}]
