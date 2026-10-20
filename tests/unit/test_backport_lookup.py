"""The backport lookup script end to end, on a local repository and stubbed windows."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sphragis.corpus import lookup

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "backport_lookup", ROOT / "scripts" / "backport_lookup.py"
)
assert _spec is not None and _spec.loader is not None
script = importlib.util.module_from_spec(_spec)
sys.argv = ["backport_lookup"]
_spec.loader.exec_module(script)

OLD = "return self.client.get_server(server_id)"
NEW = "return self.compute.find_server(name_or_id)"


def _repo(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2024-03-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2024-03-01T00:00:00Z",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.org",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.org",
    }
    (work / "a.py").write_text(f"{OLD}\n")
    for args in (["init", "-q", "-b", "master"], ["add", "-A"], ["commit", "-q", "-m", "c"]):
        subprocess.run(["git", "-C", str(work), *args], env=env, check=True)
    bare = tmp_path / "srv" / "openstack" / "nova.git"
    subprocess.run(["git", "clone", "--bare", "-q", str(work), str(bare)], check=True)
    return bare


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bare = _repo(tmp_path)
    rows = {
        "dev": [
            {"id": "e1", "project": "openstack/nova", "before": "", "after": f"{OLD}\n"},
            {"id": "e2", "project": "openstack/nova", "before": "", "after": f"{NEW}\n"},
            {"id": "e3", "project": "openstack/nova", "before": "x", "after": "y"},
            {"id": "e4", "project": "openstack/gone", "before": "", "after": f"{OLD}\n"},
        ]
    }

    def split(root, org, *, eval_window, refuse_sealed_rows):
        return [], rows.get(eval_window, []), {}

    monkeypatch.setattr(script, "window_split", split)
    monkeypatch.setattr(script, "claim_host", lambda host: None)
    monkeypatch.setitem(
        lookup.SOURCES,
        "openstack",
        lambda project: [f"file://{bare.parent.parent}/{project}.git"],
    )
    # A local path that does not exist stands for a host's "not found".
    missing = lookup._missing
    monkeypatch.setattr(
        lookup,
        "_missing",
        lambda stderr: missing(stderr) or "does not appear to be a git" in stderr,
    )
    out = tmp_path / "out.json"

    def go(*extra: str) -> dict:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "backport_lookup",
                "--root",
                str(tmp_path / "corpus"),
                "--org",
                "openstack",
                "--clones",
                str(tmp_path / "clones"),
                "--out",
                str(out),
                *extra,
            ],
        )
        script.main()
        return json.loads(out.read_text())

    return go


def test_each_example_is_read_against_its_project_and_counted(run) -> None:
    report = run("--window", "dev")
    dev = report["windows"]["dev"]
    assert {k: dev[k] for k in script.CLASSES} == {
        "clean": 1,
        "suspicious": 0,
        "found": 1,
        "no_eligible_line": 1,
        "no_reference": 1,
    }
    assert dev["ids"] == {"found": ["e1"], "suspicious": []}
    assert report["projects"]["openstack/gone"] == {"source": None}
    assert report["projects"]["openstack/nova"]["branches"] == 1


def test_the_report_holds_no_line_of_code(run) -> None:
    text = json.dumps(run("--window", "dev"))
    assert OLD not in text and NEW not in text


def test_a_sealed_test_window_is_refused(run) -> None:
    with pytest.raises(SystemExit, match="sealed"):
        run("--window", "test")
