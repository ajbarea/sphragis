"""The backport lookup's lines, reference, classes and clones, on local repositories."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import pytest

from sphragis.corpus import lookup

LONG = "return self.client.get_server(server_id)"  # 40 characters, over the floor
OTHER = "raise exception.NotFound(resource=name)"
UPLOAD = "2024-09-17T16:13:45Z"


def _git(repo: Path, *args: str, when: str = "2024-01-01T00:00:00Z") -> str:
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "GIT_AUTHOR_DATE": when,
        "GIT_COMMITTER_DATE": when,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.org",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.org",
    }
    done = subprocess.run(["git", "-C", str(repo), *args], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


def _commit(repo: Path, files: dict[str, bytes], when: str) -> None:
    for name, data in files.items():
        (repo / name).write_bytes(data)
    _git(repo, "add", "-A", when=when)
    _git(repo, "commit", "-q", "-m", "c", when=when)


def _bare(work: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "--bare", "-q", str(work), str(dest)], check=True)
    return dest


@pytest.fixture
def work(tmp_path: Path) -> Path:
    """main: LONG before the upload, OTHER after; stable/1: OTHER before; late: off main after."""
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    files = {"a.py": f"{LONG}\n".encode(), "img.bin": b"\0" + OTHER.encode()}
    _commit(work, files, "2024-03-01T00:00:00Z")
    _git(work, "branch", "stable/1")
    _git(work, "checkout", "-q", "stable/1")
    _commit(work, {"b.py": f"{OTHER}\n".encode()}, "2024-06-01T00:00:00Z")
    _git(work, "checkout", "-q", "main")
    _commit(work, {"c.py": f"{OTHER}  # later\n".encode()}, "2025-01-01T00:00:00Z")
    _git(work, "checkout", "-q", "-b", "late")
    _commit(work, {"d.py": b"x = 1\n"}, "2025-02-01T00:00:00Z")
    _git(work, "checkout", "-q", "main")
    return work


@pytest.fixture
def repo(work: Path, tmp_path: Path) -> Path:
    return _bare(work, tmp_path / "bare.git")


def test_added_lines_are_after_less_before_once_and_long_enough() -> None:
    before = f"  {LONG}\nshort\n"
    after = f"{LONG}\n{LONG}\nshort\n  {OTHER}  \nx = 1\n"
    # One copy of LONG was there before; the second is added. `short` and `x = 1` are under 20.
    assert lookup.added_lines(before, after) == [LONG, OTHER]


@pytest.mark.parametrize(
    ("eta", "cls"),
    [(0.0, "clean"), (0.19, "clean"), (0.2, "suspicious"), (0.79, "suspicious"), (0.8, "found")],
)
def test_classes_are_infini_gram_minis(eta: float, cls: str) -> None:
    assert lookup.classify(eta) == cls


def test_branch_tips_are_each_branch_before_the_upload(repo: Path) -> None:
    tips = lookup.branch_tips(repo, before=UPLOAD)
    # `late` branches off main after the upload, so its tip before then is main's.
    assert set(tips) == {"refs/heads/main", "refs/heads/stable/1", "refs/heads/late"}
    assert tips["refs/heads/main"] == tips["refs/heads/late"] != tips["refs/heads/stable/1"]


def test_a_side_branch_merged_after_the_upload_is_not_the_tip(work: Path, tmp_path: Path) -> None:
    """A side commit dated before the upload but merged after it was not on the branch then."""
    _git(work, "checkout", "-q", "-b", "side", "stable/1")
    _commit(work, {"side.py": f"{LONG} # side\n".encode()}, "2024-09-17T15:00:00Z")
    _git(work, "checkout", "-q", "stable/1")
    _commit(work, {"m.py": b"mainline = True\n"}, "2024-09-10T00:00:00Z")
    before_merge = _git(work, "rev-parse", "HEAD").strip()
    _git(work, "merge", "-q", "--no-ff", "-m", "merge", "side", when="2024-09-19T00:00:00Z")
    repo = _bare(work, tmp_path / "merged.git")
    assert lookup.branch_tips(repo, before=UPLOAD)["refs/heads/stable/1"] == before_merge


def test_an_end_of_life_tag_stands_for_its_deleted_branch(work: Path, tmp_path: Path) -> None:
    _git(work, "tag", "1.0-eol", "stable/1")
    _git(work, "tag", "1.0.0", "stable/1")
    _git(work, "branch", "-D", "stable/1")
    tips = lookup.branch_tips(_bare(work, tmp_path / "eol.git"), before=UPLOAD)
    assert "refs/tags/1.0-eol" in tips and "refs/tags/1.0.0" not in tips


def test_a_branch_born_entirely_after_the_upload_is_skipped(repo: Path, tmp_path: Path) -> None:
    orphan = tmp_path / "orphan"
    subprocess.run(["git", "clone", "-q", str(repo), str(orphan)], check=True)
    _git(orphan, "checkout", "-q", "--orphan", "new")
    _git(orphan, "rm", "-rq", "--cached", ".")
    _commit(orphan, {"n.py": b"y = 2\n"}, "2025-03-01T00:00:00Z")
    _git(orphan, "push", "-q", "origin", "new")
    assert "refs/heads/new" not in lookup.branch_tips(repo, before=UPLOAD)


def test_reference_reads_text_files_of_every_tip_and_skips_binaries(repo: Path) -> None:
    tips = lookup.branch_tips(repo, before=UPLOAD)
    lines, files = lookup.reference_lines(repo, tips.values())
    assert hash(LONG) in lines and hash(OTHER) in lines  # OTHER from stable/1's text file
    assert hash(f"{OTHER}  # later") not in lines  # written after the upload
    assert files == 3  # a.py, img.bin, b.py: each distinct blob once


def test_binary_file_lines_are_not_read(repo: Path) -> None:
    main = lookup.branch_tips(repo, before=UPLOAD)["refs/heads/main"]
    lines, _ = lookup.reference_lines(repo, [main])
    assert hash(OTHER) not in lines  # only in img.bin, which holds a NUL


def test_a_symlink_is_not_a_file(work: Path, tmp_path: Path) -> None:
    _git(work, "checkout", "-q", "stable/1")
    (work / "link").symlink_to(f"some/long/target/path/{LONG}")
    _git(work, "add", "-A", when="2024-07-01T00:00:00Z")
    _git(work, "commit", "-q", "-m", "l", when="2024-07-01T00:00:00Z")
    repo = _bare(work, tmp_path / "link.git")
    lines, files = lookup.reference_lines(repo, lookup.branch_tips(repo, before=UPLOAD).values())
    assert hash(f"some/long/target/path/{LONG}") not in lines
    assert files == 3


def test_reading_classifies_and_never_calls_no_line_clean() -> None:
    reference = {hash(LONG)}
    row = {"id": "x", "before": "", "after": f"{LONG}\n{OTHER}\n"}
    assert lookup.reading(row, reference) == {
        "id": "x",
        "eligible": 2,
        "found": 1,
        "eta": 0.5,
        "class": "suspicious",
    }
    assert lookup.reading({"id": "y", "before": "a", "after": "b"}, reference)["class"] == (
        "no_eligible_line"
    )
    assert lookup.reading(row, None)["class"] == "no_reference"


def test_sources_resolve_in_the_registered_order_on_permitted_hosts() -> None:
    assert lookup.SOURCES["qt"]("yocto/meta-boot2qt") == [
        "https://github.com/yocto/meta-boot2qt.git",
        "https://github.com/qtproject/yocto-meta-boot2qt.git",
        "https://github.com/qtproject/meta-boot2qt.git",
    ]
    assert lookup.SOURCES["openstack"]("openstack/nova") == [
        "https://opendev.org/openstack/nova.git"
    ]
    assert lookup.SOURCES["wikimedia"]("mediawiki/core") == [
        "https://gerrit.wikimedia.org/r/mediawiki/core"
    ]
    hosts = {urlparse(url).netloc for source in lookup.SOURCES.values() for url in source("a/b")}
    assert hosts <= set(lookup.LOOKUP_PERMITTED)


@pytest.mark.parametrize(
    "name", ["", "/etc/x", "-upload-pack=x", "a/../b", "a+b", "a?b", "a#b", "a%2e"]
)
def test_a_project_name_that_is_not_plainly_a_path_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match="refusing"):
        lookup.safe_project(name)


def test_a_plain_project_name_passes() -> None:
    assert lookup.safe_project("mediawiki/extensions/Wikibase") == "mediawiki/extensions/Wikibase"


class FakeClaim:
    def __init__(self, quiet: float = 0.0) -> None:
        self.quiet = quiet
        self.held: list[float] = []

    def quiet_until(self) -> float:
        return self.quiet

    def hold_until(self, deadline: float) -> None:
        self.held.append(deadline)


def _cloner(
    tmp_path: Path, claim: FakeClaim, now: list[float], slept: list[float]
) -> lookup.Cloner:
    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    return lookup.Cloner(
        tmp_path / "clones",
        user_agent="t",
        claim=lambda host: claim,
        sleep=sleep,
        clock=lambda: now[0],
    )


def test_cloner_clones_once_and_paces_from_the_end_of_the_last(repo: Path, tmp_path: Path) -> None:
    now, slept = [100.0], []
    cloner = _cloner(tmp_path, FakeClaim(), now, slept)
    first = cloner.clone(f"file://{repo}")
    assert first is not None and (first / "HEAD").is_file()
    assert cloner.clone(f"file://{repo}") == first
    now[0] += 0.5  # the next clone is asked for half a second after the first ended
    other = _bare(repo, tmp_path / "other.git")
    cloner.clone(f"file://{other}")
    assert slept == [pytest.approx(lookup.CLONE_INTERVAL - 0.5)]


def test_cloner_waits_out_the_hosts_quiet_deadline(repo: Path, tmp_path: Path) -> None:
    now, slept = [100.0], []
    _cloner(tmp_path, FakeClaim(quiet=160.0), now, slept).clone(f"file://{repo}")
    assert slept == [pytest.approx(60.0)]


def test_cloner_refuses_a_host_not_permitted(tmp_path: Path) -> None:
    with pytest.raises(PermissionError, match="example.com"):
        _cloner(tmp_path, FakeClaim(), [0.0], []).clone("https://example.com/x.git")


def test_clones_read_no_user_config(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user's URL rewrite (or credential helper) must not reach the clone."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        f'[url "file://{tmp_path}/nowhere/"]\n\tinsteadOf = file://{repo}\n'
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    assert _cloner(tmp_path, FakeClaim(), [0.0], []).clone(f"file://{repo}") is not None


@pytest.mark.parametrize(
    ("stderr", "missing"),
    [
        (
            "remote: Repository not found.\nfatal: repository "
            "'https://github.com/qtproject/x.git/' not found",
            True,
        ),
        (
            "fatal: could not read Username for 'https://opendev.org': terminal prompts disabled",
            True,
        ),
        (
            "fatal: unable to access 'https://gerrit.wikimedia.org/r/x/': "
            "The requested URL returned error: 403",
            False,
        ),
        ("fatal: unable to access 'https://opendev.org/x.git/': Could not resolve host", False),
    ],
)
def test_a_missing_repository_is_told_from_a_failure(stderr: str, missing: bool) -> None:
    assert lookup._missing(stderr) is missing


def test_a_server_error_holds_the_host_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim = FakeClaim()
    cloner = _cloner(tmp_path, claim, [1000.0], [])

    def fail(args, **kwargs):
        return subprocess.CompletedProcess(args, 128, "", "error: 503 while accessing")

    monkeypatch.setattr(lookup.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="clone of"):
        cloner.clone("https://opendev.org/openstack/nova.git")
    assert claim.held == [pytest.approx(1000.0 + lookup.QUIET_AFTER_ERROR)]


def test_a_failed_clone_that_is_not_a_missing_repository_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="clone of"):
        _cloner(tmp_path, FakeClaim(), [0.0], []).clone(f"file://{tmp_path}/does-not-exist.git")


def test_cloner_clears_a_partial_clone_left_inside_its_root(repo: Path, tmp_path: Path) -> None:
    cloner = _cloner(tmp_path, FakeClaim(), [0.0], [])
    url = f"file://{repo}"
    partial = cloner.path(url).with_name(cloner.path(url).name + ".partial")
    partial.mkdir(parents=True)
    (partial / "junk").write_text("x")
    assert cloner.clone(url) is not None
    assert not partial.exists()
