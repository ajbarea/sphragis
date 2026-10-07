"""Immutable raw snapshots and the records that make them replayable."""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from sphragis.corpus.storage import (
    read_snapshot,
    refused_snapshot,
    snapshot_path,
    unfinished_snapshot,
    write_snapshot,
)


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


def _stop_at_write(monkeypatch: pytest.MonkeyPatch, stop: int) -> None:
    """Make a snapshot's `stop`-th write stop, as a hard stop there would."""
    from sphragis.corpus import storage

    calls: list[Path] = []

    def stopping(real: Callable[..., None]) -> Callable[..., None]:
        def write(path: Path, *rest: object) -> None:
            calls.append(path)
            if len(calls) == stop:
                raise KeyboardInterrupt
            real(path, *rest)

        return write

    monkeypatch.setattr(storage, "write_atomic", stopping(storage.write_atomic))
    monkeypatch.setattr(storage, "write_json_atomic", stopping(storage.write_json_atomic))


@pytest.mark.parametrize("stop", [2, 3])
def test_a_stop_partway_leaves_a_new_month_unfinished_and_rewritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop: int
) -> None:
    with monkeypatch.context() as patched:
        _stop_at_write(patched, stop)
        with pytest.raises(KeyboardInterrupt):
            write_snapshot(tmp_path, "qt", "2024-11", [{"a": 1}], record={})
    path = snapshot_path(tmp_path, "qt", "2024-11")
    assert unfinished_snapshot(path)
    if path.exists():
        with pytest.raises(ValueError, match="did not finish"):
            read_snapshot(path)
    write_snapshot(tmp_path, "qt", "2024-11", [{"a": 1}], record={})
    assert not unfinished_snapshot(path) and read_snapshot(path) == [{"a": 1}]
    assert sorted(p.name for p in path.parent.iterdir()) == [
        "2024-11.ndjson.gz",
        "2024-11.record.json",
    ]


@pytest.mark.parametrize("stop", [2, 3])
def test_a_stop_partway_through_a_replacement_is_refused_until_refetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop: int
) -> None:
    # Old snapshot beside a new record (stop at 2) or new beside an open record (stop at 3):
    # either way no record vouches for a snapshot it was not written with.
    path = write_snapshot(tmp_path, "qt", "2024-11", [{"a": 1}], record={"rules": "old"})
    with monkeypatch.context() as patched:
        _stop_at_write(patched, stop)
        with pytest.raises(KeyboardInterrupt):
            write_snapshot(
                tmp_path, "qt", "2024-11", [{"a": 2}], record={"rules": "new"}, overwrite=True
            )
    assert unfinished_snapshot(path)
    with pytest.raises(ValueError, match="did not finish"):
        read_snapshot(path)


def test_a_record_from_before_the_marker_reads_as_finished(tmp_path: Path) -> None:
    path = write_snapshot(tmp_path, "qt", "2024-11", [{"a": 1}], record={})
    record_path = path.with_suffix("").with_suffix(".record.json")
    record = json.loads(record_path.read_text())
    del record["complete"]
    record_path.write_text(json.dumps(record))
    assert not unfinished_snapshot(path) and read_snapshot(path) == [{"a": 1}]


def test_an_unreadable_record_never_licenses_replacing_a_snapshot(tmp_path: Path) -> None:
    # Only a record that says its write did not finish lets a rerun replace the snapshot;
    # a record a hard stop zeroed says nothing, so the snapshot stays immutable.
    path = write_snapshot(tmp_path, "qt", "2024-11", [{"a": 1}], record={})
    path.with_suffix("").with_suffix(".record.json").write_bytes(b"\0" * 64)
    assert not unfinished_snapshot(path)
    with pytest.raises(FileExistsError, match="immutable"):
        write_snapshot(tmp_path, "qt", "2024-11", [{"a": 999}], record={})
    assert read_snapshot(path) == [{"a": 1}]


def test_an_empty_snapshot_is_refused_not_read_as_zero_rows(tmp_path: Path) -> None:
    path = snapshot_path(tmp_path, "qt", "2024-11")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"")
    assert refused_snapshot(path)
    with pytest.raises(ValueError, match="never landed"):
        read_snapshot(path)
    assert read_snapshot(write_snapshot(tmp_path, "qt", "2024-12", [], record={})) == []


def test_every_raw_reader_refuses_an_unfinished_month(tmp_path: Path) -> None:
    from sphragis.corpus.backports import raw_changes

    path = write_snapshot(tmp_path, "qt", "2024-11", [{"a": 1}], record={})
    record_path = path.with_suffix("").with_suffix(".record.json")
    record_path.write_text(json.dumps({**json.loads(record_path.read_text()), "complete": False}))
    with pytest.raises(ValueError, match="did not finish"):
        list(raw_changes(tmp_path, "qt"))


def test_a_zeroed_snapshot_from_before_the_marker_is_refused(tmp_path: Path) -> None:
    # Written before atomic writes, its record has no `complete` key, and a hard stop left
    # zeros where its data should be; nothing about its record gives that away.
    path = write_snapshot(tmp_path, "qt", "2024-11", [{"a": 1}], record={})
    record_path = path.with_suffix("").with_suffix(".record.json")
    record = json.loads(record_path.read_text())
    del record["complete"]
    record_path.write_text(json.dumps(record))
    path.write_bytes(b"\0" * path.stat().st_size)
    assert "not a gzip stream" in (refused_snapshot(path) or "")


def test_only_the_whole_check_finds_a_zeroed_body_and_every_reader_names_it(
    tmp_path: Path,
) -> None:
    path = write_snapshot(
        tmp_path, "qt", "2024-11", [{"n": i, "pad": "x" * 40} for i in range(3000)], record={}
    )
    data = path.read_bytes()
    path.write_bytes(data[:20] + b"\0" * (len(data) - 20))
    assert refused_snapshot(path) is None, "the quick check reads the header only"
    assert "unreadable" in (refused_snapshot(path, whole=True) or "")
    with pytest.raises(ValueError, match=f"{path.name}: unreadable"):
        read_snapshot(path)
    assert (
        refused_snapshot(write_snapshot(tmp_path, "qt", "2024-12", [], record={}), whole=True)
        is None
    )


def test_a_disk_error_is_not_taken_for_a_damaged_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A refused snapshot is refetched with --overwrite, so only damage to the file itself may
    # refuse it; an I/O error says nothing about the file and must surface as itself.
    import errno
    import gzip

    path = write_snapshot(tmp_path, "qt", "2024-11", [{"a": 1}], record={})

    def failing_read(self: object, *_: object) -> bytes:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(gzip.GzipFile, "read", failing_read)
    with pytest.raises(OSError, match="Input/output"):
        refused_snapshot(path, whole=True)
