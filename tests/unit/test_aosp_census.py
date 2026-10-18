"""AOSP's projects by language at its release tag: the code-file rule and the tree read."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from sphragis.corpus import notedb
from sphragis.corpus.pacing import Pacer

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("aosp_census", ROOT / "scripts" / "aosp_census.py")
assert _spec is not None and _spec.loader is not None
census = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(census)

LANGUAGES = {
    "C++": {
        "type": "programming",
        "extensions": [".cpp", ".cc", ".cxx", ".c++", ".h", ".hh", ".hpp", ".hxx", ".inl"],
    },
    "Java": {"type": "programming", "extensions": [".java"]},
    "Makefile": {"type": "programming", "extensions": [".mak"], "filenames": ["Makefile"]},
    "CMake": {"type": "programming", "extensions": [".cmake"], "filenames": ["CMakeLists.txt"]},
    "GCC Machine Description": {"type": "programming", "extensions": [".md"]},
    "Markdown": {"type": "prose", "extensions": [".md", ".markdown"]},
    "XML": {"type": "data", "extensions": [".xml", ".rs"]},
    "Rust": {"type": "programming", "extensions": [".rs"]},
}


def test_a_suffix_counts_unless_another_type_holds_it_as_its_primary() -> None:
    rule = census.code_files(LANGUAGES)
    assert ".md" not in rule["suffixes"]
    assert ".rs" in rule["suffixes"]
    assert ".java" in rule["suffixes"]
    assert rule["filenames"] == ["CMakeLists.txt", "Makefile"]


def test_a_definition_that_drops_a_cpp_suffix_is_refused() -> None:
    languages = {**LANGUAGES, "Hack": {"type": "data", "extensions": [".hh"]}}
    with pytest.raises(SystemExit, match=r"\.hh"):
        census.code_files(languages)


def test_cpp_majority_is_more_than_half_of_code_files() -> None:
    rule = census.code_files(LANGUAGES)
    tree = ["a.cpp", "b.h", "C.java", "README.md", "Makefile", "x/CMakeLists.txt", "res/a.xml"]
    counted = census.classify(tree, rule)
    assert counted == {
        "files": 7,
        "code_files": 5,
        "cpp_files": 2,
        "cpp_share": 0.4,
        "cpp_majority": False,
    }
    assert census.classify(["a.cpp", "b.java"], rule)["cpp_majority"] is False
    assert census.classify(["a.cpp", "b.H", "b.java"], rule)["cpp_majority"] is True
    assert census.classify(["README.md"], rule)["cpp_majority"] is False


def test_the_manifest_gives_each_project_its_default_revision_and_remote() -> None:
    xml = b"""<manifest>
      <remote name="aosp" fetch=".." />
      <default revision="refs/tags/android-15.0.0_r1" remote="aosp" />
      <project path="build/make" name="platform/build" groups="pdk" />
      <project name="platform/bionic" revision="main" />
      <project name="other/x" remote="mirror" />
    </manifest>"""
    projects, remote = census.manifest_projects(xml)
    assert remote == "aosp"
    assert projects[0] == {
        "name": "platform/build",
        "path": "build/make",
        "revision": notedb.AOSP_CENSUS_TAG,
        "remote": "aosp",
        "groups": "pdk",
    }
    assert projects[1]["revision"] == "main"
    assert projects[2]["remote"] == "mirror"


@pytest.mark.parametrize("node", ["include", "remove-project", "extend-project"])
def test_a_manifest_that_reaches_other_files_is_refused(node: str) -> None:
    xml = f'<manifest><default revision="r" /><{node} name="x" /></manifest>'.encode()
    with pytest.raises(SystemExit, match="read those first"):
        census.manifest_projects(xml)


def _git(*args: str, cwd: Path) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.org",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.org",
    }
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def test_the_tree_is_read_blobless_and_counts_files_not_links(tmp_path: Path) -> None:
    source = tmp_path / "src"
    (source / "lib").mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=source)
    _git("config", "uploadpack.allowFilter", "true", cwd=source)
    (source / "lib" / "a.cpp").write_text("int a;\n")
    (source / "B.java").write_text("class B {}\n")
    (source / "link.cpp").symlink_to("lib/a.cpp")
    _git("add", ".", cwd=source)
    _git("commit", "-q", "-m", "x", cwd=source)
    _git("tag", "-a", "android-15.0.0_r1", "-m", "release", cwd=source)
    repo = notedb.Repo.open(tmp_path / "c.git", f"file://{source}", Pacer(0))
    repo.fetch("census tree", [census.REFSPEC], "--depth=1", "--filter=blob:none")
    assert sorted(census.blobs(repo)) == ["B.java", "lib/a.cpp"]
    held = repo.git("cat-file", "--batch-all-objects", "--batch-check=%(objecttype)").decode()
    assert "blob" not in held.split()
