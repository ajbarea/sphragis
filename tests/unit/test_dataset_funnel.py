"""The dataset page's funnel is generated from the committed artifact and cannot drift from it."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from sphragis.corpus.storage import write_snapshot

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("funnel", ROOT / "scripts" / "dataset_funnel.py")
assert _spec is not None and _spec.loader is not None
funnel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(funnel)


def test_the_dataset_page_shows_the_committed_funnel() -> None:
    artifact = json.loads((ROOT / "datasets/results/dataset-funnel.json").read_text())
    page = (ROOT / "docs/dataset.md").read_text()
    assert funnel.with_table(page, artifact) == page


def _corpus(tmp_path: Path, *, test: int = 0) -> Path:
    org = tmp_path / "acme"
    (org / "raw").mkdir(parents=True)
    (org / "examples").mkdir()
    for month, rows in (("2024-09", 5), ("2024-10", 3), ("2025-10", 2)):
        write_snapshot(tmp_path, "acme", month, [{"n": i} for i in range(rows)], record={})
    for month in ("2024-10", "2025-10"):  # the months the build read
        (org / "examples" / f"{month}.drops.json").write_text("{}")
    manifest = {
        "counts": {"pilot": 1, "train": 4, "dev": 2, "test": test},
        "stats": {
            "rules": "r",
            "build_rules": "b",
            "built_drops": {"author_comment": 9, "comment_error": 1, "diff_error": 2},
            "refine_counts": {"suggested_edit": 3, "applied_fix": 0, "successor_kind_unknown": 2},
            "deduped": {"exact": 1},
        },
    }
    (org / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path


def test_the_funnel_counts_the_months_the_build_read_and_sums_each_rows_counters(tmp_path) -> None:
    read = funnel.funnel(_corpus(tmp_path), "acme")
    assert (read["merged_changes"], read["months"]) == (5, ["2024-10", "2025-10"])
    rows = {r["reason"]: r for step in read["steps"] for r in step["rows"]}
    assert rows["their comments could not be fetched"] == {
        "reason": "their comments could not be fetched",
        "unit": "changes",
        "count": 1,
    }
    assert rows["its diff could not be fetched"]["count"] == 2
    assert rows["target written by the reviewer (suggested edit, applied fix)"]["count"] == 3
    assert rows["from an account the host tags as a bot"]["count"] is None
    assert (read["windows"], read["examples"]) == ({"pilot": 1, "train": 4, "dev": 2}, 7)


def test_a_counter_the_build_did_not_record_reads_as_not_counted() -> None:
    artifact = {
        "organizations": {
            "acme": {
                "merged_changes": 1,
                "steps": [{"step": "S", "rows": [{"reason": "r", "unit": "u", "count": None}]}],
                "windows": {"pilot": 0, "train": 0, "dev": 0},
                "examples": 0,
                "kept_unchecked": {"label": "k", "count": 0},
            }
        }
    }
    assert "| r | u | not counted |" in funnel.table(artifact)


def test_organizations_whose_rows_differ_are_refused() -> None:
    org = lambda reason: {  # noqa: E731
        "merged_changes": 1,
        "steps": [{"step": "S", "rows": [{"reason": reason, "unit": "u", "count": 1}]}],
        "windows": {"pilot": 0, "train": 0, "dev": 0},
        "examples": 0,
        "kept_unchecked": {"label": "k", "count": 0},
    }
    with pytest.raises(ValueError, match="do not share one layout"):
        funnel.table({"organizations": {"a": org("x"), "b": org("y")}})


def test_a_manifest_counting_the_test_window_is_refused(tmp_path) -> None:
    with pytest.raises(ValueError, match="seal is broken"):
        funnel.funnel(_corpus(tmp_path, test=1), "acme")


def test_a_page_without_one_pair_of_markers_is_refused() -> None:
    with pytest.raises(ValueError, match="exactly one pair"):
        funnel.with_table("no markers", {"organizations": {}})


def test_a_partial_set_of_a_rows_counters_is_refused(tmp_path) -> None:
    root = _corpus(tmp_path)
    manifest = json.loads((root / "acme" / "manifest.json").read_text())
    manifest["stats"]["built_drops"]["ill_posed_empty_before"] = 4
    (root / "acme" / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="a partial sum is no count"):
        funnel.funnel(root, "acme")


def test_examples_kept_without_the_rebase_check_are_counted_apart(tmp_path) -> None:
    read = funnel.funnel(_corpus(tmp_path), "acme")
    assert read["kept_unchecked"]["count"] == 2
