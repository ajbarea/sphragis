"""Stage dispatch and the configuration the stages need."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sphragis.corpus.cli import GERRIT, STAGES, build_parser, main, require_salt


def test_every_stage_is_accepted() -> None:
    parser = build_parser()
    for stage in STAGES:
        assert parser.parse_args([stage]).stage == stage


def test_main_exits_for_an_unknown_stage() -> None:
    with pytest.raises(SystemExit):
        main(["nonsense"])


def test_both_organizations_have_a_gerrit_instance() -> None:
    assert GERRIT["openstack"] == "https://review.opendev.org"
    assert GERRIT["qt"] == "https://codereview.qt-project.org"


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
    freeze_windows(tmp_path, "openstack", windows, stats={})
    assert main(["verify", "--org", "openstack", "--root", str(tmp_path)]) == 0
    assert "clean" in capsys.readouterr().out


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
    freeze_windows(tmp_path, "openstack", windows, stats={})
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
    import json

    examples = tmp_path / "openstack" / "examples"
    examples.mkdir(parents=True)
    rows = [
        {"id": "a", "change_id": "I1", "created": "2024-10-05", "before": "x", "after": "y"},
        {"id": "b", "change_id": "I1", "created": "2024-10-05", "before": "x", "after": "y"},
    ]
    (examples / "2024-10.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

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
        json.dumps({"snapshot_sha256": cli.snapshot_digest(snapshot)})
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
