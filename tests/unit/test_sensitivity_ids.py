"""scripts/sensitivity_ids.py: the examples each registered sensitivity of H1 removes."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "sensitivity_ids", ROOT / "scripts" / "sensitivity_ids.py"
)
assert _spec is not None and _spec.loader is not None
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

TRAILERS = {
    "since": "2024-10-01",
    "merged_before": "2025-11-01",
    "orgs": {"openstack": {"changes_ai": ["openstack/nova I1", "x/y z I2"]}},
}


def test_the_ai_flag_reads_project_and_change_id_from_the_trailer_artifact() -> None:
    assert script.flagged_changes(TRAILERS, "openstack") == {
        ("openstack/nova", "I1"),
        ("x/y z", "I2"),
    }


def test_the_trailer_searches_must_span_the_window() -> None:
    assert script.covers(TRAILERS, "dev")
    # Searched up to the test window's start, so no test-window change was ever flagged.
    assert not script.covers(TRAILERS, "test")


def _run(argv: list[str]) -> None:
    saved = sys.argv
    sys.argv = ["sensitivity_ids", *argv]
    try:
        script.main()
    finally:
        sys.argv = saved


def test_the_sealed_window_and_an_uncovered_window_are_refused(tmp_path: Path) -> None:
    trailers = tmp_path / "trailers.json"
    trailers.write_text(json.dumps(TRAILERS))
    argv = ["--root", str(tmp_path), "--org", "openstack", "--ai-trailers", str(trailers)]
    with pytest.raises(SystemExit, match="test window is sealed"):
        _run([*argv, "--window", "test", "--out", str(tmp_path / "o.json")])
    (tmp_path / "openstack").mkdir()
    (tmp_path / "openstack" / "seal.json").write_text(json.dumps({"accepted_at": "2027-02-01"}))
    with pytest.raises(SystemExit, match="rerun ai_trailers.py --through-test"):
        _run([*argv, "--window", "test", "--out", str(tmp_path / "o.json")])


def _corpus(root: Path, org: str) -> None:
    """Five development-window changes, refined as the build leaves them, and their raw changes.

    c1 AI-flagged; c2 only on a stable branch; c3 merged after the search bound; c4 unmerged;
    c5 ordinary.
    """
    import gzip
    import hashlib

    from sphragis.corpus.load import refined_dir, write_build_record, write_source_record

    rows = [
        {
            "id": f"{org}:c{n}:f.py:1",
            "change_id": f"c{n}",
            "project": "p",
            "org": org,
            "created": "2025-09-10 00:00:00.000000000",
            "comments": [f"comment {n} " + "word " * n],
            "before": f"before {n} " + "x" * n,
            "after": f"after {n} " + "y" * n,
        }
        for n in range(1, 6)
    ]
    merged = "2025-09-20 00:00:00.000000000"
    raw = [
        {
            "project": "p",
            "change_id": "c1",
            "branch": "master",
            "status": "MERGED",
            "submitted": merged,
        },
        {
            "project": "p",
            "change_id": "c2",
            "branch": "stable/2025.1",
            "status": "MERGED",
            "submitted": merged,
        },
        {
            "project": "p",
            "change_id": "c3",
            "branch": "master",
            "status": "MERGED",
            "submitted": "2025-11-03 00:00:00.000000000",
        },
        {"project": "p", "change_id": "c4", "branch": "master", "status": "NEW"},
        {
            "project": "p",
            "change_id": "c5",
            "branch": "master",
            "status": "MERGED",
            "submitted": merged,
        },
    ]
    (root / org / "raw").mkdir(parents=True)
    snapshot = root / org / "raw" / "2025-09.ndjson.gz"
    with gzip.open(snapshot, "wt") as out:
        out.writelines(json.dumps(c) + "\n" for c in raw)
    text = "".join(json.dumps(r) + "\n" for r in rows)
    built = root / org / "examples" / "2025-09.jsonl"
    built.parent.mkdir(parents=True)
    built.write_text(text)
    write_build_record(built, hashlib.sha256(snapshot.read_bytes()).hexdigest(), complete=True)
    refined = refined_dir(root, org) / "2025-09.jsonl"
    refined.parent.mkdir(parents=True)
    refined.write_text(text)
    write_source_record(root, org, built, refined)


def test_each_sensitivity_lists_its_examples_and_says_what_the_searches_missed(
    tmp_path: Path,
) -> None:
    _corpus(tmp_path / "corpus", "openstack")
    trailers = tmp_path / "trailers.json"
    trailers.write_text(json.dumps({**TRAILERS, "orgs": {"openstack": {"changes_ai": ["p c1"]}}}))
    out = tmp_path / "ids.json"
    _run(
        [
            "--root", str(tmp_path / "corpus"), "--org", "openstack",
            "--ai-trailers", str(trailers), "--out", str(out),
        ]
    )  # fmt: skip
    listing = json.loads(out.read_text())
    every = [f"openstack:c{n}:f.py:1" for n in range(1, 6)]
    assert listing["universe"] == every and listing["examples"] == 5 and listing["changes"] == 5
    assert listing["ids"] == {"ai_assisted": [every[0]], "backport_only": [every[1]]}
    # Merged after the bound, or never: the searches could not have seen them.
    assert listing["ai_unsearched"] == [every[2], every[3]]
