"""Stage dispatch and the configuration the stages need."""

from __future__ import annotations

import urllib.error
from pathlib import Path

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

    monkeypatch.setattr(cli, "http_transport", lambda: transport)
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


def test_http_transport_returns_error_statuses_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # urlopen raises HTTPError on 4xx/5xx, which would bypass the retry logic in
    # gerrit._get entirely: a retryable 500 would propagate as an exception instead of
    # being retried. The transport contract is (status, headers, body).
    import email.message
    import urllib.error
    import urllib.request

    from sphragis.corpus import cli

    headers = email.message.Message()
    headers["Retry-After"] = "1"

    def boom(*args: object, **kwargs: object) -> None:
        raise urllib.error.HTTPError("https://g/x", 500, "Server Error", headers, None)

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    status, got_headers, body = cli.http_transport()("https://g/x")
    assert status == 500
    assert got_headers.get("Retry-After") == "1"
    assert body == ""


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("[Errno -3] Temporary failure in name resolution"),
        TimeoutError("timed out"),
        ConnectionResetError("peer hung up"),
    ],
)
def test_http_transport_makes_connection_failures_retryable(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    # A DNS blip killed a 3,336-change build five minutes in, because a connection-level
    # failure is not an HTTPError and escaped the retry budget entirely. It is exactly as
    # transient as the 503 it is now reported as, and 503 is in gerrit._RETRYABLE.
    import urllib.request

    from sphragis.corpus import cli
    from sphragis.corpus.gerrit import _RETRYABLE

    def boom(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    status, headers, body = cli.http_transport()("https://g/x")
    assert status in _RETRYABLE
    assert (headers, body) == ({}, "")


def test_http_transport_still_distinguishes_a_fatal_client_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # HTTPError subclasses URLError, so the order of the except clauses decides whether a
    # 404 is reported as itself or laundered into a retryable 503 and retried five times.
    import email.message
    import urllib.request

    from sphragis.corpus import cli

    def boom(*args: object, **kwargs: object) -> None:
        raise urllib.error.HTTPError("https://g/x", 404, "Not Found", email.message.Message(), None)

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert cli.http_transport()("https://g/x")[0] == 404


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
    monkeypatch.setattr(cli, "http_transport", lambda: lambda url: (200, {}, ")]}'\n{}"))
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

    monkeypatch.setattr(cli, "http_transport", lambda: lambda url: (200, {}, ")]}'\n{}"))
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
    monkeypatch.setattr(cli, "http_transport", lambda: lambda url: (200, {}, ")]}'\n{}"))
    monkeypatch.setattr(cli, "scrubbed_comment_fetcher", lambda *a, **k: lambda n: {})
    monkeypatch.setattr(cli, "scrubbed_diff_fetcher", lambda *a, **k: lambda *i: {})

    assert cli.main(["build", "--org", "openstack", "--root", str(tmp_path), "--overwrite"]) == 0
    assert (examples / "2024-10.jsonl").read_text() == ""
