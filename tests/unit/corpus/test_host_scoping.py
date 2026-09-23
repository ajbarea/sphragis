"""The scoping script is a second path to a live host, so it carries fetch's seal."""

from __future__ import annotations

import importlib.util
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


def test_measure_allows_the_last_dev_month(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    asked = _offline(monkeypatch, tmp_path)
    scoping.main(["measure", "volume", "v8/v8", "2025-10"])
    assert asked and '"merged": 0' in capsys.readouterr().out
