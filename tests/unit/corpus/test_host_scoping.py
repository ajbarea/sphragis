"""The scoping script is a second path to a live host, so it carries fetch's seal."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "host_scoping.py"
_spec = importlib.util.spec_from_file_location("host_scoping", _SCRIPT)
assert _spec and _spec.loader
scoping = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scoping)


def _offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    asked: list[str] = []

    def transport(url: str) -> tuple[int, dict[str, str], str]:
        asked.append(url)
        return 200, {}, ")]}'\n[]"

    monkeypatch.chdir(tmp_path)  # no seal under datasets/gerrit/chromium: locked
    monkeypatch.setattr(scoping, "http_transport", lambda **_: transport)
    return asked


@pytest.mark.parametrize(
    "argv",
    [
        ["measure", "yield", "v8/v8", "2025-12", "5"],
        ["measure", "volume", "v8/v8", "2025-11"],
        ["measure", "ranged", "chromium/src", "2026-09"],
        # A sealed month anywhere in the list stops it before the first month is asked.
        ["measure", "volume", "v8/v8", "2025-10,2025-11"],
    ],
)
def test_measure_refuses_the_sealed_window_before_any_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, argv: list[str]
) -> None:
    asked = _offline(monkeypatch, tmp_path)
    with pytest.raises(SystemExit, match="sealed test window"):
        scoping.main(argv)
    assert asked == []


def test_measure_allows_the_grants_last_month(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    asked = _offline(monkeypatch, tmp_path)
    scoping.main(["measure", "volume", "v8/v8", "2025-08"])
    assert asked and '"merged": 0' in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["volume", "ranged", "yield"])
def test_a_month_outside_the_grant_is_refused_before_any_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    """Past V8's grant (changes created before 2025-09-01) and before the sealed window."""
    asked = _offline(monkeypatch, tmp_path)
    with pytest.raises(SystemExit, match="2025-09: outside the host's grant"):
        scoping.main(["measure", kind, "v8/v8", "2025-09", "1"])
    assert asked == []


def _change(number: int, created: str) -> dict:
    return {
        "_number": number,
        "created": created,
        "project": "v8/v8",
        "owner": {"_account_id": 1, "email": "dev@example.org"},
        "current_revision": "r",
        "revisions": {"r": {"_number": 1, "files": {}}},
    }


def test_only_changes_created_inside_the_grant_are_asked_about(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [_change(1, "2025-08-20 00:00:00.000000000"), _change(2, "2025-09-02 00:00:00.000")]
    monkeypatch.setattr(scoping, "_transport", lambda *_: None)
    monkeypatch.setattr(scoping, "listing", lambda *_, **__: (rows, False))
    asked: list[str] = []
    monkeypatch.setattr(scoping, "get", lambda url, _: asked.append(url) or {})
    scoping.measure_yield("v8/v8", "2025-08", 2)
    assert asked == [f"{scoping.BASE}/changes/1/comments"]
    assert '"sampled": 1' in capsys.readouterr().out


def test_summarize_refuses_a_ranged_count_that_hit_the_cap(tmp_path: Path) -> None:
    row = {"project": "chromium/src", "month": "2024-11", "merged": 10000, "created_in_window": 1}
    (tmp_path / "volume.jsonl").write_text(
        json.dumps({**row, "human": 1, "capped": True, "ranged": True}) + "\n"
    )
    (tmp_path / "yield.jsonl").write_text("")
    with pytest.raises(SystemExit, match="floor"):
        scoping.summarize(tmp_path, tmp_path / "out.json")
