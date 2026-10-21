"""A build stopped partway through a month resumes from the changes it had read, and the month it
writes matches an uninterrupted build's."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from sphragis.corpus import cli
from sphragis.corpus.storage import write_snapshot

DIFF = {"content": [{"a": ["x=1"], "b": ["x = 1"]}]}
COMMENTS = {
    "f.py": [{"patch_set": 1, "line": 1, "message": "fix", "author": {"_account_id": "rev"}}]
}


class Stop(BaseException):
    """What a hard stop looks like to the build: nothing it catches."""


def _change(number: int) -> dict[str, Any]:
    return {
        "_number": number,
        "change_id": f"I{number}",
        "created": "2024-10-05 00:00:00.000000000",
        "owner": {"_account_id": "owner"},
        "revisions": {"a": {}, "b": {}},
    }


def _paths(root: Path) -> tuple[Path, Path]:
    examples = root / "openstack" / "examples" / "2024-10.jsonl"
    return examples, root / "openstack" / "raw" / "2024-10.build.partial.jsonl"


def _build(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    comments: Callable[[int], dict[str, Any]] | None = None,
    *flags: str,
) -> list[int]:
    """Builds the month and returns the changes whose comments were asked for."""
    monkeypatch.setenv("SPHRAGIS_CORPUS_SALT", "salt")
    if not (root / "openstack" / "raw" / "2024-10.ndjson.gz").exists():
        write_snapshot(root, "openstack", "2024-10", [_change(n) for n in (1, 2, 3)], record={})
    asked: list[int] = []

    def fetch_comments(number: int) -> dict[str, Any]:
        asked.append(number)
        return comments(number) if comments else COMMENTS

    monkeypatch.setattr(cli, "http_transport", lambda **_: lambda url: (200, {}, ")]}'\n{}"))
    monkeypatch.setattr(
        cli, "scrubbed_comment_fetcher", lambda base, salt, transport: fetch_comments
    )
    monkeypatch.setattr(cli, "scrubbed_diff_fetcher", lambda base, transport: lambda *a: DIFF)
    assert cli.main(["build", "--org", "openstack", "--root", str(root), *flags]) == 0
    return asked


def _stop_at(number: int) -> Callable[[int], dict[str, Any]]:
    def comments(asked: int) -> dict[str, Any]:
        if asked == number:
            raise Stop
        return COMMENTS

    return comments


def test_a_stopped_month_resumes_where_it_stopped_and_matches_an_uninterrupted_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    whole = tmp_path / "whole"
    _build(whole, monkeypatch)
    stopped = tmp_path / "stopped"
    with pytest.raises(Stop):
        _build(stopped, monkeypatch, _stop_at(3))
    examples, checkpoint = _paths(stopped)
    assert not examples.exists() and checkpoint.is_file()

    asked = _build(stopped, monkeypatch)
    assert asked == [3]
    assert "resuming, 2 changes read before the stop" in capsys.readouterr().out
    assert examples.read_bytes() == _paths(whole)[0].read_bytes()
    assert examples.with_name("2024-10.drops.json").read_bytes() == (
        _paths(whole)[0].with_name("2024-10.drops.json").read_bytes()
    )
    assert not checkpoint.exists()


def test_a_closed_month_leaves_no_checkpoint(tmp_path: Path, monkeypatch) -> None:
    _build(tmp_path, monkeypatch)
    assert not _paths(tmp_path)[1].exists()


@pytest.mark.parametrize(
    "changed",
    [
        {"build_rules": "other"},
        {"snapshot_sha256": "other"},
        {"fewer_requests": True},
        {"read_cap": 5},
        {"salt": "other"},
    ],
)
def test_a_checkpoint_under_another_key_is_discarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: dict[str, Any]
) -> None:
    with pytest.raises(Stop):
        _build(tmp_path, monkeypatch, _stop_at(3))
    checkpoint = _paths(tmp_path)[1]
    lines = checkpoint.read_text().splitlines()
    header = json.loads(lines[0])
    header["key"].update(changed)
    checkpoint.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n")
    assert _build(tmp_path, monkeypatch) == [1, 2, 3]
    assert not checkpoint.exists()


def test_a_checkpoint_with_no_key_is_discarded(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(Stop):
        _build(tmp_path, monkeypatch, _stop_at(3))
    checkpoint = _paths(tmp_path)[1]
    checkpoint.write_text("\n".join(checkpoint.read_text().splitlines()[1:]) + "\n")
    assert _build(tmp_path, monkeypatch) == [1, 2, 3]


def test_a_change_whose_read_failed_is_asked_again(tmp_path: Path, monkeypatch) -> None:
    def comments(number: int) -> dict[str, Any]:
        if number == 1:
            raise OSError("reset")
        if number == 3:
            raise Stop
        return COMMENTS

    with pytest.raises(Stop):
        _build(tmp_path, monkeypatch, comments)
    assert _build(tmp_path, monkeypatch) == [1, 3]
    examples = _paths(tmp_path)[0]
    assert len(examples.read_text().splitlines()) == 3
    assert json.loads(examples.with_name("2024-10.drops.json").read_text())["comment_error"] == 0


def test_the_checkpoint_sits_where_no_stage_globs_examples() -> None:
    path = cli.build_checkpoint(Path("root/openstack/raw"), "2024-10")
    assert path.parent.name == "raw" and not path.name.endswith(".ndjson.gz")
    assert path.suffix == ".jsonl" and path.parent.name != "examples"


def test_the_salt_is_not_written_into_the_checkpoint(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(Stop):
        _build(tmp_path, monkeypatch, _stop_at(3))
    header = _paths(tmp_path)[1].read_text().splitlines()[0]
    assert '"salt"' in header and '"salt": "salt"' not in header
