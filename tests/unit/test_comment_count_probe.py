"""The comment-count probe asks each change once, skips refused months, and records failures."""

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


def _corpus(tmp_path: Path) -> Path:
    write_snapshot(
        tmp_path,
        "qt",
        "2024-10",
        [{"_number": n, "total_comment_count": 0} for n in range(1, 5)]
        + [{"_number": 9, "total_comment_count": 0}, {"_number": 20, "total_comment_count": 3}],
        record={},
    )
    # Change 9 again a month later, now with comments: it counts once, under its latest row.
    write_snapshot(
        tmp_path,
        "qt",
        "2024-11",
        [{"_number": n, "total_comment_count": 0} for n in range(5, 9)]
        + [{"_number": 9, "total_comment_count": 2}, {"_number": 21, "total_comment_count": 1}],
        record={},
    )
    return tmp_path


def test_each_change_counts_once_under_its_latest_month(tmp_path) -> None:
    by_month, refused = probe.recorded(_corpus(tmp_path), "qt")
    assert refused == []
    assert by_month["2024-10"] == ([1, 2, 3, 4], [20])
    assert by_month["2024-11"] == ([5, 6, 7, 8], [9, 21])


def _run(tmp_path, monkeypatch, answer) -> dict:
    root = _corpus(tmp_path)
    monkeypatch.setattr(probe, "fetch_comments", lambda base, n, transport: answer(n))
    monkeypatch.setattr(probe, "http_transport", lambda: None)
    out = tmp_path / "out.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "probe",
            "--org",
            "qt",
            "--root",
            str(root),
            "--zero",
            "2",
            "--control",
            "1",
            "--out",
            str(out),
        ],
    )
    probe.main()
    return json.loads(out.read_text())


def test_the_probe_holds_when_zeros_are_empty_and_controls_are_not(tmp_path, monkeypatch) -> None:
    read = _run(tmp_path, monkeypatch, lambda n: {"f": [{}] * (n >= 9)})
    assert read["holds"] is True
    assert read["upper_95_share_with_comments"] == pytest.approx(1 - 0.05 ** (1 / 4))


def test_a_failed_request_is_recorded_and_the_probe_does_not_hold(tmp_path, monkeypatch) -> None:
    def answer(n):
        if n in (1, 2, 3, 4, 5, 6, 7, 8):
            raise RuntimeError("404")
        return {"f": [{}]}

    read = _run(tmp_path, monkeypatch, answer)
    assert read["holds"] is False and len(read["failed"]) == 4
    assert all("RuntimeError" in a["error"] for a in read["answers"]["zero"].values())


def test_a_control_without_comments_fails_the_probe(tmp_path, monkeypatch) -> None:
    read = _run(tmp_path, monkeypatch, lambda n: {})
    assert read["holds"] is False and read["control_without_comments"]
