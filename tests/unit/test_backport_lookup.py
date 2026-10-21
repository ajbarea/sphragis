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

#: Unrelated lines, so dedup's near-duplicate rule keeps every example apart.
LINES = {
    1: "return self.client.get_server(server_id)",
    2: "raise exception.NotFound(resource=name)",
    3: "for volume in context.session.query(Volume):",
    4: "logger.warning('quota exceeded for %s', tenant)",
    5: "with open(config_path, encoding='utf-8') as handle:",
    6: "yield from self._paginate(request, page_size=limit)",
}
UNSEEN = {n: f"{line}  # rewritten after the upload" for n, line in LINES.items()}


def old(n: int) -> str:
    """A line the repository held before the upload."""
    return LINES[n]


def new(n: int) -> str:
    """A line it never held."""
    return UNSEEN[n]


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
    (work / "a.py").write_text("".join(f"{line}\n" for line in LINES.values()))
    for args in (["init", "-q", "-b", "master"], ["add", "-A"], ["commit", "-q", "-m", "c"]):
        subprocess.run(["git", "-C", str(work), *args], env=env, check=True)
    bare = tmp_path / "srv" / "openstack" / "nova.git"
    subprocess.run(["git", "clone", "--bare", "-q", str(work), str(bare)], check=True)
    return bare


def _row(n: int, project: str, created: str, after: str, before: str = "") -> dict:
    change = f"I{n:040d}"
    return {
        "id": f"openstack:{change}:a.py:1:{n}",
        "org": "openstack",
        "project": project,
        "change_id": change,
        "change_number": n,
        "created": created,
        "path": "a.py",
        "patch_set": 1,
        "before": before,
        "after": after,
        "context_before": f"ctx {n}",
        "context_after": "",
        "comments": [f"comment {n}"],
    }


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bare = _repo(tmp_path)
    examples = [
        _row(1, "openstack/nova", "2025-09-10 00:00:00.000000000", f"{old(1)}\n"),
        _row(2, "openstack/nova", "2025-09-11 00:00:00.000000000", f"{new(2)}\n"),
        _row(3, "openstack/nova", "2025-09-12 00:00:00.000000000", "y = 3", "x = 3"),
        _row(4, "openstack/gone", "2025-09-13 00:00:00.000000000", f"{old(4)}\n"),
        _row(5, "openstack/nova", "2024-10-10 00:00:00.000000000", f"{old(5)}\n"),
        _row(6, "openstack/nova", "2025-01-10 00:00:00.000000000", f"{new(6)}\n"),
    ]
    # The real dedup and split read these, as the runner's loader does.
    monkeypatch.setattr(lookup, "refined_examples", lambda root, org: examples)
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


def test_the_default_windows_are_read_through_the_real_split(run) -> None:
    report = run()
    assert {w: report["windows"][w]["examples"] for w in report["windows"]} == {
        "pilot": 1,
        "train": 1,
        "dev": 4,
    }
    assert report["windows"]["pilot"]["ids"]["found"] == [f"openstack:I{5:040d}:a.py:1:5"]


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
    assert dev["ids"] == {"found": [f"openstack:I{1:040d}:a.py:1:1"], "suspicious": []}
    assert report["projects"]["openstack/gone"] == {"source": None}
    assert report["projects"]["openstack/nova"]["branches"] == 1


def test_the_report_holds_no_line_of_code(run) -> None:
    text = json.dumps(run("--window", "dev"))
    assert not any(line in text for line in (*LINES.values(), *UNSEEN.values()))


def test_a_sealed_test_window_is_refused(run) -> None:
    with pytest.raises(SystemExit, match="sealed"):
        run("--window", "test")
