"""The control that asks whether an arbitrary boundary reproduces the organization's."""

from __future__ import annotations

import hashlib
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


def _build(
    src: Path, out: Path, org: str = "qt", names: list[str] | None = None, extra: tuple = ()
) -> None:
    """Run the builder the way the job does, through its own argument parser."""
    argv = sys.argv
    sys.argv = ["placebo_corpus.py", "--root", str(src), "--org", org, "--out-root", str(out)]
    sys.argv += ["--names", *names] if names else []
    sys.argv += list(extra)
    try:
        placebo.main()
    finally:
        sys.argv = argv


def _corpus(root: Path, org: str, rows: list[dict]) -> None:
    """One built and refined month, as the refine stage leaves it."""
    from sphragis.corpus.load import refined_dir, write_build_record, write_source_record

    text = "".join(json.dumps(r) + "\n" for r in rows)
    built = root / org / "examples" / "2024-11.jsonl"
    built.parent.mkdir(parents=True)
    built.write_text(text)
    write_build_record(built, None, complete=True)
    refined = refined_dir(root, org) / "2024-11.jsonl"
    refined.parent.mkdir(parents=True)
    refined.write_text(text)
    write_source_record(root, org, built, refined)


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
    # Each half is a derived corpus the loader accepts, which it only does with a derived record.
    from sphragis.corpus.load import refined_examples

    for name in manifest["names"]:
        assert refined_examples(tmp_path / "out", name)


def test_a_reused_out_root_keeps_no_month_from_an_earlier_split(tmp_path: Path) -> None:
    _corpus(tmp_path / "src", "qt", [_row(f"p{i % 4}", "2024-11-05", f"x{i}") for i in range(40)])
    stale = tmp_path / "out" / "qt-a" / "refined" / "2020-01.jsonl"
    stale.parent.mkdir(parents=True)
    stale.write_text('{"id": "old"}\n')
    _build(tmp_path / "src", tmp_path / "out")
    assert not stale.exists()


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


def test_two_halves_with_one_name_are_refused(tmp_path: Path) -> None:
    rows = [_row(p, "2024-11-05", f"{p}{i}") for p in ("x", "y") for i in range(3)]
    _corpus(tmp_path / "src", "qt", rows)
    with pytest.raises(SystemExit, match="two distinct"):
        _build(tmp_path / "src", tmp_path / "out", names=["h", "h"])


@pytest.mark.parametrize("names", [["qt", "qt-b"], ["openstack", "qt-b"]])
def test_a_half_cannot_be_written_over_a_built_corpus(tmp_path: Path, names: list[str]) -> None:
    rows = [_row(p, "2024-11-05", f"{p}{i}") for p in ("x", "y") for i in range(3)]
    _corpus(tmp_path / "src", "qt", rows)
    _corpus(tmp_path / "src", "openstack", rows)
    with pytest.raises(SystemExit, match="built corpus"):
        _build(tmp_path / "src", tmp_path / "src", names=names)
    for org in ("qt", "openstack"):
        assert (tmp_path / "src" / org / "refined" / "2024-11.jsonl").exists()


def test_a_half_name_that_is_a_path_is_refused(tmp_path: Path) -> None:
    rows = [_row(p, "2024-11-05", f"{p}{i}") for p in ("x", "y") for i in range(3)]
    _corpus(tmp_path / "src", "qt", rows)
    with pytest.raises(SystemExit, match="plain corpus name"):
        _build(tmp_path / "src", tmp_path / "out", names=["../src/qt", "x"])


def _written(out: Path) -> list[dict]:
    rows = []
    for name in json.loads((out / "placebo.json").read_text())["names"]:
        text = (out / name / "refined" / "2024-11.jsonl").read_text()
        rows += [json.loads(line) for line in text.splitlines() if line]
    return rows


def _duplicated(tmp_path: Path) -> list[dict]:
    """Two projects of 20 rows in opposite halves, with one pair duplicated across them."""
    rows = [
        dict(
            _row(p, "2024-11-05", f"{p}{i}"),
            before=" ".join(hashlib.sha256(f"{p}{i}{w}".encode()).hexdigest() for w in range(8)),
            after=" ".join(hashlib.sha256(f"{p}{i}{w}!".encode()).hexdigest() for w in range(8)),
        )
        for p in ("a", "b")
        for i in range(20)
    ]
    rows[25] = dict(rows[25], before=rows[0]["before"], after=rows[0]["after"])
    _corpus(tmp_path / "src", "qt", rows)
    return rows


def test_dedup_org_removes_a_duplicate_pair_that_would_survive_in_both_halves(
    tmp_path: Path,
) -> None:
    rows = _duplicated(tmp_path)
    _build(tmp_path / "src", tmp_path / "plain")
    assert len(_written(tmp_path / "plain")) == len(rows)
    _build(tmp_path / "src", tmp_path / "org", extra=("--dedup-org",))
    written = _written(tmp_path / "org")
    assert len(written) == len(rows) - 1
    assert "_position" not in written[0]
    manifest = json.loads((tmp_path / "org" / "placebo.json").read_text())
    assert manifest["dedup_org"]["exact"] == 1


def test_dedup_org_writes_the_same_examples_whatever_the_partition(tmp_path: Path) -> None:
    _duplicated(tmp_path)
    ids = []
    for seed in (None, 1, 2):
        out = tmp_path / f"p{seed}"
        extra = ("--dedup-org",) + (("--partition-seed", str(seed)) if seed else ())
        _build(tmp_path / "src", out, extra=extra)
        ids.append(sorted(row["id"] for row in _written(out)))
    assert ids[0] == ids[1] == ids[2]


def _rows_with_targets(n: int) -> list[dict]:
    return [
        dict(_row(f"p{i % 4}", "2024-11-05", f"x{i}"), change_id=f"c{i}", after=f"line {i}\n")
        for i in range(n)
    ]


def _half(out: Path, name: str) -> list[dict]:
    text = (out / name / "refined" / "2024-11.jsonl").read_text()
    return [json.loads(line) for line in text.splitlines() if line]


def test_plant_gives_each_half_its_own_marker_at_the_same_rate(tmp_path: Path) -> None:
    from sphragis.experiment.planted import MARKER, MARKER_OTHER

    _corpus(tmp_path / "src", "qt", _rows_with_targets(400))
    _build(tmp_path / "src", tmp_path / "out", extra=("--plant", "0.25"))
    manifest = json.loads((tmp_path / "out" / "placebo.json").read_text())
    a, b = _half(tmp_path / "out", "qt-a"), _half(tmp_path / "out", "qt-b")
    assert not any(r["after"].endswith(MARKER) for r in a)
    assert not any(r["after"].endswith(MARKER_OTHER) for r in b)
    marked = {
        "qt-a": sum(r["after"].endswith(MARKER_OTHER) for r in a),
        "qt-b": sum(r["after"].endswith(MARKER) for r in b),
    }
    for name, rows in (("qt-a", a), ("qt-b", b)):
        report = manifest["plant"]["halves"][name]
        assert report["changed"] == marked[name] > 0
        assert abs(marked[name] / len(rows) - 0.25) < 0.1
    assert manifest["plant"]["fraction"] == 0.25


def test_plant_changes_only_the_target_and_leaves_the_split_alone(tmp_path: Path) -> None:
    rows = _rows_with_targets(80)
    _corpus(tmp_path / "src", "qt", rows)
    _build(tmp_path / "src", tmp_path / "plain")
    _build(tmp_path / "src", tmp_path / "planted", extra=("--plant", "0.5"))
    for name in ("qt-a", "qt-b"):
        plain, planted = _half(tmp_path / "plain", name), _half(tmp_path / "planted", name)
        assert [r["id"] for r in plain] == [r["id"] for r in planted]
        for before, after in zip(plain, planted, strict=True):
            assert {k: v for k, v in before.items() if k != "after"} == {
                k: v for k, v in after.items() if k != "after"
            }
            assert after["after"].startswith(before["after"].rstrip("\n"))


def test_without_plant_nothing_is_marked(tmp_path: Path) -> None:
    _corpus(tmp_path / "src", "qt", _rows_with_targets(40))
    _build(tmp_path / "src", tmp_path / "out")
    assert json.loads((tmp_path / "out" / "placebo.json").read_text())["plant"] is None
