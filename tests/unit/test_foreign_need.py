"""What a foreign pool must hold: the C++ and other examples of every sibling draw."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("foreign_need", ROOT / "scripts" / "foreign_need.py")
assert _spec is not None and _spec.loader is not None
need = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(need)

SOURCE = "/data/gerrit-granted"


def _partition(tmp_path: Path, seed: int, cpp: dict[str, int], **override) -> None:
    root = tmp_path / f"p{seed}"
    root.mkdir()
    manifest = {
        "source_org": "qt",
        "partition_seed": seed,
        "source_root": SOURCE,
        "plant": None,
        "dedup_org": {"exact": 0},
        "names": ["qt-a", "qt-b"],
        "train_window": "train",
        **override,
    }
    (root / "placebo.json").write_text(json.dumps(manifest))
    (root / "rows.json").write_text(json.dumps(cpp))


def _window_split(root: Path, name: str):
    cpp = json.loads((Path(root) / "rows.json").read_text())[name]
    rows = [
        {"id": f"{name}:{i}", "path": "src/a.cpp" if i < cpp else "src/a.py"} for i in range(10)
    ]
    return rows, [], {}


@pytest.fixture(autouse=True)
def _rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(need, "window_split", _window_split)


def _main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seeds: list[int]) -> dict:
    admissible = tmp_path / "admissible.json"
    admissible.write_text(
        json.dumps({"org": "qt", "size_floor": 6, "root": SOURCE, "admissible": seeds})
    )
    out = tmp_path / "out.json"
    argv = ["foreign_need", "--admissible", str(admissible), "--partitions", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", [*argv, "--out", str(out)])
    need.main()
    return json.loads(out.read_text())


def test_need_is_the_largest_count_of_each_stratum_over_every_draw(tmp_path, monkeypatch) -> None:
    _partition(tmp_path, 4, {"qt-a": 9, "qt-b": 2})
    _partition(tmp_path, 7, {"qt-a": 5, "qt-b": 0})
    report = _main(tmp_path, monkeypatch, [4, 7])
    for halves in report["draws"].values():
        for half in halves.values():
            assert half["drawn"] == 6
            assert half["in_language"] + half["other"] == 6
    largest_cpp = max(h["in_language"] for hs in report["draws"].values() for h in hs.values())
    largest_other = max(h["other"] for hs in report["draws"].values() for h in hs.values())
    assert report["need"]["in_language"]["examples"] == largest_cpp
    assert report["need"]["other"]["examples"] == largest_other
    assert report["need"]["other"] == {"examples": 6, "seed": 7, "half": "qt-b"}
    assert report["partitions"] == 2
    assert report["population"] == "cpp"


@pytest.mark.parametrize(
    ("override", "names"),
    [
        ({"partition_seed": 5}, "partition_seed"),
        ({"source_org": "openstack"}, "source_org"),
        ({"source_root": "/elsewhere"}, "source_root"),
        ({"plant": {"fraction": 0.5}}, "plant"),
        ({"dedup_org": None}, "dedup-org"),
        ({"names": ["x", "y"]}, "names"),
        ({"train_window": "dev"}, "train_window"),
    ],
)
def test_a_partition_built_otherwise_is_refused(tmp_path, monkeypatch, override, names) -> None:
    _partition(tmp_path, 4, {"qt-a": 3, "qt-b": 3}, **override)
    with pytest.raises(ValueError, match=names):
        _main(tmp_path, monkeypatch, [4])


def test_a_partition_built_from_the_same_root_written_otherwise_is_read(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "data")
    _partition(tmp_path, 4, {"qt-a": 3, "qt-b": 3}, source_root=str(tmp_path / "link"))
    admissible = tmp_path / "admissible.json"
    admissible.write_text(
        json.dumps(
            {"org": "qt", "size_floor": 6, "root": str(tmp_path / "data"), "admissible": [4]}
        )
    )
    out = tmp_path / "out.json"
    argv = ["foreign_need", "--admissible", str(admissible), "--partitions", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", [*argv, "--out", str(out)])
    need.main()
    assert json.loads(out.read_text())["partitions"] == 1


def test_the_cut_to_n_is_seeded_as_the_partition_runs_seed_it() -> None:
    pilot = (ROOT / "scripts" / "rq1_pilot.py").read_text()
    default = re.search(r'"--split-seed", type=int, default=(\d+)\)', pilot)
    assert default is not None and default.group(1) == str(need.SPLIT_SEED)
    assert "equalize_training(train_rows, seed=args.split_seed" in pilot
    assert "--split-seed" not in (ROOT / "scripts" / "partition_run.sbatch").read_text()


def _stored(
    tmp_path: Path, name: str, seed: int, train: int, plant: str = "", size: int = 6
) -> Path:
    root = f"/data/corpus-partition-qt-p{seed}-s2-n1850{plant}"
    corpora = {
        h: {
            "source": f"train -> dev windows under {root}/{h}/refined",
            "train_examples": train,
            "held_out_examples": 0,
        }
        for h in ("qt-a", "qt-b")
    }
    path = tmp_path / name
    path.write_text(json.dumps({"corpora": corpora, "split_seed": 0, "train_size": size}))
    return path


def test_a_rebuilt_partition_is_checked_against_its_stored_run(tmp_path, monkeypatch) -> None:
    _partition(tmp_path, 4, {"qt-a": 3, "qt-b": 3})
    admissible = tmp_path / "admissible.json"
    admissible.write_text(
        json.dumps({"org": "qt", "size_floor": 6, "root": SOURCE, "admissible": [4]})
    )

    def run(*stored: Path) -> dict:
        out = tmp_path / "out.json"
        argv = ["foreign_need", "--admissible", str(admissible), "--partitions", str(tmp_path)]
        monkeypatch.setattr(sys, "argv", [*argv, "--out", str(out), "--runs", *map(str, stored)])
        need.main()
        return json.loads(out.read_text())

    good = _stored(tmp_path, "good.json", 4, train=10)
    assert run(good)["checked_against_runs"] == [str(good)]
    for stored, message in (
        (_stored(tmp_path, "short.json", 4, train=9), "train_examples 9 recorded, 10 rebuilt"),
        (_stored(tmp_path, "other.json", 7, train=10), "partition 7, not one read here"),
        (_stored(tmp_path, "planted.json", 4, 10, "-plant0.5"), "not one unplanted"),
        (_stored(tmp_path, "bigger.json", 4, 10, size=7), "seed and size"),
    ):
        with pytest.raises(SystemExit, match=message):
            run(stored)
