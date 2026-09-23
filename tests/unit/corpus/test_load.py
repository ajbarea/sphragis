"""Every stage and analysis reads refined examples, and refuses one that no longer matches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sphragis.corpus.load import (
    DERIVED,
    built_examples,
    derived_file_rows,
    file_record,
    mark_derived,
    refined_dir,
    refined_examples,
    stale_refinements,
    write_derived_file,
    write_source_record,
)


def _month(root: Path, org: str = "o", name: str = "2024-10.jsonl", *, refine: bool = True):
    built = root / org / "examples" / name
    built.parent.mkdir(parents=True, exist_ok=True)
    built.write_text(json.dumps({"id": f"built-{name}"}) + "\n")
    raw = root / org / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "2024-10.ndjson.gz").write_bytes(b"raw")
    refined = refined_dir(root, org) / name
    if refine:
        refined.parent.mkdir(parents=True, exist_ok=True)
        refined.write_text(json.dumps({"id": f"refined-{name}"}) + "\n")
        write_source_record(root, org, built, refined)
    return built, refined


def test_refined_examples_are_what_is_read(tmp_path: Path) -> None:
    _month(tmp_path)
    assert [r["id"] for r in refined_examples(tmp_path, "o")] == ["refined-2024-10.jsonl"]
    assert [r["id"] for r in built_examples(tmp_path, "o")] == ["built-2024-10.jsonl"]


def test_an_unrefined_month_is_refused(tmp_path: Path) -> None:
    _month(tmp_path, refine=False)
    with pytest.raises(SystemExit, match="not refined"):
        refined_examples(tmp_path, "o")


def test_a_hand_edited_refined_file_is_refused(tmp_path: Path) -> None:
    _, refined = _month(tmp_path)
    refined.write_text(refined.read_text() + json.dumps({"id": "injected"}) + "\n")
    assert "changed since it was written" in stale_refinements(tmp_path, "o")[0]


def test_a_deleted_refined_file_is_refused_though_its_record_remains(tmp_path: Path) -> None:
    _, refined = _month(tmp_path)
    refined.unlink()
    assert "not refined" in stale_refinements(tmp_path, "o")[0]


def test_a_refined_month_whose_built_month_is_gone_is_refused(tmp_path: Path) -> None:
    built, _ = _month(tmp_path)
    _month(tmp_path, name="2024-11.jsonl")
    (built.parent / "2024-11.jsonl").unlink()
    assert any("no built month" in s for s in stale_refinements(tmp_path, "o"))


def test_a_stray_refined_month_is_refused(tmp_path: Path) -> None:
    _month(tmp_path)
    (refined_dir(tmp_path, "o") / "2099-01.jsonl").write_text('{"id": "stray"}\n')
    assert any("no built month" in s for s in stale_refinements(tmp_path, "o"))


def test_a_rebuilt_month_is_refused(tmp_path: Path) -> None:
    built, _ = _month(tmp_path)
    built.write_text(built.read_text() + "\n")
    assert "rebuilt" in stale_refinements(tmp_path, "o")[0]


def test_changed_raw_snapshots_are_refused(tmp_path: Path) -> None:
    _month(tmp_path)
    (tmp_path / "o" / "raw" / "2024-10.ndjson.gz").write_bytes(b"refetched")
    assert "raw snapshots changed" in stale_refinements(tmp_path, "o")[0]


def test_other_rules_are_refused(tmp_path: Path) -> None:
    built, _ = _month(tmp_path)
    record = refined_dir(tmp_path, "o") / "2024-10.source.json"
    record.write_text(json.dumps({**json.loads(record.read_text()), "rules": "old"}))
    assert "other rules" in stale_refinements(tmp_path, "o")[0]


def test_a_directory_with_neither_builds_nor_a_derived_record_is_refused(tmp_path: Path) -> None:
    (tmp_path / "o").mkdir()
    with pytest.raises(SystemExit):
        refined_examples(tmp_path, "o")


def _derived(root: Path) -> Path:
    _month(root / "src")
    half = refined_dir(root / "out", "half")
    half.mkdir(parents=True)
    (half / "2024-10.jsonl").write_text('{"id": "h"}\n')
    mark_derived(root / "out", "half", source_root=root / "src", source_org="o")
    return half


def test_a_derived_corpus_is_read_while_it_and_its_source_are_unchanged(tmp_path: Path) -> None:
    _derived(tmp_path)
    assert [r["id"] for r in refined_examples(tmp_path / "out", "half")] == ["h"]


def test_a_derived_corpus_whose_source_was_re_refined_is_refused(tmp_path: Path) -> None:
    _derived(tmp_path)
    source = refined_dir(tmp_path / "src", "o") / "2024-10.jsonl"
    source.write_text('{"id": "re-refined"}\n')
    assert "re-refined" in stale_refinements(tmp_path / "out", "half")[0]


def test_an_edited_derived_month_is_refused(tmp_path: Path) -> None:
    half = _derived(tmp_path)
    (half / "2024-10.jsonl").write_text('{"id": "edited"}\n')
    assert "differ from their record" in stale_refinements(tmp_path / "out", "half")[0]


def test_a_derived_record_under_other_rules_is_refused(tmp_path: Path) -> None:
    half = _derived(tmp_path)
    record = half / DERIVED
    record.write_text(json.dumps({**json.loads(record.read_text()), "rules": "old"}))
    assert "other rules" in stale_refinements(tmp_path / "out", "half")[0]


def test_a_derived_file_is_read_while_it_matches_its_record(tmp_path: Path) -> None:
    path = tmp_path / "nova.jsonl"
    write_derived_file(path, [{"id": "a"}, {"id": "b"}])
    assert [r["id"] for r in derived_file_rows(path)] == ["a", "b"]


def test_a_file_with_no_record_is_refused_unless_read_as_legacy(tmp_path: Path) -> None:
    path = tmp_path / "openstack-2024-10.jsonl"
    path.write_text('{"id": "v1"}\n')
    with pytest.raises(SystemExit, match="no record"):
        derived_file_rows(path)
    assert [r["id"] for r in derived_file_rows(path, legacy=True)] == ["v1"]


def test_an_edited_derived_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "nova.jsonl"
    write_derived_file(path, [{"id": "a"}])
    path.write_text(path.read_text() + '{"id": "injected"}\n')
    with pytest.raises(SystemExit, match="changed since it was cut"):
        derived_file_rows(path)


def test_a_derived_file_cut_under_other_rules_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "nova.jsonl"
    write_derived_file(path, [{"id": "a"}])
    record = file_record(path)
    record.write_text(json.dumps({**json.loads(record.read_text()), "rules": "old"}))
    with pytest.raises(SystemExit, match="other rules"):
        derived_file_rows(path)
