"""Stage dispatch and the configuration the stages need."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from sphragis.corpus.cli import GERRIT, STAGES, build_parser, main, require_salt
from sphragis.corpus.rules import BUILD_RULES


def test_every_stage_is_accepted() -> None:
    parser = build_parser()
    for stage in STAGES:
        assert parser.parse_args([stage]).stage == stage


def test_main_exits_for_an_unknown_stage() -> None:
    with pytest.raises(SystemExit):
        main(["nonsense"])


def test_every_organization_has_its_gerrit_instance() -> None:
    # Exact, so an organization added or dropped is a deliberate edit here too.
    assert GERRIT == {
        "aosp": "https://android-review.googlesource.com",
        "chromium": "https://chromium-review.googlesource.com",
        "openstack": "https://review.opendev.org",
        "qt": "https://codereview.qt-project.org",
    }


def test_chromium_is_an_org_choice() -> None:
    assert build_parser().parse_args(["fetch", "--org", "chromium"]).org == "chromium"


def test_org_choices_come_from_the_instance_table() -> None:
    # A typo in --org should fail at parse time rather than fetch from nowhere.
    with pytest.raises(SystemExit):
        build_parser().parse_args(["fetch", "--org", "openstak"])


def test_require_salt_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "s3cret")
    assert require_salt() == "s3cret"


def test_require_salt_exits_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without a salt the pseudonyms would not be stable across runs, so a corpus built
    # today would not compare with one built tomorrow.
    monkeypatch.delenv("SPHRAGIS_CORPUS_SALT", raising=False)
    with pytest.raises(SystemExit, match="SPHRAGIS_CORPUS_SALT"):
        require_salt()


def test_require_salt_rejects_an_empty_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "   ")
    with pytest.raises(SystemExit, match="SPHRAGIS_CORPUS_SALT"):
        require_salt()


def test_defaults_are_set() -> None:
    args = build_parser().parse_args(["fetch"])
    assert args.org == "openstack"
    assert args.root == Path("datasets/gerrit")


def test_fetch_writes_a_snapshot_and_reports_the_cutoff_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from sphragis.corpus import cli
    from sphragis.corpus.storage import read_snapshot, snapshot_path

    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    payload = [
        {
            "_number": 1,
            "change_id": "I1",
            "created": "2024-10-05 00:00:00.000000000",
            "owner": {"_account_id": 7, "name": "Alice"},
        },
        {
            "_number": 2,
            "change_id": "I2",
            "created": "2024-08-01 00:00:00.000000000",
            "owner": {"_account_id": 8},
        },
    ]

    def transport(url: str) -> tuple[int, dict[str, str], str]:
        return 200, {}, ")]}'\n" + json.dumps(payload)

    monkeypatch.setattr(cli, "http_transport", lambda **_: transport)
    rc = cli.main(["fetch", "--org", "openstack", "--month", "2024-10", "--root", str(tmp_path)])
    assert rc == 0
    rows = read_snapshot(snapshot_path(tmp_path, "openstack", "2024-10"))
    assert [r["change_id"] for r in rows] == ["I1"], "the pre-cutoff change is dropped"
    assert rows[0]["owner"] == {"_account_id": cli_pseudonym(7, "salt")}
    out = capsys.readouterr().out
    assert "dropped 1" in out and "kept 1" in out


def cli_pseudonym(value: object, salt: str) -> str:
    from sphragis.corpus.scrub import pseudonym

    return pseudonym(value, salt)


def test_fetch_refuses_without_a_salt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPHRAGIS_CORPUS_SALT", raising=False)
    with pytest.raises(SystemExit, match="SPHRAGIS_CORPUS_SALT"):
        main(["fetch", "--root", str(tmp_path)])


def test_verify_is_clean_on_a_freshly_frozen_corpus(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from sphragis.corpus.cli import main
    from sphragis.corpus.pipeline import freeze_windows

    windows = {
        "pilot": [{"id": "a", "change_id": "I1", "created": "2024-10-02"}],
        "train": [],
        "dev": [],
        "test": [],
    }
    from sphragis.corpus.refine import RULES_VERSION

    freeze_windows(
        tmp_path, "openstack", windows, stats={"rules": RULES_VERSION, "build_rules": BUILD_RULES}
    )
    assert main(["verify", "--org", "openstack", "--root", str(tmp_path)]) == 0
    assert "clean" in capsys.readouterr().out


def test_verify_fails_on_a_corpus_frozen_under_other_rules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from sphragis.corpus.cli import main
    from sphragis.corpus.pipeline import freeze_windows

    windows = {"pilot": [{"id": "a", "change_id": "I1", "created": "2024-10-02"}]}
    freeze_windows(
        tmp_path, "openstack", windows, stats={"rules": "old", "build_rules": BUILD_RULES}
    )
    assert main(["verify", "--org", "openstack", "--root", str(tmp_path)]) == 1
    assert "frozen under rules old" in capsys.readouterr().out


def test_verify_fails_when_a_window_no_longer_matches_its_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from sphragis.corpus.cli import main
    from sphragis.corpus.pipeline import freeze_windows

    windows = {
        "pilot": [{"id": "a", "change_id": "I1", "created": "2024-10-02"}],
        "train": [],
        "dev": [],
        "test": [],
    }
    from sphragis.corpus.refine import RULES_VERSION

    freeze_windows(
        tmp_path, "openstack", windows, stats={"rules": RULES_VERSION, "build_rules": BUILD_RULES}
    )
    splits = tmp_path / "openstack" / "splits"
    (splits / "pilot.jsonl").write_text('{"id": "tampered", "change_id": "I1"}\n')
    assert main(["verify", "--org", "openstack", "--root", str(tmp_path)]) == 1
    assert "pilot" in capsys.readouterr().out


def test_verify_reports_a_missing_manifest_rather_than_raising(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from sphragis.corpus.cli import main

    assert main(["verify", "--org", "qt", "--root", str(tmp_path)]) == 1
    assert "no manifest" in capsys.readouterr().out


class _FakeResponse:
    def __init__(self, status: int, body: str = "", headers: dict[str, str] | None = None) -> None:
        self.status, self._body, self._headers = status, body, headers or {}

    def read(self) -> bytes:
        return self._body.encode()

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self._headers.items())


def _scripted_connections(monkeypatch: pytest.MonkeyPatch, script: list[object]) -> list[Any]:
    """Replace HTTPSConnection with one that plays `script`: responses, or exceptions to raise."""
    import http.client

    instances: list[Any] = []

    class FakeConnection:
        def __init__(self, host: str, timeout: float | None = None) -> None:
            self.host, self.timeout, self.paths, self.closed = host, timeout, [], False
            self._pending: object = None
            instances.append(self)

        def request(self, method: str, path: str, headers: dict[str, str] | None = None) -> None:
            action = script.pop(0)
            if isinstance(action, BaseException):
                raise action
            self.paths.append(path)
            self._pending = action

        def getresponse(self) -> object:
            return self._pending

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(http.client, "HTTPSConnection", FakeConnection)
    return instances


def test_http_transport_reuses_one_connection_across_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sphragis.corpus import cli

    instances = _scripted_connections(
        monkeypatch, [_FakeResponse(200, "a"), _FakeResponse(200, "b")]
    )
    transport = cli.http_transport()
    assert transport("https://g/changes/?q=x&n=1")[2] == "a"
    assert transport("https://g/changes/2/comments")[2] == "b"
    assert len(instances) == 1, "a fresh handshake per request is what stalled the build"
    assert instances[0].paths == ["/changes/?q=x&n=1", "/changes/2/comments"]
    assert instances[0].timeout == 15.0


def test_http_transport_returns_error_statuses_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Raising on a 500 would bypass gerrit._get's retry budget and Retry-After handling.
    from sphragis.corpus import cli

    _scripted_connections(monkeypatch, [_FakeResponse(500, "", {"Retry-After": "1"})])
    status, headers, body = cli.http_transport()("https://g/x")
    assert (status, headers.get("Retry-After"), body) == (500, "1", "")


def test_http_transport_keeps_a_fatal_client_error_as_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sphragis.corpus import cli

    _scripted_connections(monkeypatch, [_FakeResponse(404)])
    assert cli.http_transport()("https://g/x")[0] == 404


@pytest.mark.parametrize(
    "error", [TimeoutError("timed out"), ConnectionRefusedError("refused"), OSError("no route")]
)
def test_http_transport_makes_connection_failures_retryable_and_replaces_the_connection(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    from sphragis.corpus import cli
    from sphragis.corpus.gerrit import _RETRYABLE

    instances = _scripted_connections(monkeypatch, [error, _FakeResponse(200, "ok")])
    transport = cli.http_transport()
    status, headers, body = transport("https://g/x")
    assert status in _RETRYABLE and (headers, body) == ({}, "")
    assert instances[0].closed
    assert transport("https://g/x")[2] == "ok"
    assert len(instances) == 2, "a failed connection must not be reused"


def test_http_transport_reconnects_once_when_the_server_closed_an_idle_keep_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import http.client

    from sphragis.corpus import cli

    instances = _scripted_connections(
        monkeypatch, [http.client.RemoteDisconnected("closed"), _FakeResponse(200, "fresh")]
    )
    assert cli.http_transport()("https://g/x") == (200, {}, "fresh")
    assert len(instances) == 2 and instances[0].closed


def test_http_transport_paces_requests_to_one_host(monkeypatch: pytest.MonkeyPatch) -> None:
    from sphragis.corpus import cli

    _scripted_connections(monkeypatch, [_FakeResponse(200)] * 3)
    now, slept = [100.0], []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    transport = cli.http_transport(min_interval=0.5, clock=lambda: now[0], sleep=sleep)
    transport("https://g/a")
    now[0] += 0.1
    transport("https://g/b")
    transport("https://g/c")
    assert slept == pytest.approx([0.4, 0.5])


def test_http_transport_paces_each_host_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    from sphragis.corpus import cli

    _scripted_connections(monkeypatch, [_FakeResponse(200)] * 2)
    slept: list[float] = []
    transport = cli.http_transport(min_interval=1.0, clock=lambda: 0.0, sleep=slept.append)
    transport("https://g/a")
    transport("https://h/a")
    assert slept == []


def test_http_transport_without_an_interval_never_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    from sphragis.corpus import cli

    _scripted_connections(monkeypatch, [_FakeResponse(200)] * 3)
    slept: list[float] = []
    transport = cli.http_transport(clock=lambda: 0.0, sleep=slept.append)
    for _ in range(3):
        transport("https://g/a")
    assert slept == []


def test_the_cli_paces_gerrit_by_default() -> None:
    from sphragis.corpus.cli import build_parser

    # One request a second. Two community Gerrits banned this client at five a second.
    assert build_parser().parse_args(["build"]).request_interval == 1.0


def _snapshot(tmp_path: Path, org: str, month: str, rows: list[dict[str, object]]) -> None:
    from sphragis.corpus.storage import write_snapshot

    write_snapshot(tmp_path, org, month, rows, record={}, overwrite=True)


def test_build_reads_every_snapshot_and_writes_examples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from sphragis.corpus import cli

    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    _snapshot(
        tmp_path,
        "openstack",
        "2024-10",
        [
            {
                "_number": 1,
                "change_id": "I1",
                "created": "2024-10-05 00:00:00.000000000",
                "owner": {"_account_id": "owner"},
                "revisions": {"a": {}, "b": {}},
            },
        ],
    )
    monkeypatch.setattr(cli, "http_transport", lambda **_: lambda url: (200, {}, ")]}'\n{}"))
    monkeypatch.setattr(
        cli,
        "scrubbed_comment_fetcher",
        lambda base, salt, transport: (
            lambda n: {
                "f.py": [
                    {"patch_set": 1, "line": 1, "message": "fix", "author": {"_account_id": "rev"}}
                ]
            }
        ),
    )
    monkeypatch.setattr(
        cli,
        "scrubbed_diff_fetcher",
        lambda base, transport: lambda n, r, p, b: {"content": [{"a": ["x=1"], "b": ["x = 1"]}]},
    )
    assert cli.main(["build", "--org", "openstack", "--root", str(tmp_path)]) == 0
    path = tmp_path / "openstack" / "examples" / "2024-10.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    assert len(rows) == 1 and rows[0]["comments"] == ["fix"]
    assert "1 examples" in capsys.readouterr().out
    # The drop profile survives the run. It used to be printed and lost, and the examples
    # on disk are post-filter, so the discard rate could not be reconstructed.
    drops = json.loads((path.parent / "2024-10.drops.json").read_text())
    assert drops["author_comment"] == 0 and set(drops) >= {"no_anchored_hunk", "metadata_file"}


def test_build_reports_a_missing_snapshot_rather_than_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    assert main(["build", "--org", "qt", "--root", str(tmp_path)]) == 1
    assert "no snapshots" in capsys.readouterr().out


def test_dedup_and_split_report_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    _built_corpus(tmp_path)
    assert main(["refine", "--org", "openstack", "--root", str(tmp_path)]) == 0
    assert main(["dedup", "--org", "openstack", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "exact" in out
    assert not (tmp_path / "openstack" / "splits").exists(), "dedup must not write"

    assert main(["split", "--org", "openstack", "--root", str(tmp_path)]) == 0
    assert "pilot" in capsys.readouterr().out
    assert not (tmp_path / "openstack" / "manifest.json").exists(), "split must not write"


def test_build_skips_months_already_built_so_it_can_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A month of OpenStack takes minutes over the network and Qt needs ~400 requests per
    # month. Without resume, any interruption discards the work already done.
    from sphragis.corpus import cli

    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    _snapshot(
        tmp_path,
        "openstack",
        "2024-10",
        [
            {
                "_number": 1,
                "change_id": "I1",
                "created": "2024-10-05 00:00:00.000000000",
                "owner": {"_account_id": "o"},
                "revisions": {"a": {}, "b": {}},
            },
        ],
    )
    examples = tmp_path / "openstack" / "examples"
    examples.mkdir(parents=True)
    (examples / "2024-10.jsonl").write_text('{"id": "already-built"}\n')

    called: list[int] = []

    def fetcher(*args: object, **kwargs: object):  # noqa: ANN202
        def fetch(*inner: object) -> dict[str, object]:
            called.append(1)
            return {}

        return fetch

    monkeypatch.setattr(cli, "http_transport", lambda **_: lambda url: (200, {}, ")]}'\n{}"))
    monkeypatch.setattr(cli, "scrubbed_comment_fetcher", fetcher)
    monkeypatch.setattr(cli, "scrubbed_diff_fetcher", fetcher)

    assert cli.main(["build", "--org", "openstack", "--root", str(tmp_path)]) == 0
    assert called == [], "an already-built month must not be re-fetched"
    assert "skip" in capsys.readouterr().out.lower()
    assert (examples / "2024-10.jsonl").read_text() == '{"id": "already-built"}\n'


def test_build_overwrite_rebuilds_a_month(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from sphragis.corpus import cli

    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    _snapshot(tmp_path, "openstack", "2024-10", [])
    examples = tmp_path / "openstack" / "examples"
    examples.mkdir(parents=True)
    (examples / "2024-10.jsonl").write_text('{"id": "stale"}\n')
    monkeypatch.setattr(cli, "http_transport", lambda **_: lambda url: (200, {}, ")]}'\n{}"))
    monkeypatch.setattr(cli, "scrubbed_comment_fetcher", lambda *a, **k: lambda n: {})
    monkeypatch.setattr(cli, "scrubbed_diff_fetcher", lambda *a, **k: lambda *i: {})

    assert cli.main(["build", "--org", "openstack", "--root", str(tmp_path), "--overwrite"]) == 0
    assert (examples / "2024-10.jsonl").read_text() == ""


def test_a_resumed_build_reports_the_whole_corpus_not_just_its_own_months(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The summary is the sampling section's figure, so a resume must not under-state it."""
    import json

    from sphragis.corpus import cli

    examples = tmp_path / "openstack" / "examples"
    examples.mkdir(parents=True)
    raw = tmp_path / "openstack" / "raw"
    raw.mkdir(parents=True)
    # One month already built, with its drop profile beside it.
    (examples / "2024-10.jsonl").write_text('{"id": "a"}\n{"id": "b"}\n')
    (examples / "2024-10.drops.json").write_text(json.dumps({"metadata_file": 7}))
    _snapshot(tmp_path, "openstack", "2024-10", [])

    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    monkeypatch.setattr(cli, "http_transport", lambda **_: lambda url: (200, {}, ")]}'\n{}"))
    monkeypatch.setattr(cli, "scrubbed_comment_fetcher", lambda *a, **k: lambda n: {})
    monkeypatch.setattr(cli, "scrubbed_diff_fetcher", lambda *a, **k: lambda *args: {})
    assert cli.main(["build", "--org", "openstack", "--root", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "skip, already built (2 examples)" in out
    assert "openstack: 2 examples over 1 months" in out
    assert "'metadata_file': 7" in out, "a skipped month's drops belong in the total too"


def test_drops_path_sits_beside_its_month() -> None:
    from sphragis.corpus.cli import drops_path

    assert drops_path(Path("x/examples/2024-10.jsonl")) == Path("x/examples/2024-10.drops.json")


def _fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, month: str) -> list[str]:
    """Run the fetch stage with the network replaced; return the queries that reached it."""
    from sphragis.corpus import cli

    queries: list[str] = []

    def fake_fetch(base, query, *, transport, options):
        queries.append(query)
        return [], {"pages": 0}

    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    monkeypatch.setattr(cli, "fetch_changes", fake_fetch)
    cli.main(["fetch", "--org", "openstack", "--month", month, "--root", str(tmp_path)])
    return queries


@pytest.mark.parametrize("month", ["2025-11", "2026-03", "2026-08"])
def test_fetch_refuses_a_test_window_month_while_sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, month: str
) -> None:
    with pytest.raises(SystemExit, match="sealed test window"):
        _fetch(tmp_path, monkeypatch, month)


def test_fetch_refuses_a_month_after_the_window_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gerrit filters on last update: a later month returns changes created inside the window."""
    with pytest.raises(SystemExit, match="sealed test window"):
        _fetch(tmp_path, monkeypatch, "2026-09")


def test_fetch_allows_the_last_dev_month(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _fetch(tmp_path, monkeypatch, "2025-10")


def test_fetch_unlocks_once_the_seal_records_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from sphragis.corpus.split import seal

    (tmp_path / "openstack").mkdir(parents=True)
    record = {**seal({"after": "2025-11-01"}), "accepted_at": "2027-02-04"}
    (tmp_path / "openstack" / "seal.json").write_text(json.dumps(record))
    assert _fetch(tmp_path, monkeypatch, "2025-11")


def test_a_seal_without_acceptance_stays_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from sphragis.corpus.split import seal

    (tmp_path / "openstack").mkdir(parents=True)
    (tmp_path / "openstack" / "seal.json").write_text(json.dumps(seal({"after": "2025-11-01"})))
    with pytest.raises(SystemExit, match="sealed test window"):
        _fetch(tmp_path, monkeypatch, "2025-11")


def test_build_rebuilds_a_month_built_from_a_different_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Resume must not mistake examples from a replaced snapshot for finished work.

    Refetching a month under different fetch parameters leaves examples derived from a
    snapshot that no longer exists. Skipping them mixes months built under different rules
    into one corpus, which is what happened to the control window's 2024-01.
    """
    from sphragis.corpus import cli

    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    examples = tmp_path / "openstack" / "examples"
    examples.mkdir(parents=True)
    built = examples / "2024-10.jsonl"
    built.write_text('{"id": "from-the-old-snapshot"}\n')
    cli.source_path(built).write_text(json.dumps({"snapshot_sha256": "a-snapshot-since-replaced"}))

    _snapshot(tmp_path, "openstack", "2024-10", [])

    monkeypatch.setattr(cli, "http_transport", lambda **_: lambda url: (200, {}, ")]}'\n{}"))
    monkeypatch.setattr(cli, "scrubbed_comment_fetcher", lambda *a, **k: lambda n: {})
    monkeypatch.setattr(cli, "scrubbed_diff_fetcher", lambda *a, **k: lambda *i: {})

    assert cli.main(["build", "--org", "openstack", "--root", str(tmp_path)]) == 0
    assert "built from a different snapshot, rebuilding" in capsys.readouterr().out
    assert built.read_text() == "", "the stale examples must not survive the rebuild"


def test_build_resumes_a_month_built_from_the_snapshot_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ordinary resume, which the staleness check must not turn into a rebuild."""
    from sphragis.corpus import cli

    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    _snapshot(tmp_path, "openstack", "2024-10", [])
    snapshot = tmp_path / "openstack" / "raw" / "2024-10.ndjson.gz"
    examples = tmp_path / "openstack" / "examples"
    examples.mkdir(parents=True)
    built = examples / "2024-10.jsonl"
    built.write_text('{"id": "already-built"}\n')
    cli.source_path(built).write_text(
        json.dumps({"snapshot_sha256": cli.snapshot_digest(snapshot), "build_rules": BUILD_RULES})
    )

    monkeypatch.setattr(cli, "http_transport", lambda **_: lambda url: (200, {}, ")]}'\n{}"))
    monkeypatch.setattr(cli, "scrubbed_comment_fetcher", lambda *a, **k: lambda n: {})
    monkeypatch.setattr(cli, "scrubbed_diff_fetcher", lambda *a, **k: lambda *i: {})

    assert cli.main(["build", "--org", "openstack", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "skip, already built" in out
    assert "rebuilding" not in out
    assert built.read_text() == '{"id": "already-built"}\n'


def test_build_records_the_snapshot_it_built_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sphragis.corpus import cli

    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    _snapshot(tmp_path, "openstack", "2024-10", [])
    monkeypatch.setattr(cli, "http_transport", lambda **_: lambda url: (200, {}, ")]}'\n{}"))
    monkeypatch.setattr(cli, "scrubbed_comment_fetcher", lambda *a, **k: lambda n: {})
    monkeypatch.setattr(cli, "scrubbed_diff_fetcher", lambda *a, **k: lambda *i: {})

    assert cli.main(["build", "--org", "openstack", "--root", str(tmp_path)]) == 0
    built = tmp_path / "openstack" / "examples" / "2024-10.jsonl"
    snapshot = tmp_path / "openstack" / "raw" / "2024-10.ndjson.gz"
    recorded = json.loads(cli.source_path(built).read_text())["snapshot_sha256"]
    assert recorded == cli.snapshot_digest(snapshot)


def test_examples_with_no_recorded_snapshot_still_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Corpora built before the digest existed must not all rebuild; say so instead."""
    from sphragis.corpus import cli

    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    _snapshot(tmp_path, "openstack", "2024-10", [])
    examples = tmp_path / "openstack" / "examples"
    examples.mkdir(parents=True)
    (examples / "2024-10.jsonl").write_text('{"id": "built-before-the-digest"}\n')

    monkeypatch.setattr(cli, "http_transport", lambda **_: lambda url: (200, {}, ")]}'\n{}"))
    monkeypatch.setattr(cli, "scrubbed_comment_fetcher", lambda *a, **k: lambda n: {})
    monkeypatch.setattr(cli, "scrubbed_diff_fetcher", lambda *a, **k: lambda *i: {})

    assert cli.main(["build", "--org", "openstack", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "no snapshot digest recorded" in out
    assert "skip, already built" in out


def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    from sphragis.corpus import cli

    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    monkeypatch.setattr(cli, "http_transport", lambda **_: lambda url: (200, {}, ")]}'\n{}"))
    monkeypatch.setattr(cli, "scrubbed_comment_fetcher", lambda *a, **k: lambda n: {})
    monkeypatch.setattr(cli, "scrubbed_diff_fetcher", lambda *a, **k: lambda *i: {})


def _change(number: int, created: str = "2024-10-05 00:00:00.000000000") -> dict[str, object]:
    return {
        "_number": number,
        "change_id": f"I{number}",
        "created": created,
        "owner": {"_account_id": "o"},
        "revisions": {"a": {}, "b": {}},
    }


def test_refetching_the_same_path_with_different_content_rebuilds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The digest must be of the snapshot's CONTENT, not of its name or its size.

    Every other test either records a literal fake digest or computes the expected one with
    the function under test, so any self-consistent digest passes them -- including one that
    hashes the filename, which would reproduce the original bug exactly.
    """
    from sphragis.corpus import cli

    _offline(monkeypatch)
    _snapshot(tmp_path, "openstack", "2024-10", [_change(1)])
    assert cli.main(["build", "--org", "openstack", "--root", str(tmp_path)]) == 0
    capsys.readouterr()

    # Same organization, same month, same path: only the content differs.
    _snapshot(tmp_path, "openstack", "2024-10", [_change(2), _change(3)])
    assert cli.main(["build", "--org", "openstack", "--root", str(tmp_path)]) == 0
    assert "built from a different snapshot, rebuilding" in capsys.readouterr().out


def test_an_unreadable_record_resumes_rather_than_aborting_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A truncated record is what a kill or a full disk leaves during the final write.

    Raising made one bad month abort the build for every month after it, and --overwrite
    could not clear it because the guard ran first.
    """
    from sphragis.corpus import cli

    _offline(monkeypatch)
    _snapshot(tmp_path, "openstack", "2024-10", [])
    examples = tmp_path / "openstack" / "examples"
    examples.mkdir(parents=True)
    built = examples / "2024-10.jsonl"
    built.write_text('{"id": "a"}\n')

    for broken in ('{"snapshot_sha256": "abc', "", "null", "[]"):
        cli.source_path(built).write_text(broken)
        assert cli.main(["build", "--org", "openstack", "--root", str(tmp_path)]) == 0
        assert "no snapshot digest recorded" in capsys.readouterr().out


def test_a_month_whose_write_was_interrupted_is_not_certified_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The record is the completion marker, so a half-written month must rebuild."""
    from sphragis.corpus import cli

    _offline(monkeypatch)
    _snapshot(tmp_path, "openstack", "2024-10", [])
    snapshot = tmp_path / "openstack" / "raw" / "2024-10.ndjson.gz"
    examples = tmp_path / "openstack" / "examples"
    examples.mkdir(parents=True)
    built = examples / "2024-10.jsonl"
    built.write_text('{"id": "truncated-tai\n')
    # The digest is right: the snapshot never changed. Only `complete` says otherwise.
    cli.source_path(built).write_text(
        json.dumps({"snapshot_sha256": cli.snapshot_digest(snapshot), "complete": False})
    )

    assert cli.main(["build", "--org", "openstack", "--root", str(tmp_path)]) == 0
    assert "skip, already built" not in capsys.readouterr().out
    assert built.read_text() == ""


def test_a_record_written_before_complete_existed_still_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The months already on disk were backfilled without the marker; they must not rebuild."""
    from sphragis.corpus import cli

    _offline(monkeypatch)
    _snapshot(tmp_path, "openstack", "2024-10", [])
    snapshot = tmp_path / "openstack" / "raw" / "2024-10.ndjson.gz"
    examples = tmp_path / "openstack" / "examples"
    examples.mkdir(parents=True)
    built = examples / "2024-10.jsonl"
    built.write_text('{"id": "a"}\n')
    cli.source_path(built).write_text(
        json.dumps({"snapshot_sha256": cli.snapshot_digest(snapshot)})
    )

    assert cli.main(["build", "--org", "openstack", "--root", str(tmp_path)]) == 0
    assert "skip, already built" in capsys.readouterr().out
    assert built.read_text() == '{"id": "a"}\n'


def test_fetch_restricted_to_projects_asks_for_those_and_records_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from sphragis.corpus import cli
    from sphragis.corpus.storage import snapshot_path

    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    asked: list[str] = []

    def transport(url: str) -> tuple[int, dict[str, str], str]:
        asked.append(url)
        return 200, {}, ")]}'\n" + json.dumps([])

    monkeypatch.setattr(cli, "http_transport", lambda **_: transport)
    argv = ["fetch", "--org", "aosp", "--month", "2025-03", "--root", str(tmp_path)]
    argv += ["--project", "platform/art", "--project", "platform/bionic"]
    assert cli.main(argv) == 0
    query = (
        "status:merged after:2025-03-01 before:2025-04-01"
        " (project:platform/art OR project:platform/bionic)"
    )
    assert asked and asked[0].startswith("https://android-review.googlesource.com/changes/?q=")
    from urllib.parse import quote

    assert quote(query) in asked[0]
    raw = snapshot_path(tmp_path, "aosp", "2025-03")
    record = json.loads((raw.parent / "2025-03.record.json").read_text())
    assert record["query"] == query


def test_the_module_entry_runs_the_cli_rather_than_exiting_silently() -> None:
    """`python -m sphragis.corpus.cli` used to import, run nothing and exit 0.

    A fetch invoked that way reported success and wrote no snapshot, which is the one failure
    shape this repository treats as worse than a crash.
    """
    result = subprocess.run(
        [sys.executable, "-m", "sphragis.corpus.cli", "--help"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[3],
    )
    assert result.returncode == 0, result.stderr
    assert "fetch" in result.stdout, result.stdout


def test_the_module_entry_refuses_an_unknown_stage() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "sphragis.corpus.cli", "nonsense"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[3],
    )
    assert result.returncode != 0, "an unknown stage must not look like a clean run"


def _built_corpus(root: Path, kind: str = "REWORK") -> Path:
    """One built month of two duplicate examples, and the raw snapshot they came from."""
    import json

    from sphragis.corpus.cli import snapshot_digest
    from sphragis.corpus.load import write_build_record
    from sphragis.corpus.storage import write_snapshot

    base = {"change_id": "I1", "project": "openstack/nova", "created": "2024-10-05"}
    rows = [
        {**base, "id": i, "patch_set": 1, "comments": ["rename"], "before": "x", "after": "y"}
        for i in ("a", "b")
    ]
    examples = root / "openstack" / "examples"
    examples.mkdir(parents=True, exist_ok=True)
    (examples / "2024-10.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    revisions = {"p1": {"_number": 1}, "p2": {"_number": 2, "kind": kind}}
    change = {**base, "_number": 7, "revisions": revisions}
    write_snapshot(root, "openstack", "2024-10", [change], record={"query": "test"})
    snapshot = root / "openstack" / "raw" / "2024-10.ndjson.gz"
    write_build_record(examples / "2024-10.jsonl", snapshot_digest(snapshot), complete=True)
    return examples / "2024-10.jsonl"


def test_dedup_refuses_a_corpus_that_was_never_refined(tmp_path: Path) -> None:
    _built_corpus(tmp_path)
    with pytest.raises(SystemExit, match="not refined"):
        main(["dedup", "--org", "openstack", "--root", str(tmp_path)])


def test_refine_writes_examples_drops_and_a_source_record(tmp_path: Path) -> None:
    import json

    _built_corpus(tmp_path)
    assert main(["refine", "--org", "openstack", "--root", str(tmp_path)]) == 0
    refined = tmp_path / "openstack" / "refined"
    assert len((refined / "2024-10.jsonl").read_text().splitlines()) == 2
    assert json.loads((refined / "2024-10.drops.json").read_text())["not_rework_successor"] == 0
    assert "examples_sha256" in json.loads((refined / "2024-10.source.json").read_text())


def test_refine_drops_examples_whose_successor_only_rebased(tmp_path: Path) -> None:
    _built_corpus(tmp_path, kind="TRIVIAL_REBASE")
    assert main(["refine", "--org", "openstack", "--root", str(tmp_path)]) == 0
    assert (tmp_path / "openstack" / "refined" / "2024-10.jsonl").read_text() == ""


def test_a_rebuilt_month_makes_its_refinement_stale(tmp_path: Path) -> None:
    built = _built_corpus(tmp_path)
    assert main(["refine", "--org", "openstack", "--root", str(tmp_path)]) == 0
    built.write_text(built.read_text() + "\n")
    with pytest.raises(SystemExit, match="rebuilt since it was refined"):
        main(["split", "--org", "openstack", "--root", str(tmp_path)])


def test_a_refinement_under_other_rules_is_stale(tmp_path: Path) -> None:
    import json

    _built_corpus(tmp_path)
    assert main(["refine", "--org", "openstack", "--root", str(tmp_path)]) == 0
    record = tmp_path / "openstack" / "refined" / "2024-10.source.json"
    record.write_text(json.dumps({**json.loads(record.read_text()), "rules": "old"}))
    with pytest.raises(SystemExit, match="refined under other rules"):
        main(["freeze", "--org", "openstack", "--root", str(tmp_path)])


def test_a_month_built_under_other_build_rules_is_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refining it again would stamp the old build's output as current."""
    from sphragis.corpus import cli

    _offline(monkeypatch)
    _snapshot(tmp_path, "openstack", "2024-10", [])
    snapshot = tmp_path / "openstack" / "raw" / "2024-10.ndjson.gz"
    built = tmp_path / "openstack" / "examples" / "2024-10.jsonl"
    built.parent.mkdir(parents=True)
    built.write_text('{"id": "old-build"}\n')
    cli.source_path(built).write_text(
        json.dumps({"snapshot_sha256": cli.snapshot_digest(snapshot), "build_rules": "old"})
    )
    assert cli.main(["build", "--org", "openstack", "--root", str(tmp_path)]) == 0
    assert "built under other build rules, rebuilding" in capsys.readouterr().out
    assert built.read_text() == ""
    assert json.loads(cli.source_path(built).read_text())["build_rules"] == BUILD_RULES


def _unstamped(root: Path, monkeypatch: pytest.MonkeyPatch, **allowlist: Any) -> Path:
    """One month built before build rules were recorded, and an audit allowlist covering it."""
    from sphragis.corpus import cli

    built = _built_corpus(root)
    record = built.with_name("2024-10.source.json")
    digest = json.loads(record.read_text())["snapshot_sha256"]
    record.write_text(json.dumps({"snapshot_sha256": digest}))
    covered = {"build_rules": BUILD_RULES, "snapshots": {digest: "openstack/2024-10"}}
    monkeypatch.setattr(cli, "_stamp_allowlist", lambda: {**covered, **allowlist})
    return record


def test_an_unstamped_month_is_refused_until_stamped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sphragis.corpus.load import refined_examples

    record = _unstamped(tmp_path, monkeypatch)
    assert main(["refine", "--org", "openstack", "--root", str(tmp_path)]) == 0
    with pytest.raises(SystemExit, match="other build rules"):
        refined_examples(tmp_path, "openstack")
    assert main(["stamp", "--org", "openstack", "--root", str(tmp_path)]) == 0
    stamped = json.loads(record.read_text())
    assert stamped["build_rules"] == BUILD_RULES and stamped["stamped"]
    assert stamped["without_context"] == 2, "the fixture's rows carry no context"
    assert len(refined_examples(tmp_path, "openstack")) == 2


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        (lambda record: {**record, "build_rules": "old"}, "rebuild it"),
        (lambda record: {**record, "complete": False}, "did not complete"),
        (lambda record: {k: v for k, v in record.items() if k != "snapshot_sha256"}, "refetched"),
        (lambda record: {**record, "snapshot_sha256": "0" * 64}, "refetched"),
    ],
)
def test_stamp_refuses_a_month_it_cannot_vouch_for_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    damage: Any,
    reason: str,
) -> None:
    record = _unstamped(tmp_path, monkeypatch)
    record.write_text(json.dumps(damage(json.loads(record.read_text()))))
    before = record.read_text()
    assert main(["stamp", "--org", "openstack", "--root", str(tmp_path)]) == 1
    assert reason in capsys.readouterr().out
    assert record.read_text() == before


def test_stamp_refuses_a_month_the_audit_did_not_cover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _unstamped(tmp_path, monkeypatch, snapshots={})
    assert main(["stamp", "--org", "openstack", "--root", str(tmp_path)]) == 1
    assert "not a month the audit covered" in capsys.readouterr().out


def test_stamp_refuses_once_the_build_rules_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _unstamped(tmp_path, monkeypatch, build_rules="the audited build")
    assert main(["stamp", "--org", "openstack", "--root", str(tmp_path)]) == 1
    assert "rebuild instead" in capsys.readouterr().out


def test_stamp_checks_every_month_before_writing_any(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _unstamped(tmp_path, monkeypatch)
    later = record.with_name("2024-11.jsonl")
    later.write_text("{not json\n")
    later.with_name("2024-11.source.json").write_text("")
    before = record.read_text()
    assert main(["stamp", "--org", "openstack", "--root", str(tmp_path)]) == 1
    assert record.read_text() == before


def test_the_audit_allowlist_names_the_build_rules_of_this_code() -> None:
    """Stamping stays possible exactly while the build is the one the audit judged."""
    from sphragis.corpus import cli

    allowed = cli._stamp_allowlist()
    assert allowed["build_rules"] == BUILD_RULES
    assert all(len(digest) == 64 for digest in allowed["snapshots"])


def test_freeze_records_both_rule_digests(tmp_path: Path) -> None:
    from sphragis.corpus.refine import RULES_VERSION

    _built_corpus(tmp_path)
    assert main(["refine", "--org", "openstack", "--root", str(tmp_path)]) == 0
    assert main(["freeze", "--org", "openstack", "--root", str(tmp_path)]) == 0
    stats = json.loads((tmp_path / "openstack" / "manifest.json").read_text())["stats"]
    assert (stats["build_rules"], stats["rules"]) == (BUILD_RULES, RULES_VERSION)


def test_verify_fails_on_a_corpus_frozen_under_other_build_rules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from sphragis.corpus.pipeline import freeze_windows
    from sphragis.corpus.refine import RULES_VERSION

    windows = {"pilot": [{"id": "a", "change_id": "I1", "created": "2024-10-02"}]}
    freeze_windows(
        tmp_path, "openstack", windows, stats={"rules": RULES_VERSION, "build_rules": "old"}
    )
    assert main(["verify", "--org", "openstack", "--root", str(tmp_path)]) == 1
    assert "frozen under build_rules old" in capsys.readouterr().out
