"""AOSP's projects by language at its release tag, for H2's foreign pool (item 4 of the H2 plan).

    code-files  the code-file definition, from a pinned GitHub Linguist `languages.yml`
    census      every project in the manifest at `notedb.AOSP_CENSUS_TAG`, its tree's files
                counted: code files, and C++ files by `decomposition.CPP_SUFFIXES`

A code file is one whose lowercased suffix some Linguist language of type `programming` lists,
unless a language of another type lists it as its own primary extension (Markdown's `.md`,
SQL's `.sql`), or whose name Linguist lists as a programming language's filename (`Makefile`,
`CMakeLists.txt`). A project is C++-majority when C++ files are more than half its code files.
Symlinks and submodule entries are not files. Reads trees only, over git, never a blob or a
review record: each project is a depth-1, blob-less fetch of the tag, the only ref
`GIT_PERMITTED` opens on the host (the manifest repository, whose `default.xml` is read, is
fetched whole), paced at the host's floor and ledgered. A project whose name could leave its
scratch directory, or whose manifest revision or remote is not the default's, is recorded and
not read, as is one the host holds no tag for; any other fetch failure stops the run, and the
next run asks that project again. Resumes from `--progress`, refusing rows counted under another
tag, manifest or code-file definition. `code-files` reads YAML with PyYAML, which the
experiment extra's tree installs.

    uv run --no-sync --no-active python scripts/aosp_census.py code-files \\
        --languages <linguist>/lib/linguist/languages.yml --tag v9.7.0 \\
        --out datasets/results/code-files-linguist.json
    uv run --no-sync --no-active python scripts/aosp_census.py census \\
        --code-files datasets/results/code-files-linguist.json \\
        --scratch ~/ajsoftworks/sphragis-data-local/aosp/census \\
        --ledger datasets/logs/aosp-census/ledger.jsonl \\
        --out datasets/results/aosp-census.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any

from sphragis.corpus.cli import _git_pacer
from sphragis.corpus.halves import suffix
from sphragis.corpus.notedb import AOSP_CENSUS_TAG, GIT_HOSTS, GitError, Repo, valid_project_name
from sphragis.corpus.pacing import Pacer
from sphragis.experiment.decomposition import CPP_SUFFIXES
from sphragis.provenance import provenance_header

HOST = GIT_HOSTS["aosp"]
MANIFEST_PROJECT = "platform/manifest"
REFSPEC = f"+{AOSP_CENSUS_TAG}:{AOSP_CENSUS_TAG}"
SYMLINK, GITLINK = "120000", "160000"
LINGUIST = "https://github.com/github-linguist/linguist"
#: git's answer when a project has no such tag: the one fetch failure that is about the project.
MISSING_TAG = re.compile(r"couldn't find remote ref")

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
sub = parser.add_subparsers(dest="command", required=True)
code = sub.add_parser("code-files")
code.add_argument("--languages", type=Path, required=True, help="Linguist's languages.yml")
code.add_argument("--tag", required=True, help="the Linguist release it was read at")
code.add_argument("--out", type=Path, required=True)
census = sub.add_parser("census")
census.add_argument("--code-files", type=Path, required=True)
census.add_argument(
    "--scratch", type=Path, required=True, help="scratch repositories, each removed once read"
)
census.add_argument("--ledger", type=Path, required=True)
census.add_argument("--progress", type=Path, help="one line a project read; default in --scratch")
census.add_argument("--out", type=Path, required=True)


def code_files(languages: dict[str, Any]) -> dict[str, list[str]]:
    """The suffixes and file names that make a file a code file, by the rule above."""
    programming = {k: v for k, v in languages.items() if v.get("type") == "programming"}
    others = [v for v in languages.values() if v.get("type") != "programming"]
    listed = {e.lower() for v in programming.values() for e in v.get("extensions", [])}
    primary_elsewhere = {v["extensions"][0].lower() for v in others if v.get("extensions")}
    names = {name for v in programming.values() for name in v.get("filenames", [])}
    missing = CPP_SUFFIXES - (listed - primary_elsewhere)
    if missing:
        raise SystemExit(f"the definition would not count C++ files {sorted(missing)} as code")
    return {"suffixes": sorted(listed - primary_elsewhere), "filenames": sorted(names)}


def classify(paths: list[str], definition: dict[str, list[str]]) -> dict[str, Any]:
    """A tree's file counts: all files, code files and C++ files, and whether C++ is a majority.

    A listed extension of more than one dot (`.h.in`, `.cmake.in`) is matched on the file name's
    end, as Linguist matches it; every other on the file's last suffix (`halves.suffix`)."""
    suffixes, names = set(definition["suffixes"]), set(definition["filenames"])
    dotted = tuple(e for e in suffixes if e.count(".") > 1)

    def is_code(path: str) -> bool:
        name = PurePosixPath(path).name
        return suffix(path) in suffixes or name in names or name.lower().endswith(dotted)

    coded = [p for p in paths if is_code(p)]
    cpp = sum(1 for p in coded if suffix(p) in CPP_SUFFIXES)
    return {
        "files": len(paths),
        "code_files": len(coded),
        "cpp_files": cpp,
        "cpp_share": cpp / len(coded) if coded else 0.0,
        "cpp_majority": 2 * cpp > len(coded),
    }


def manifest_projects(xml: bytes) -> tuple[list[dict[str, Any]], str | None]:
    """The manifest's projects, each with its revision and remote, its default's where it names
    none, and the default's remote. A default revision other than the tag is refused: the census
    reads that tag's trees alone."""
    root = ET.fromstring(xml)
    reaching = ("include", "remove-project", "extend-project", "submanifest", ".//project/project")
    if any(root.findall(tag) for tag in reaching):
        raise SystemExit(
            "the manifest includes, removes, extends or nests projects; read those first"
        )
    default = root.find("default")
    revision = default.get("revision") if default is not None else None
    remote = default.get("remote") if default is not None else None
    if revision != AOSP_CENSUS_TAG:
        raise SystemExit(f"the manifest's default revision is {revision!r}, not {AOSP_CENSUS_TAG}")
    projects = []
    for node in root.findall("project"):
        projects.append(
            {
                "name": node.get("name"),
                "path": node.get("path") or node.get("name"),
                "revision": node.get("revision") or revision,
                "remote": node.get("remote") or remote,
                "groups": node.get("groups") or "",
            }
        )
    return projects, remote


def blobs(repo: Repo) -> list[str]:
    """Every file path in the tag's tree: blobs that are not symlinks."""
    out = repo.git("ls-tree", "-r", "-z", "--full-tree", AOSP_CENSUS_TAG)
    paths = []
    for entry in out.decode("utf-8", "surrogateescape").split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        mode, kind, _ = meta.split(" ")
        if kind == "blob" and mode != SYMLINK:
            paths.append(path)
        elif kind not in ("blob", "commit") or (kind == "commit" and mode != GITLINK):
            raise GitError(f"unexpected tree entry {entry!r}")
    return paths


def fetch_tag(scratch: Path, project: str, pacer: Pacer, ledger: Path, *, blobless: bool) -> Repo:
    """The tag fetched at depth 1: the manifest repository whole (its `default.xml` is read),
    every other project's trees only, refused if the host sent a blob anyway."""
    repo = Repo.open(scratch / f"{project}.git", f"{HOST}/{project}", pacer, ledger)
    options = ("--depth=1", "--filter=blob:none") if blobless else ("--depth=1",)
    repo.fetch("census tree" if blobless else "census manifest", [REFSPEC], *options)
    if blobless:
        kinds = repo.git("cat-file", "--batch-all-objects", "--batch-check=%(objecttype)").split()
        if b"blob" in kinds:
            raise GitError(f"{project}: the host sent blobs to a blob-less fetch")
    return repo


def run_census(args: argparse.Namespace) -> None:
    definition = json.loads(args.code_files.read_text())
    pacer = _git_pacer(1.0)
    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    args.scratch.mkdir(parents=True, exist_ok=True)
    progress = args.progress or args.scratch / "progress.jsonl"
    manifest = fetch_tag(args.scratch, MANIFEST_PROJECT, pacer, args.ledger, blobless=False)
    xml = manifest.git("show", f"{AOSP_CENSUS_TAG}:default.xml")
    manifest_commit = manifest.git("rev-parse", f"{AOSP_CENSUS_TAG}^{{commit}}").decode().strip()
    projects, remote = manifest_projects(xml)
    # What a progress row was counted under; a row counted under another is never resumed from.
    under = {
        "tag": AOSP_CENSUS_TAG,
        "manifest_commit": manifest_commit,
        "code_files_sha256": hashlib.sha256(args.code_files.read_bytes()).hexdigest(),
    }
    done = {}
    if progress.exists():
        for line in progress.read_text().splitlines():
            row = json.loads(line)
            if row.get("under") != under:
                raise SystemExit(f"{progress} was counted under {row.get('under')}, not {under}")
            done[row["name"]] = row
    for number, project in enumerate(projects, 1):
        name = project["name"]
        if name in done:
            continue
        row: dict[str, Any] = {**project, "under": under}
        if not valid_project_name(name):
            row["not_read"] = "its name could resolve outside its own repository"
        elif project["revision"] != AOSP_CENSUS_TAG:
            row["not_read"] = f"its manifest revision is {project['revision']!r}"
        elif project["remote"] != remote:
            row["not_read"] = f"its manifest remote is {project['remote']!r}"
        else:
            try:
                repo = fetch_tag(args.scratch, name, pacer, args.ledger, blobless=True)
                row.update(classify(blobs(repo), definition))
            except GitError as error:
                shutil.rmtree(args.scratch / f"{name}.git", ignore_errors=True)
                if not MISSING_TAG.search(str(error)):
                    # Not an answer about the project, and the host may be refusing the client:
                    # the run stops here, and a later one asks this project again.
                    raise SystemExit(
                        f"{number}/{len(projects)} {name}: fetch failed, census stopped; rerun "
                        f"later to resume: {str(error)[-300:]}"
                    ) from error
                row["not_read"] = f"the host holds no {AOSP_CENSUS_TAG} for it"
            shutil.rmtree(args.scratch / f"{name}.git", ignore_errors=True)
        with progress.open("a") as handle:
            handle.write(json.dumps(row) + "\n")
        done[name] = row
        print(f"{number}/{len(projects)} {name}: {row.get('cpp_share', row.get('not_read'))}")
        sys.stdout.flush()
    rows = [done[p["name"]] for p in projects]
    if all("not_read" in r for r in rows):
        raise SystemExit("no project was read; no census is written")
    majority = [r for r in rows if r.get("cpp_majority")]
    report = {
        "host": HOST,
        "tag": AOSP_CENSUS_TAG,
        "manifest_commit": manifest_commit,
        "manifest_sha256": hashlib.sha256(xml).hexdigest(),
        "code_files": str(args.code_files),
        "code_files_source": definition["source"],
        "projects": len(rows),
        "not_read": sum(1 for r in rows if "not_read" in r),
        "cpp_majority": [r["name"] for r in majority],
        "rows": rows,
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"{len(rows)} projects, {report['not_read']} not read, {len(majority)} C++-majority; "
        f"wrote {args.out}"
    )


def main() -> None:
    args = parser.parse_args()
    if args.command == "code-files":
        import yaml

        raw = args.languages.read_bytes()
        report = {
            "source": {
                "file": "lib/linguist/languages.yml",
                "release": args.tag,
                "url": f"{LINGUIST}/blob/{args.tag}/lib/linguist/languages.yml",
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            **code_files(yaml.safe_load(raw)),
            "provenance": provenance_header(),
        }
        args.out.write_text(json.dumps(report, indent=2) + "\n")
        print(f"{len(report['suffixes'])} suffixes, {len(report['filenames'])} file names")
    else:
        run_census(args)


if __name__ == "__main__":
    main()
