"""The control that asks whether an arbitrary boundary reproduces the organization's."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "placebo_corpus.py"
_spec = importlib.util.spec_from_file_location("placebo_corpus", _SCRIPT)
assert _spec and _spec.loader
placebo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(placebo)


def _build(src: Path, out: Path, org: str = "qt") -> None:
    """Run the builder the way the job does, through its own argument parser."""
    argv = sys.argv
    sys.argv = ["placebo_corpus.py", "--root", str(src), "--org", org, "--out-root", str(out)]
    try:
        placebo.main()
    finally:
        sys.argv = argv


def _corpus(root: Path, org: str, rows: list[dict]) -> None:
    """One built and refined month, as the refine stage leaves it."""
    from sphragis.corpus.load import refined_dir, sha256
    from sphragis.corpus.refine import RULES_VERSION

    text = "".join(json.dumps(r) + "\n" for r in rows)
    built = root / org / "examples" / "2024-11.jsonl"
    built.parent.mkdir(parents=True)
    built.write_text(text)
    refined = refined_dir(root, org)
    refined.mkdir(parents=True)
    (refined / "2024-11.jsonl").write_text(text)
    (refined / "2024-11.source.json").write_text(
        json.dumps({"examples_sha256": sha256(built), "rules": RULES_VERSION})
    )


def _row(project: str, day: str, ident: str) -> dict:
    return {"project": project, "created": f"{day} 00:00:00.000000000", "id": ident, "org": "qt"}


def test_the_largest_project_goes_first_and_the_sides_stay_balanced() -> None:
    counts = {"a": 100, "b": 60, "c": 50, "d": 10}
    sides = placebo.assign(counts)
    totals = [0, 0]
    for project, side in sides.items():
        totals[side] += counts[project]
    assert abs(totals[0] - totals[1]) <= 20, totals


def test_the_split_is_a_function_of_the_corpus_alone() -> None:
    """No seed, so a control that behaved badly cannot be reshuffled into behaving."""
    counts = {"p1": 7, "p2": 7, "p3": 3, "p4": 1}
    assert placebo.assign(counts) == placebo.assign(dict(reversed(list(counts.items()))))


def test_projects_of_equal_size_break_ties_by_name() -> None:
    assert placebo.assign({"b": 5, "a": 5})["a"] == 0


def test_each_half_becomes_a_corpus_the_registered_runner_can_read(tmp_path: Path) -> None:
    _corpus(tmp_path / "src", "qt", [_row(f"p{i % 4}", "2024-11-05", f"x{i}") for i in range(40)])
    _build(tmp_path / "src", tmp_path / "out")

    manifest = json.loads((tmp_path / "out" / "placebo.json").read_text())
    assert set(manifest["names"]) == {"qt-a", "qt-b"}
    for name in manifest["names"]:
        written = (tmp_path / "out" / name / "refined" / "2024-11.jsonl").read_text()
        kept = [json.loads(line) for line in written.splitlines() if line]
        assert kept, f"{name} got no rows"
        # The rows must claim the pseudo-organization, or the runner labels both halves 'qt'.
        assert {row["org"] for row in kept} == {name}
    assert manifest["projects"]["qt-a"] and manifest["projects"]["qt-b"]
    assert not set(manifest["projects"]["qt-a"]) & set(manifest["projects"]["qt-b"])


def test_a_project_outside_the_training_window_is_dropped_not_silently_sided(
    tmp_path: Path,
) -> None:
    """It was never balanced against anything, so putting it on a side would tilt the control."""
    rows = [_row("kept", "2024-11-05", f"a{i}") for i in range(6)]
    rows += [_row("also-kept", "2024-11-06", f"c{i}") for i in range(6)]
    rows += [_row("later", "2025-10-05", f"b{i}") for i in range(6)]
    _corpus(tmp_path / "src", "qt", rows)
    _build(tmp_path / "src", tmp_path / "out")

    manifest = json.loads((tmp_path / "out" / "placebo.json").read_text())
    assert manifest["dropped_projects"] == ["later"]
    sided = set(manifest["projects"]["qt-a"]) | set(manifest["projects"]["qt-b"])
    assert "later" not in sided
    # The manifest and the corpus have to agree: a project the manifest calls dropped and the
    # corpus still carries would tilt one side with rows nothing balanced.
    written = [
        json.loads(line)
        for name in manifest["names"]
        for month in (tmp_path / "out" / name / "refined").glob("*.jsonl")
        for line in month.read_text().splitlines()
        if line
    ]
    assert "later" not in {row["project"] for row in written}


def test_an_organization_of_one_project_cannot_be_split(tmp_path: Path) -> None:
    _corpus(tmp_path / "src", "qt", [_row("only", "2024-11-05", f"a{i}") for i in range(4)])
    with pytest.raises(SystemExit, match="1 projects"):
        _build(tmp_path / "src", tmp_path / "out")
