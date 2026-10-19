"""A host's project list, read once through the real transport: one request, the grant held."""

from __future__ import annotations

import http.client
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from sphragis.corpus import cli, hosts

ROOT = Path(__file__).resolve().parents[2]
FUCHSIA = "fuchsia-review.googlesource.com"
_spec = importlib.util.spec_from_file_location(
    "gerrit_projects", ROOT / "scripts" / "gerrit_projects.py"
)
assert _spec is not None and _spec.loader is not None
projects = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(projects)

LISTED = {
    "fuchsia": {"state": "ACTIVE"},
    "third_party/libc++": {"state": "READ_ONLY"},
    "All-Projects": {"state": "ACTIVE"},
    "All-Users": {"state": "ACTIVE"},
    "All-Things": {"state": "ACTIVE"},
}


def _body(listed: Any) -> str:
    return ")]}'\n" + json.dumps(listed)


class _Response:
    def __init__(self, status: int, body: str = "") -> None:
        self.status, self._body = status, body

    def read(self) -> bytes:
        return self._body.encode()

    def getheaders(self) -> list[tuple[str, str]]:
        return []


def _connections(monkeypatch: pytest.MonkeyPatch, script: list[_Response], now=None) -> list:
    asked: list[tuple[str, str, float | None]] = []

    class Connection:
        def __init__(self, host: str, timeout: float | None = None) -> None:
            self.host = host

        def request(self, method: str, path: str, headers: Any = None) -> None:
            asked.append((self.host, path, None if now is None else now[0]))
            self._pending = script.pop(0)

        def getresponse(self) -> _Response:
            return self._pending

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    return asked


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPHRAGIS_GRANTS", str(tmp_path / "grants"))
    monkeypatch.setattr(hosts, "LOCK_DIR", tmp_path / "locks")
    yield
    for directory, host in list(hosts._CLAIMS):
        hosts.release_host(host, directory)


def _grant(tmp_path: Path) -> None:
    (tmp_path / "grants").write_text(f"{FUCHSIA}\n")


def _run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["gerrit_projects", *argv])
    projects.main()


def test_without_the_grant_nothing_is_asked_or_written(tmp_path, monkeypatch) -> None:
    asked = _connections(monkeypatch, [_Response(200, _body(LISTED))])
    out = tmp_path / "out.json"
    with pytest.raises(SystemExit, match="written grant"):
        _run(monkeypatch, "--org", "fuchsia", "--out", str(out))
    assert asked == [] and not out.exists()


def test_one_request_through_the_claimed_host_writes_the_scope(tmp_path, monkeypatch) -> None:
    _grant(tmp_path)
    asked = _connections(monkeypatch, [_Response(200, _body(LISTED))])
    out = tmp_path / "out.json"
    _run(monkeypatch, "--org", "fuchsia", "--out", str(out))
    assert asked == [(FUCHSIA, "/projects/?type=CODE", None)]
    assert (tmp_path / "locks" / f"{FUCHSIA}.lock").exists()
    assert cli.fuchsia_projects(out) == ("All-Things", "fuchsia", "third_party/libc++")
    report = json.loads(out.read_text())
    assert report["listed"] == 5 and report["states"] == {"ACTIVE": 4, "READ_ONLY": 1}


def test_a_5xx_quiets_the_host_five_minutes_before_the_retry(tmp_path, monkeypatch) -> None:
    _grant(tmp_path)
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    asked = _connections(monkeypatch, [_Response(503), _Response(200, _body(LISTED))], now)
    monkeypatch.setattr(
        projects,
        "http_transport",
        lambda: cli.http_transport(clock=lambda: now[0], sleep=sleep, wall=lambda: 1e9 + now[0]),
    )
    monkeypatch.setattr(projects.time, "sleep", sleep)
    _run(monkeypatch, "--org", "fuchsia", "--out", str(tmp_path / "out.json"))
    assert [a[1] for a in asked] == ["/projects/?type=CODE"] * 2
    assert asked[1][2] - asked[0][2] >= 300.0


def test_the_list_is_read_once(tmp_path, monkeypatch) -> None:
    _grant(tmp_path)
    out = tmp_path / "out.json"
    out.write_text("{}")
    asked = _connections(monkeypatch, [_Response(200, _body(LISTED))])
    with pytest.raises(SystemExit, match="read once"):
        _run(monkeypatch, "--org", "fuchsia", "--out", str(out))
    assert asked == [] and out.read_text() == "{}"


@pytest.mark.parametrize("org", ["aosp", "chromium", "qt"])
def test_no_other_host_is_asked_for_its_whole_project_list(org: str, monkeypatch) -> None:
    asked = _connections(monkeypatch, [])
    with pytest.raises(SystemExit):
        _run(monkeypatch, "--org", org)
    assert asked == []


@pytest.mark.parametrize(
    ("listed", "why"),
    [
        ({}, "empty"),
        (["fuchsia"], "not a mapping"),
        ({"fuchsia": {}, "z": {"_more_projects": True}}, "truncated"),
        ({"fuchsia": {}, "^.*": {}}, "misread"),
        ({"fuchsia": {}, "a b": {}}, "misread"),
    ],
)
def test_a_partial_or_unsafe_list_writes_nothing(tmp_path, monkeypatch, listed, why) -> None:
    _grant(tmp_path)
    _connections(monkeypatch, [_Response(200, _body(listed))])
    out = tmp_path / "out.json"
    with pytest.raises(SystemExit, match=why):
        _run(monkeypatch, "--org", "fuchsia", "--out", str(out))
    assert not out.exists()


def test_only_the_hosts_own_two_projects_are_set_aside() -> None:
    assert projects.code_projects(LISTED) == ["All-Things", "fuchsia", "third_party/libc++"]
