"""AOSP's projects by language at its release tag: the code-file rule and the tree read."""

from __future__ import annotations

import importlib.util
import json
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


@pytest.mark.parametrize(
    "node",
    ["include", "remove-project", "extend-project", "submanifest", 'project name="y"><project'],
)
def test_a_manifest_that_reaches_other_files_is_refused(node: str) -> None:
    nested = "</project>" if node.startswith("project") else ""
    xml = (f'<manifest><default revision="r" /><{node} name="x" />{nested}</manifest>').encode()
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


class _Manifest:
    """The manifest repository's two local reads, with no fetch behind them."""

    def __init__(self, xml: bytes) -> None:
        self.xml = xml

    def git(self, *args: str) -> bytes:
        return self.xml if args[0] == "show" else b"0" * 40 + b"\n"


def _census(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, xml: bytes, fail) -> Path:
    """`run_census` over a manifest with no network: `fail(project)` raises for a project or
    returns the paths of its tree."""
    fetched: list[str] = []

    class Tree:
        def __init__(self, paths: list[str]) -> None:
            self.paths = paths

    def fetch_tag(scratch, project, pacer, ledger, *, blobless):
        fetched.append(project)
        return _Manifest(xml) if not blobless else Tree(fail(project))

    monkeypatch.setattr(census, "fetch_tag", fetch_tag)
    monkeypatch.setattr(census, "blobs", lambda repo: repo.paths)
    code_files = tmp_path / "code.json"
    if not code_files.exists():
        code_files.write_text(json.dumps({"source": {}, **census.code_files(LANGUAGES)}))
    out = tmp_path / "census.json"
    args = census.parser.parse_args(
        [
            "census",
            "--code-files",
            str(code_files),
            "--scratch",
            str(tmp_path / "s"),
            "--ledger",
            str(tmp_path / "ledger.jsonl"),
            "--out",
            str(out),
        ]
    )
    census.run_census(args)
    return out


MANIFEST = b"""<manifest>
  <default revision="refs/tags/android-15.0.0_r1" remote="aosp" />
  <project name="../escape" />
  <project name="platform/off" revision="main" />
  <project name="platform/elsewhere" remote="mirror" />
  <project name="platform/gone" />
  <project name="platform/read" />
</manifest>"""


def _tree(project: str) -> list[str]:
    if project == "platform/gone":
        raise notedb.GitError("fatal: couldn't find remote ref refs/tags/android-15.0.0_r1")
    return ["a.cpp", "b.h", "c.java"]


def test_a_project_off_the_tag_named_outside_its_directory_or_tagless_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _census(tmp_path, monkeypatch, MANIFEST, _tree)
    report = json.loads(out.read_text())
    rows = {r["name"]: r for r in report["rows"]}
    assert "outside its own repository" in rows["../escape"]["not_read"]
    assert "revision" in rows["platform/off"]["not_read"]
    assert "remote" in rows["platform/elsewhere"]["not_read"]
    assert "holds no" in rows["platform/gone"]["not_read"]
    assert rows["platform/read"]["cpp_majority"] is True
    assert report["cpp_majority"] == ["platform/read"]
    assert (tmp_path / "s" / "progress.jsonl").is_file()


def test_a_failed_fetch_stops_the_census_and_is_asked_again_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def flaky(project: str) -> list[str]:
        if project == "platform/read":
            raise notedb.GitError("fatal: unable to access: The requested URL returned error: 429")
        return _tree(project)

    with pytest.raises(SystemExit, match="census stopped"):
        _census(tmp_path, monkeypatch, MANIFEST, flaky)
    assert not (tmp_path / "census.json").exists()
    progress = (tmp_path / "s" / "progress.jsonl").read_text()
    assert "platform/read" not in progress and "platform/gone" in progress
    out = _census(tmp_path, monkeypatch, MANIFEST, _tree)
    assert json.loads(out.read_text())["cpp_majority"] == ["platform/read"]


def test_progress_counted_under_another_definition_is_not_resumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _census(tmp_path, monkeypatch, MANIFEST, _tree)
    (tmp_path / "code.json").write_text(
        json.dumps({"source": {"release": "other"}, "suffixes": [], "filenames": []})
    )
    with pytest.raises(SystemExit, match="was counted under"):
        _census(tmp_path, monkeypatch, MANIFEST, _tree)


def test_a_census_that_reads_nothing_or_another_tag_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nothing = b"""<manifest><default revision="refs/tags/android-15.0.0_r1" remote="aosp" />
      <project name="platform/off" revision="main" /></manifest>"""
    with pytest.raises(SystemExit, match="no project was read"):
        _census(tmp_path, monkeypatch, nothing, _tree)
    other = MANIFEST.replace(b"android-15.0.0_r1", b"android-14.0.0_r1")
    with pytest.raises(SystemExit, match="default revision"):
        census.manifest_projects(other)


def test_a_dotted_extension_matches_the_end_of_the_name() -> None:
    rule = {"suffixes": [".cpp", ".h", ".h.in"], "filenames": []}
    counted = census.classify(["config.h.in", "a.cpp", "notes.in"], rule)
    assert (counted["code_files"], counted["cpp_files"]) == (2, 1)


def test_a_host_that_sends_blobs_to_a_blobless_fetch_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Sent:
        fetched = False

        def fetch(self, *args: object) -> None:
            self.fetched = True

        def git(self, *args: str) -> bytes:
            return b"commit\ntree\nblob\n"

    sent = Sent()
    monkeypatch.setattr(census.Repo, "open", staticmethod(lambda *a: sent))
    with pytest.raises(notedb.GitError, match="sent blobs"):
        census.fetch_tag(tmp_path, "platform/x", Pacer(0), tmp_path / "l", blobless=True)
    assert sent.fetched


def test_a_manifest_listing_a_project_twice_is_refused() -> None:
    xml = b"""<manifest><default revision="refs/tags/android-15.0.0_r1" remote="aosp" />
      <project name="platform/a" path="a" /><project name="platform/a" path="b" /></manifest>"""
    with pytest.raises(SystemExit, match="twice"):
        census.manifest_projects(xml)
