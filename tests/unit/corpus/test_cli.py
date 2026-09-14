"""Stage dispatch and the configuration the stages need."""

from __future__ import annotations

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
