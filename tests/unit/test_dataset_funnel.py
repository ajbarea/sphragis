"""The dataset page's funnel is generated from the committed artifact and cannot drift from it."""

from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path

import pytest

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
    for month, rows in (("2024-09", 5), ("2024-10", 3), ("2025-10", 2), ("2025-11", 7)):
        with gzip.open(org / "raw" / f"{month}.ndjson.gz", "wt") as out:
            out.writelines("{}\n" for _ in range(rows))
    manifest = {
        "counts": {"pilot": 1, "train": 4, "dev": 2, "test": test},
        "stats": {
            "rules": "r",
            "built_drops": {"author_comment": 9, "comment_error": 1, "diff_error": 2},
            "refine_counts": {"suggested_edit": 3},
            "deduped": {"exact": 1},
        },
    }
    (org / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path


def test_the_funnel_counts_the_counted_windows_and_sums_each_rows_counters(tmp_path) -> None:
    read = funnel.funnel(_corpus(tmp_path), "acme")
    assert read["merged_changes"] == 5  # 2024-10 and 2025-10; neither 2024-09 nor the test window
    rows = {r["reason"]: r["count"] for step in read["steps"] for r in step["rows"]}
    assert rows["lost to a fetch error"] == 3
    assert rows["written by the change's author"] == 9
    assert (read["windows"], read["examples"]) == ({"pilot": 1, "train": 4, "dev": 2}, 7)


def test_a_manifest_counting_the_test_window_is_refused(tmp_path) -> None:
    with pytest.raises(ValueError, match="seal is broken"):
        funnel.funnel(_corpus(tmp_path, test=1), "acme")


def test_a_page_without_one_pair_of_markers_is_refused() -> None:
    with pytest.raises(ValueError, match="exactly one pair"):
        funnel.with_table("no markers", {"organizations": {}})
