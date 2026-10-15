"""The comment-count probe samples the rows the build skips, in proportion, and keeps answers."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from sphragis.corpus.storage import write_snapshot

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("probe", ROOT / "scripts" / "comment_count_probe.py")
assert _spec is not None and _spec.loader is not None
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


def _rows(zero: range, some: list[int]) -> list[dict]:
    return [{"_number": n, "total_comment_count": 0} for n in zero] + [
        {"_number": n, "total_comment_count": 2} for n in some
    ]


def _corpus(tmp_path: Path) -> Path:
    if (tmp_path / "qt" / "raw" / "2024-10.ndjson.gz").exists():
        return tmp_path
    write_snapshot(tmp_path, "qt", "2024-10", _rows(range(1, 13), [90]), record={})
    # Change 9 recorded at zero in 2024-10 and with comments a month later: its zero row is skipped.
    write_snapshot(tmp_path, "qt", "2024-11", _rows(range(13, 17), [9, 91]), record={})
    return tmp_path


def test_every_zero_row_the_build_skips_is_in_the_frame(tmp_path) -> None:
    by_month, skipped = probe.recorded(_corpus(tmp_path), "qt")
    assert skipped == {}
    assert 9 in by_month["2024-10"][0] and 9 in by_month["2024-11"][1]


def test_zero_rows_are_drawn_in_proportion_to_each_month() -> None:
    assert probe.proportional(8, {"a": 12, "b": 4}) == {"a": 6, "b": 2}
    assert probe.proportional(5, {"a": 3, "b": 0}) == {"a": 3, "b": 0}


def _run(tmp_path, monkeypatch, answer, *extra) -> dict:
    root = _corpus(tmp_path)
    monkeypatch.setattr(probe, "fetch_comments", lambda base, n, transport: answer(n))
    monkeypatch.setattr(probe, "http_transport", lambda: None)
    out = tmp_path / "out.json"
    argv = ["probe", "--org", "qt", "--root", str(root), "--zero", "8", "--control", "1"]
    monkeypatch.setattr(sys, "argv", [*argv, "--out", str(out), *extra])
    probe.main()
    return json.loads(out.read_text())


def test_the_probe_holds_when_zero_rows_are_empty_and_controls_are_not(
    tmp_path, monkeypatch
) -> None:
    read = _run(tmp_path, monkeypatch, lambda n: {"f": [{}]} if n in (9, 90, 91) else {})
    assert read["holds"] is True and read["answered_zero"] == 8
    assert read["upper_95_share_with_comments"] == pytest.approx(1 - 0.05 ** (1 / 8))
    assert read["raw_digest"]
    assert not (tmp_path / "out.json.partial.jsonl").exists()


def test_a_zero_row_with_comments_fails_it_and_gives_no_bound(tmp_path, monkeypatch) -> None:
    read = _run(tmp_path, monkeypatch, lambda n: {"f": [{}]})
    assert read["holds"] is False and read["upper_95_share_with_comments"] is None


def test_a_failed_request_is_recorded_and_fails_it(tmp_path, monkeypatch) -> None:
    def answer(n):
        if n < 90:
            raise RuntimeError("404")
        return {"f": [{}]}

    read = _run(tmp_path, monkeypatch, answer)
    assert read["holds"] is False and read["failed"]


def test_an_interrupted_probe_resumes_from_its_partial(tmp_path, monkeypatch) -> None:
    asked: list[int] = []

    def answer(n):
        asked.append(n)
        if len(asked) == 3:
            raise KeyboardInterrupt
        return {"f": [{}]} if n in (9, 90, 91) else {}

    with pytest.raises(KeyboardInterrupt):
        _run(tmp_path, monkeypatch, answer)
    kept = (tmp_path / "out.json.partial.jsonl").read_text().splitlines()
    assert len(kept) == 2
    asked.clear()
    read = _run(
        tmp_path,
        monkeypatch,
        lambda n: asked.append(n) or ({"f": [{}]} if n in (9, 90, 91) else {}),
    )
    assert read["holds"] is True and len(asked) == 10 - 2


def test_a_month_whose_body_is_damaged_is_skipped(tmp_path) -> None:
    root = _corpus(tmp_path)
    snapshot = root / "qt" / "raw" / "2024-11.ndjson.gz"
    data = snapshot.read_bytes()
    snapshot.write_bytes(data[: len(data) // 2] + b"\0" * (len(data) - len(data) // 2))
    by_month, skipped = probe.recorded(root, "qt")
    assert "2024-11" in skipped and "2024-11" not in by_month
