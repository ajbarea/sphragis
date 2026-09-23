"""Every stage and analysis reads refined examples, and refuses one that no longer matches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sphragis.corpus.load import (
    DERIVED,
    build_record,
    built_examples,
    derived_file_rows,
    file_record,
    mark_derived,
    refined_dir,
    refined_examples,
    sha256,
    stale_refinements,
    write_build_record,
    write_derived_file,
    write_source_record,
)


def _month(root: Path, org: str = "o", name: str = "2024-10.jsonl", *, refine: bool = True):
    built = root / org / "examples" / name
    built.parent.mkdir(parents=True, exist_ok=True)
    built.write_text(json.dumps({"id": f"built-{name}"}) + "\n")
    raw = root / org / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    snapshot = raw / name.replace(".jsonl", ".ndjson.gz")
    snapshot.write_bytes(b"raw")
    write_build_record(built, sha256(snapshot), complete=True)
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
    (tmp_path / "o" / "raw" / "2099-01.ndjson.gz").write_bytes(b"another month's kinds")
    assert "raw snapshots changed" in stale_refinements(tmp_path, "o")[0]


def test_a_month_built_from_a_snapshot_since_refetched_is_refused(tmp_path: Path) -> None:
    _month(tmp_path)
    (tmp_path / "o" / "raw" / "2024-10.ndjson.gz").write_bytes(b"refetched")
    assert "since refetched" in stale_refinements(tmp_path, "o")[0]


def test_a_month_built_under_other_build_rules_is_refused_though_refined_since(
    tmp_path: Path,
) -> None:
    built, _ = _month(tmp_path)
    record = build_record(built)
    record.write_text(json.dumps({**json.loads(record.read_text()), "build_rules": "old"}))
    assert "other build rules" in stale_refinements(tmp_path, "o")[0]


def test_a_month_with_no_build_record_is_refused(tmp_path: Path) -> None:
    built, _ = _month(tmp_path)
    build_record(built).unlink()
    assert "no build record" in stale_refinements(tmp_path, "o")[0]


def test_an_incomplete_build_is_refused(tmp_path: Path) -> None:
    built, _ = _month(tmp_path)
    record = build_record(built)
    record.write_text(json.dumps({**json.loads(record.read_text()), "complete": False}))
    assert "did not complete" in stale_refinements(tmp_path, "o")[0]


def test_an_empty_built_directory_is_refused_rather_than_read_as_nothing(tmp_path: Path) -> None:
    (tmp_path / "o" / "examples").mkdir(parents=True)
    with pytest.raises(SystemExit, match="no built months"):
        refined_examples(tmp_path, "o")


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


def test_a_derived_corpus_checks_its_source_from_any_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _month(Path("src"))
    half = refined_dir(Path("out"), "half")
    half.mkdir(parents=True)
    (half / "2024-10.jsonl").write_text('{"id": "h"}\n')
    mark_derived(Path("out"), "half", source_root=Path("src"), source_org="o")
    (refined_dir(Path("src"), "o") / "2024-10.jsonl").write_text('{"id": "re-refined"}\n')
    monkeypatch.chdir("/")
    assert stale_refinements(tmp_path / "out", "half"), "a relative source root was skipped"


def test_a_derived_corpus_whose_source_went_stale_is_refused(tmp_path: Path) -> None:
    _derived(tmp_path)
    built = tmp_path / "src" / "o" / "examples" / "2024-10.jsonl"
    built.write_text(built.read_text() + "\n")
    assert "itself stale" in stale_refinements(tmp_path / "out", "half")[0]


def test_a_derived_corpus_whose_source_was_re_refined_is_refused(tmp_path: Path) -> None:
    _derived(tmp_path)
    source = refined_dir(tmp_path / "src", "o") / "2024-10.jsonl"
    source.write_text('{"id": "re-refined"}\n')
    built = tmp_path / "src" / "o" / "examples" / "2024-10.jsonl"
    write_source_record(tmp_path / "src", "o", built, source)
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
    _month(tmp_path)
    path = tmp_path / "nova.jsonl"
    write_derived_file(path, [{"id": "a"}, {"id": "b"}], sources=[(tmp_path, "o")])
    assert [r["id"] for r in derived_file_rows(path)] == ["a", "b"]


def test_a_file_with_no_record_is_refused_unless_read_as_legacy(tmp_path: Path) -> None:
    path = tmp_path / "openstack-2024-10.jsonl"
    path.write_text('{"id": "v1"}\n')
    with pytest.raises(SystemExit, match="no record"):
        derived_file_rows(path)
    assert [r["id"] for r in derived_file_rows(path, legacy=True)] == ["v1"]


def test_an_edited_derived_file_is_refused(tmp_path: Path) -> None:
    _month(tmp_path)
    path = tmp_path / "nova.jsonl"
    write_derived_file(path, [{"id": "a"}], sources=[(tmp_path, "o")])
    path.write_text(path.read_text() + '{"id": "injected"}\n')
    with pytest.raises(SystemExit, match="changed since it was cut"):
        derived_file_rows(path)


def test_a_derived_file_cut_under_other_rules_is_refused(tmp_path: Path) -> None:
    _month(tmp_path)
    path = tmp_path / "nova.jsonl"
    write_derived_file(path, [{"id": "a"}], sources=[(tmp_path, "o")])
    record = file_record(path)
    record.write_text(json.dumps({**json.loads(record.read_text()), "rules": "old"}))
    with pytest.raises(SystemExit, match="other rules"):
        derived_file_rows(path)


def test_a_derived_file_whose_source_was_re_refined_is_refused(tmp_path: Path) -> None:
    _month(tmp_path)
    path = tmp_path / "nova.jsonl"
    write_derived_file(path, [{"id": "a"}], sources=[(tmp_path, "o")])
    assert derived_file_rows(path) == [{"id": "a"}]
    (refined_dir(tmp_path, "o") / "2024-10.jsonl").write_text('{"id": "re-refined"}\n')
    with pytest.raises(SystemExit, match="re-refined|itself stale"):
        derived_file_rows(path)


def test_an_empty_derived_file_is_refused(tmp_path: Path) -> None:
    _month(tmp_path)
    path = tmp_path / "nova.jsonl"
    write_derived_file(path, [], sources=[(tmp_path, "o")])
    with pytest.raises(SystemExit, match="no examples"):
        derived_file_rows(path)


def test_files_that_differ_only_in_suffix_keep_separate_records(tmp_path: Path) -> None:
    assert file_record(tmp_path / "nova.jsonl") != file_record(tmp_path / "nova.json")


def test_an_unreadable_build_record_is_refused_rather_than_raised(tmp_path: Path) -> None:
    built, _ = _month(tmp_path)
    build_record(built).write_text("")
    assert "unreadable build record" in stale_refinements(tmp_path, "o")[0]
    build_record(built).write_text("[]")
    assert "unreadable build record" in stale_refinements(tmp_path, "o")[0]


def test_a_derived_record_with_no_months_is_refused(tmp_path: Path) -> None:
    half = _derived(tmp_path)
    (half / "2024-10.jsonl").unlink()
    assert "no months" in stale_refinements(tmp_path / "out", "half")[0]


def test_a_source_gone_from_a_root_that_is_still_here_is_refused(tmp_path: Path) -> None:
    import shutil

    _derived(tmp_path)
    shutil.rmtree(refined_dir(tmp_path / "src", "o"))
    assert "gone" in stale_refinements(tmp_path / "out", "half")[0]


def test_a_copy_away_from_its_source_root_is_checked_against_its_own_record(
    tmp_path: Path,
) -> None:
    import shutil

    _derived(tmp_path)
    shutil.rmtree(tmp_path / "src")
    assert stale_refinements(tmp_path / "out", "half") == []


def test_derived_corpora_that_cite_each_other_are_refused_not_recursed(tmp_path: Path) -> None:
    for org in ("a", "b"):
        half = refined_dir(tmp_path, org)
        half.mkdir(parents=True)
        (half / "2024-10.jsonl").write_text(f'{{"id": "{org}"}}\n')
    mark_derived(tmp_path, "a", source_root=tmp_path, source_org="b")
    mark_derived(tmp_path, "b", source_root=tmp_path, source_org="a")
    assert stale_refinements(tmp_path, "a")
    with pytest.raises(ValueError, match="itself"):
        mark_derived(tmp_path, "a", source_root=tmp_path, source_org="a")


def test_a_file_cut_from_a_changed_intermediate_is_refused(tmp_path: Path) -> None:
    _month(tmp_path)
    middle = tmp_path / "nova.jsonl"
    write_derived_file(middle, [{"id": "a"}, {"id": "b"}], sources=[(tmp_path, "o")])
    half = tmp_path / "planted-a.jsonl"
    write_derived_file(half, [{"id": "a"}], sources=[(tmp_path, "o")], via=[middle])
    assert derived_file_rows(half) == [{"id": "a"}]
    write_derived_file(middle, [{"id": "c"}], sources=[(tmp_path, "o")])
    with pytest.raises(SystemExit, match="nova.jsonl"):
        derived_file_rows(half)
