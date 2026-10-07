import json
import os
import socket
import subprocess
from pathlib import Path

import pytest

from sphragis.durable import (
    append_record,
    exclusive,
    open_log,
    read_json_object,
    read_records,
    write_atomic,
    write_json_atomic,
)

WHOLE = b'{"key": "a#1", "n": 1}\n{"key": "a#2", "n": 2}\n'


def _log(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "2025-01.partial.jsonl"
    path.write_bytes(data)
    return path


def test_a_whole_log_reads_back_untouched(tmp_path: Path) -> None:
    path = _log(tmp_path, WHOLE)
    assert [r["n"] for r in read_records(path)] == [1, 2]
    assert path.read_bytes() == WHOLE


@pytest.mark.parametrize(
    "tail",
    [
        b"\0" * 1539,  # the file grew but its data never landed (ext4 after a hard stop)
        b'{"key": "a#3", "n"',  # an append cut off partway
        b'{"key": "a#3", "n": 3}',  # a whole record whose newline never landed
        b'{"key": "a#3"' + b"\0" * 40,  # cut off, then zeros
        b"\0" * 40 + b"\n",  # zeros ending in a newline, nothing whole after them
    ],
)
def test_a_torn_tail_is_cut_off_and_reported(
    tmp_path: Path, tail: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _log(tmp_path, WHOLE + tail)
    assert [r["n"] for r in read_records(path)] == [1, 2]
    assert path.read_bytes() == WHOLE, "the next append must start a clean line"
    assert f", {len(tail)} bytes;" in capsys.readouterr().err


def test_appending_after_a_cut_leaves_a_readable_log(tmp_path: Path) -> None:
    path = _log(tmp_path, WHOLE + b"\0" * 64)
    read_records(path)
    with path.open("a", encoding="utf-8") as handle:
        append_record(handle, {"key": "a#3", "n": 3})
    assert [r["n"] for r in read_records(path)] == [1, 2, 3]


def test_zeros_inside_a_log_written_without_fsync_are_dropped(tmp_path: Path) -> None:
    # ext4 can write a later block back before an earlier one, so a log appended without
    # fsync can hold zeros with whole records after them; only the torn record is lost.
    line3 = b'{"key": "a#3", "n": 3}\n'
    path = _log(tmp_path, WHOLE + b"\0" * 16 + b'"n": 9}\n' + line3)
    assert [r["n"] for r in read_records(path)] == [1, 2, 3]
    assert path.read_bytes() == WHOLE + line3


def test_blank_lines_and_an_empty_log_read_as_before(tmp_path: Path) -> None:
    assert read_records(_log(tmp_path, b"")) == []
    path = _log(tmp_path, b'{"n": 1}\n\n{"n": 2}\n')
    assert [r["n"] for r in read_records(path)] == [1, 2]


def test_an_append_is_one_json_line(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        append_record(handle, {"key": "a#1", "text": "ü\nnext"})
    assert path.read_text(encoding="utf-8").count("\n") == 1
    assert json.loads(path.read_text(encoding="utf-8")) == {"key": "a#1", "text": "ü\nnext"}


def test_an_atomic_write_replaces_whole_and_leaves_no_staging(tmp_path: Path) -> None:
    path = tmp_path / "2025-01.ndjson.gz"
    path.write_bytes(b"old")
    write_atomic(path, b"new")
    assert path.read_bytes() == b"new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2025-01.ndjson.gz"]


def test_a_new_log_and_its_directory_are_made(tmp_path: Path) -> None:
    path = tmp_path / "apache" / "raw" / "2025-01.partial.jsonl"
    with open_log(path) as handle:
        append_record(handle, {"key": "a#1"})
    assert read_records(path) == [{"key": "a#1"}]


def test_a_failed_atomic_write_keeps_the_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "2025-01.ndjson.gz"
    path.write_bytes(b"old")

    def crash(*_: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr("sphragis.durable.os.replace", crash)
    with pytest.raises(OSError):
        write_atomic(path, b"new")
    assert path.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2025-01.ndjson.gz"]


def test_an_atomic_write_keeps_a_replaced_files_mode_and_gives_a_new_one_the_umasks(
    tmp_path: Path,
) -> None:
    kept = tmp_path / "kept.json"
    kept.write_bytes(b"old")
    kept.chmod(0o640)
    write_atomic(kept, b"new")
    assert kept.stat().st_mode & 0o777 == 0o640
    umask = os.umask(0)
    os.umask(umask)
    fresh = tmp_path / "fresh.json"
    write_atomic(fresh, b"new")
    assert fresh.stat().st_mode & 0o777 == 0o666 & ~umask


def test_staging_a_stopped_process_left_is_swept_and_others_kept(tmp_path: Path) -> None:
    path = tmp_path / "2025-01.ndjson.gz"
    dead = subprocess.Popen(["true"])
    dead.wait()
    host = socket.gethostname()
    stale = tmp_path / f".{path.name}.{host}.{dead.pid}.tmp"
    live = tmp_path / f".{path.name}.{host}.{os.getppid()}.tmp"
    elsewhere = tmp_path / f".{path.name}.another-node.{dead.pid}.tmp"
    for staging in (stale, live, elsewhere):
        staging.write_bytes(b"left behind")
    write_atomic(path, b"new")
    assert not stale.exists(), "a dead process on this host left it"
    assert live.exists(), "a live writer's staging is not ours to remove"
    assert elsewhere.exists(), "another host's process cannot be seen from here"
    assert path.read_bytes() == b"new"


def test_a_second_holder_is_refused_while_the_first_holds(tmp_path: Path) -> None:
    path = tmp_path / ".locks" / "apache" / "2025-01.lock"
    with exclusive(path), pytest.raises(SystemExit, match="held by another"), exclusive(path):
        pass
    with exclusive(path):
        pass


def test_a_json_record_is_written_whole_and_read_back(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    write_json_atomic(path, {"b": 1, "a": 2})
    assert path.read_text().endswith("}\n") and read_json_object(path) == {"b": 1, "a": 2}
    for bad in (b"", b"\0" * 8, b"[1, 2]"):
        path.write_bytes(bad)
        assert read_json_object(path) is None
    assert read_json_object(tmp_path / "missing.json") is None
