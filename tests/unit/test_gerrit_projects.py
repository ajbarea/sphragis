"""A host's project list: its code projects by name, the host's meta projects aside."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "gerrit_projects", ROOT / "scripts" / "gerrit_projects.py"
)
assert _spec is not None and _spec.loader is not None
projects = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(projects)


def test_the_list_keeps_code_projects_and_drops_the_hosts_own() -> None:
    listed = {
        "fuchsia": {"state": "ACTIVE"},
        "third_party/old": {"state": "READ_ONLY"},
        "All-Projects": {"state": "ACTIVE"},
        "All-Users": {"state": "ACTIVE"},
        "secret": {"state": "HIDDEN"},
    }
    assert projects.code_projects(listed) == ["fuchsia", "third_party/old"]


def test_the_artifact_records_the_query_and_every_state(tmp_path, monkeypatch) -> None:
    body = ")]}'\n" + json.dumps({"fuchsia": {"state": "ACTIVE"}, "a/b": {"state": "READ_ONLY"}})
    asked: list[str] = []
    monkeypatch.setattr(projects, "http_transport", lambda: "transport")
    monkeypatch.setattr(projects, "_get", lambda url, *_: asked.append(url) or body)
    out = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv", ["gerrit_projects", "--org", "fuchsia", "--out", str(out)])
    projects.main()
    report = json.loads(out.read_text())
    assert asked == ["https://fuchsia-review.googlesource.com/projects/?type=CODE"]
    assert report["projects"] == ["a/b", "fuchsia"]
    assert report["states"] == {"ACTIVE": 1, "READ_ONLY": 1}
    assert report["host"] == "fuchsia-review.googlesource.com"
