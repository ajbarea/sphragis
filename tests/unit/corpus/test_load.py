"""Every stage and analysis reads refined examples, and refuses a stale refinement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sphragis.corpus.load import (
    DERIVED,
    built_examples,
    mark_derived,
    refined_dir,
    refined_examples,
    refined_month_files,
    sha256,
    stale_refinements,
)
from sphragis.corpus.refine import RULES_VERSION


def _month(root: Path, org: str = "o", *, refined: bool = True, rules: str = RULES_VERSION) -> Path:
    built = root / org / "examples" / "2024-10.jsonl"
    built.parent.mkdir(parents=True)
    built.write_text(json.dumps({"id": "built"}) + "\n")
    if refined:
        out = refined_dir(root, org)
        out.mkdir(parents=True)
        (out / "2024-10.jsonl").write_text(json.dumps({"id": "refined"}) + "\n")
        (out / "2024-10.source.json").write_text(
            json.dumps({"examples_sha256": sha256(built), "rules": rules})
        )
    return built


def test_refined_examples_are_what_is_read(tmp_path: Path) -> None:
    _month(tmp_path)
    assert [r["id"] for r in refined_examples(tmp_path, "o")] == ["refined"]
    assert [r["id"] for r in built_examples(tmp_path, "o")] == ["built"]


def test_an_unrefined_month_is_refused(tmp_path: Path) -> None:
    _month(tmp_path, refined=False)
    with pytest.raises(SystemExit, match="not refined"):
        refined_examples(tmp_path, "o")


def test_a_refinement_under_other_rules_is_refused(tmp_path: Path) -> None:
    _month(tmp_path, rules="old")
    assert stale_refinements(tmp_path, "o") == ["2024-10.jsonl"]
    with pytest.raises(SystemExit):
        refined_month_files(tmp_path, "o")


def test_a_directory_with_neither_builds_nor_a_derived_record_is_refused(tmp_path: Path) -> None:
    (tmp_path / "o").mkdir()
    with pytest.raises(SystemExit, match="no built months"):
        refined_examples(tmp_path, "o")


def test_a_derived_corpus_is_read_while_its_rules_are_current(tmp_path: Path) -> None:
    out = refined_dir(tmp_path, "half")
    out.mkdir(parents=True)
    (out / "2024-10.jsonl").write_text(json.dumps({"id": "h"}) + "\n")
    mark_derived(tmp_path, "half", source="root/o")
    assert [r["id"] for r in refined_examples(tmp_path, "half")] == ["h"]
    record = out / DERIVED
    record.write_text(json.dumps({**json.loads(record.read_text()), "rules": "old"}))
    with pytest.raises(SystemExit):
        refined_examples(tmp_path, "half")
