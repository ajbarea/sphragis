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
    scoping.main(["measure", "volume", "v8/v8", "2025-10"])
    assert asked and '"merged": 0' in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["volume", "ranged"])
def test_a_listing_naming_a_project_passes_the_grants_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    """Chromium's grant covers every code project; a project the script names is one of them."""
    from sphragis.corpus import cli

    asked = _offline(monkeypatch, tmp_path)
    scoping.main(["measure", kind, "chromium/src", "2025-01"])
    scope = cli.REST_PERMITTED[scoping.HOST].scope
    assert scope is not None and asked
    targets = [url.removeprefix(scoping.BASE) for url in asked]
    assert all(cli.out_of_scope_request(t, scope, set()) is None for t in targets), targets


@pytest.mark.parametrize("kind", ["volume", "ranged", "yield"])
def test_a_month_outside_the_grant_is_refused_before_any_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    """Before the grant's first month (Qt's training window opens 2024-11-01)."""
    asked = _offline(monkeypatch, tmp_path)
    with pytest.raises(SystemExit, match="2024-10: outside the host's grant"):
        scoping.main(["measure", kind, "v8/v8", "2024-10", "1"])
    assert asked == []


def _change(number: int, created: str, branch: str = "main") -> dict:
    return {
        "_number": number,
        "created": created,
        "project": "v8/v8",
        "branch": branch,
        "owner": {"_account_id": 1, "email": "dev@example.org"},
        "current_revision": "r",
        "revisions": {"r": {"_number": 1, "files": {}}},
    }


def test_only_changes_created_inside_the_grant_are_asked_about(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [_change(1, "2025-08-20 00:00:00.000000000"), _change(2, "2025-09-02 00:00:00.000")]
    # A change on refs/meta/ holds no code, so the grant does not admit it to be asked about.
    rows.append(_change(3, "2025-08-21 00:00:00.000000000", "refs/meta/config"))
    monkeypatch.setattr(scoping, "_transport", lambda *_: None)
    monkeypatch.setattr(scoping, "listing", lambda *_, **__: (rows, False))
    asked: list[str] = []
    monkeypatch.setattr(scoping, "get", lambda url, _: asked.append(url) or {})
    scoping.measure_yield("v8/v8", "2025-08", 3)
    assert asked == [f"{scoping.BASE}/changes/1/comments"]
    assert '"sampled": 1' in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["volume", "ranged"])
def test_a_count_the_host_would_truncate_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    """chromium/src passes the host's 10,000 results a month; a count cut short is never printed."""
    from urllib.parse import unquote

    from sphragis.corpus.gerrit import TruncatedListing

    served = [{"_number": 1, "id": "a", "updated": "2025-01-20 10:00:00.000000000"}]
    unserved = [{"_number": 2, "id": "b", "updated": "2025-01-10 10:00:00.000000000"}]

    def transport(url: str) -> tuple[int, dict[str, str], str]:
        probe = 'before:"' in unquote(url)
        return 200, {}, ")]}'\n" + json.dumps(unserved if probe else served)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scoping, "http_transport", lambda **_: transport)
    with pytest.raises(TruncatedListing):
        scoping.main(["measure", kind, "chromium/src", "2025-01"])


def test_a_ranged_count_counts_a_change_at_a_shared_midnight_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Gerrit's after: and before: both include their own time, so a change last updated at the
    midnight two sub-ranges share is listed by both."""
    owner = {"_account_id": 1, "email": "dev@example.org"}
    edge = {
        "_number": 1,
        "project": "chromium/src",
        "created": "2025-01-02 00:00:00.000000000",
        "updated": "2025-01-06 00:00:00.000000000",
        "owner": owner,
    }
    inside = {**edge, "_number": 2, "updated": "2025-01-07 10:00:00.000000000"}
    parts = {
        "status:merged after:2025-01-01 before:2025-01-06 (project:chromium/src)": [edge],
        "status:merged after:2025-01-06 before:2025-01-11 (project:chromium/src)": [
            {**edge, "_more_changes": True},
            inside,
        ],
    }
    monkeypatch.setattr(scoping, "_transport", lambda *_: None)
    monkeypatch.setattr(
        scoping, "listing", lambda _t, q, *_, **__: ([dict(r) for r in parts.get(q, [])], False)
    )
    scoping.measure_ranged("chromium/src", ["2025-01"])
    row = json.loads(capsys.readouterr().out)
    assert row["merged"] == 2 and row["ranged"] is True and "capped" not in row


def test_a_ranged_count_refuses_any_other_repeat(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {
        "_number": 1,
        "project": "chromium/src",
        "created": "2025-01-02 00:00:00.000000000",
        "updated": "2025-01-03 10:00:00.000000000",
        "owner": {"_account_id": 1, "email": "dev@example.org"},
    }
    monkeypatch.setattr(scoping, "_transport", lambda *_: None)
    monkeypatch.setattr(scoping, "listing", lambda *_, **__: ([dict(row)], False))
    with pytest.raises(SystemExit, match="repeat"):
        scoping.measure_ranged("chromium/src", ["2025-01"])
