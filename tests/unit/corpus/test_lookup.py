"""The backport lookup's lines, reference, classes and clones, on local repositories."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from sphragis.corpus import lookup

LONG = "return self.client.get_server(server_id)"  # 40 characters, over the floor
OTHER = "raise exception.NotFound(resource=name)"


def _git(repo: Path, *args: str, when: str = "2024-01-01T00:00:00Z") -> str:
    env = {
        **os.environ,
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


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """main: LONG before the upload, OTHER after; stable/1: OTHER before; late: created after."""
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _commit(
        work,
        {"a.py": f"{LONG}\n".encode(), "img.bin": b"\0" + OTHER.encode()},
        "2024-03-01T00:00:00Z",
    )
    _git(work, "branch", "stable/1")
    _git(work, "checkout", "-q", "stable/1")
    _commit(work, {"b.py": f"{OTHER}\n".encode()}, "2024-06-01T00:00:00Z")
    _git(work, "checkout", "-q", "main")
    _commit(work, {"c.py": f"{OTHER}  # later\n".encode()}, "2025-01-01T00:00:00Z")
    _git(work, "checkout", "-q", "-b", "late")
    _commit(work, {"d.py": b"x = 1\n"}, "2025-02-01T00:00:00Z")
    _git(work, "checkout", "-q", "main")
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "--bare", "-q", str(work), str(bare)], check=True)
    return bare


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
    tips = lookup.branch_tips(repo, before="2024-09-17T16:13:45Z")
    # `late` branches off main after the upload, so its tip before then is main's: kept, the
    # same commit. A branch is skipped only when it has no commit before the upload at all.
    assert set(tips) == {"main", "stable/1", "late"}
    assert tips["main"] == tips["late"] != tips["stable/1"]


def test_a_branch_born_entirely_after_the_upload_is_skipped(repo: Path, tmp_path: Path) -> None:
    orphan = tmp_path / "orphan"
    subprocess.run(["git", "clone", "-q", str(repo), str(orphan)], check=True)
    _git(orphan, "checkout", "-q", "--orphan", "new")
    _git(orphan, "rm", "-rq", "--cached", ".")
    _commit(orphan, {"n.py": b"y = 2\n"}, "2025-03-01T00:00:00Z")
    _git(orphan, "push", "-q", "origin", "new")
    assert "new" not in lookup.branch_tips(repo, before="2024-09-17T16:13:45Z")


def test_reference_reads_text_files_of_every_tip_and_skips_binaries(repo: Path) -> None:
    tips = lookup.branch_tips(repo, before="2024-09-17T16:13:45Z")
    lines, files = lookup.reference_lines(repo, tips.values())
    assert LONG in lines and OTHER in lines  # OTHER from stable/1's text file
    assert f"{OTHER}  # later" not in lines  # written after the upload
    assert files == 3  # a.py, img.bin, b.py: each distinct blob once


def test_binary_file_lines_are_not_read(repo: Path) -> None:
    main_only = lookup.branch_tips(repo, before="2024-09-17T16:13:45Z")["main"]
    lines, _ = lookup.reference_lines(repo, [main_only])
    assert OTHER not in lines  # only in img.bin, which holds a NUL


def test_reading_classifies_and_never_calls_no_line_clean() -> None:
    reference = {LONG}
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


def test_qt_resolves_in_the_registered_order() -> None:
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


def test_cloner_claims_paces_and_clones_once(repo: Path, tmp_path: Path) -> None:
    claimed: list[str] = []
    slept: list[float] = []
    now = [100.0]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    cloner = lookup.Cloner(
        tmp_path / "clones",
        user_agent="test",
        claim=claimed.append,
        sleep=sleep,
        clock=lambda: now[0],
    )
    url = f"file://{repo}"
    first = cloner.clone(url)
    assert first is not None and (first / "HEAD").is_file()
    assert cloner.clone(url) == first  # held: no second clone, no claim
    assert claimed == [""]  # file:// has no host; the claim is still taken
    other = tmp_path / "other.git"
    subprocess.run(["git", "clone", "--bare", "-q", str(repo), str(other)], check=True)
    cloner.clone(f"file://{other}")
    assert slept == [pytest.approx(lookup.CLONE_INTERVAL)]


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


def test_a_failed_clone_that_is_not_a_missing_repository_raises(tmp_path: Path) -> None:
    cloner = lookup.Cloner(tmp_path / "clones", user_agent="t", claim=lambda h: None)
    with pytest.raises(RuntimeError, match="clone of"):
        cloner.clone(f"file://{tmp_path}/does-not-exist.git")


def test_cloner_clears_a_partial_clone_left_inside_its_root(repo: Path, tmp_path: Path) -> None:
    cloner = lookup.Cloner(tmp_path / "clones", user_agent="t", claim=lambda h: None)
    url = f"file://{repo}"
    partial = cloner.path(url).with_name(cloner.path(url).name + ".partial")
    partial.mkdir(parents=True)
    (partial / "junk").write_text("x")
    assert cloner.clone(url) is not None
    assert not partial.exists()
