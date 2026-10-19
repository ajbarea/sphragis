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
    monkeypatch.setattr(probe, "http_transport", lambda **_: None)
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


def test_a_month_its_hosts_grant_does_not_cover_gives_no_change_to_ask(
    tmp_path, monkeypatch
) -> None:
    """The probe asks by number, as the build does, so it refuses what the build refuses."""
    from sphragis.corpus import cli

    host = "chromium-review.googlesource.com"
    scope = cli.GrantScope(("platform/a",), "2024-10-01", "2025-03-27", None)
    monkeypatch.setitem(cli.REST_PERMITTED, host, cli.REST_PERMITTED[host]._replace(scope=scope))
    inside = "2024-11-05 00:00:00.000000000"
    rows = {
        "2024-10": [{"_number": 1, "project": "platform/a", "created": "2024-09-20 00:00:00.000"}],
        "2024-11": [
            {"_number": 2, "project": "platform/a", "created": inside, "total_comment_count": 2},
            {"_number": 3, "project": "platform/a", "created": inside, "total_comment_count": 0},
        ],
        "2024-12": [{"_number": 4, "project": "platform/z", "created": "2024-12-01 00:00:00.000"}],
        "2025-04": [{"_number": 5, "project": "platform/a", "created": inside}],
    }
    for month, changes in rows.items():
        write_snapshot(tmp_path, "chromium", month, changes, record={})
    by_month, skipped = probe.recorded(tmp_path, "chromium")
    assert list(by_month) == ["2024-11"] and by_month["2024-11"] == ([3], [2])
    assert skipped["2024-10"] == "1 rows created outside the host's grant"
    assert skipped["2024-12"] == "rows of projects outside the host's grant"
    assert skipped["2025-04"] == "outside the host's grant"


def test_a_probe_with_nothing_inside_the_grant_asks_nothing(tmp_path, monkeypatch) -> None:
    from sphragis.corpus import cli

    host = "chromium-review.googlesource.com"
    scope = cli.GrantScope(("platform/a",), "2024-10-01", "2025-03-27", None)
    monkeypatch.setitem(cli.REST_PERMITTED, host, cli.REST_PERMITTED[host]._replace(scope=scope))
    held = [{"_number": 1, "project": "platform/art", "created": "2024-01-02 00:00:00.000"}]
    write_snapshot(tmp_path, "chromium", "2024-01", held, record={})
    monkeypatch.setattr(probe, "http_transport", lambda **_: pytest.fail("no request"))
    argv = ["probe", "--org", "chromium", "--root", str(tmp_path), "--zero", "5", "--control", "1"]
    monkeypatch.setattr(sys, "argv", [*argv, "--out", str(tmp_path / "out.json")])
    with pytest.raises(SystemExit, match="no control asked"):
        probe.main()
