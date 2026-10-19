"""The admissible list's search: a full list under its own name, a short search under `-refused`."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "admissible_partitions", ROOT / "scripts" / "admissible_partitions.py"
)
assert _spec is not None and _spec.loader is not None
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)


class _Serial:
    """ProcessPoolExecutor's surface, run in-process so a test's fake `check` is the one called."""

    def __init__(self, workers, initializer, initargs):
        initializer(*initargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def map(self, fn, items):
        return map(fn, items)


def _main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qualifying: set[int], *flags: str):
    monkeypatch.setattr(ap, "refined_examples", lambda root, org: [])
    monkeypatch.setattr(ap, "run_dedup", lambda rows: ([], {"exact": 0}))
    monkeypatch.setattr(ap, "organization_train", lambda rows, windows: [])
    monkeypatch.setattr(ap, "excluded_projects", lambda rows, window: [])
    monkeypatch.setattr(ap, "provenance_header", lambda: {})
    monkeypatch.setattr(ap, "ProcessPoolExecutor", _Serial)
    monkeypatch.setattr(
        ap,
        "check",
        lambda seed: {"seed": seed, "qualifies": seed in qualifying, "checks": {}},
    )
    reference = tmp_path / "split-criteria.json"
    reference.write_text(json.dumps({"halves": [], "suffix_total_variation": 0.3}))
    size = tmp_path / "training-size.json"
    size.write_text(json.dumps({"training_size": 1850}))
    out = tmp_path / "admissible-partitions-org.json"
    argv = ["admissible_partitions", "--root", str(tmp_path), "--org", "org"]
    argv += ["--reference", str(reference), "--training-size", str(size), "--out", str(out)]
    monkeypatch.setattr(sys, "argv", [*argv, *flags])
    ap.main()
    return out


def test_a_full_search_writes_the_list_under_its_own_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _main(tmp_path, monkeypatch, {2, 3, 5, 7}, "--count", "3", "--batch", "4")
    report = json.loads(out.read_text())
    assert report["admissible"] == [2, 3, 5]
    # Seed 6, 7 and 8 ran only because the batch was parallel; the record ends at seed 5.
    assert [c["seed"] for c in report["checked"]] == [1, 2, 3, 4, 5]
    assert "refused" not in report
    assert not out.with_name(out.stem + "-refused.json").exists()


def test_a_short_search_is_refused_under_its_own_name_with_every_seed_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flags = ("--count", "3", "--batch", "4", "--max-seed", "6")
    with pytest.raises(SystemExit, match="fewer than 3 admissible partitions by seed 8"):
        _main(tmp_path, monkeypatch, {2}, *flags)
    out = tmp_path / "admissible-partitions-org.json"
    assert not out.exists()
    report = json.loads((tmp_path / "admissible-partitions-org-refused.json").read_text())
    assert report["admissible"] == [2]
    assert [c["seed"] for c in report["checked"]] == list(range(1, 9))
    assert report["refused"] == {"count": 3, "max_seed": 6, "last_seed": 8}


def test_a_search_with_no_admissible_seed_is_still_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SystemExit):
        _main(tmp_path, monkeypatch, set(), "--count", "1", "--batch", "2", "--max-seed", "2")
    report = json.loads((tmp_path / "admissible-partitions-org-refused.json").read_text())
    assert report["admissible"] == []
    assert [c["seed"] for c in report["checked"]] == [1, 2]
