from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sphragis.experiment.runs import merge


def _run(seed: int, root: str, *, org: str = "openstack-a", held_out: int = 3) -> dict[str, Any]:
    ids = [f"openstack:I{n}:f.py:1:{n}" for n in range(held_out)]
    arm = [{"id": i, "change_id": i.split(":")[1], "exact_match": 1} for i in ids]
    return {
        "model_id": "m",
        "split_seed": 0,
        "equalize_train": True,
        "max_new_tokens": 64,
        "seeds": [seed],
        "corpora": {
            org: {
                "source": f"train -> dev windows under {root}/{org}/refined",
                "train_examples_equalized": 10,
                "held_out_examples": held_out,
            }
        },
        "training": {f"{org}-s{seed}": {"items": 10, "steps": 5}},
        "results": {f"adapter:{org}|{org}|s{seed}": arm, f"base|{org}": arm},
    }


def _write(tmp_path: Path, runs: list[dict[str, Any]]) -> list[Path]:
    paths = []
    for n, run in enumerate(runs):
        path = tmp_path / f"run{n}.json"
        path.write_text(json.dumps(run))
        paths.append(path)
    return paths


def test_runs_built_into_separate_roots_are_refused_unless_the_roots_are_verified(
    tmp_path: Path,
) -> None:
    paths = _write(tmp_path, [_run(1, "/data/root"), _run(2, "/data/root-s2")])
    with pytest.raises(SystemExit, match="differs on \\['corpora'\\]"):
        merge(paths)
    results, seeds = merge(paths, roots_verified=True)
    assert seeds == (1, 2)
    assert set(results) == {
        "adapter:openstack-a|openstack-a|s1",
        "adapter:openstack-a|openstack-a|s2",
        "base|openstack-a",
    }


def test_verified_roots_still_refuse_a_different_corpus_below_the_root(tmp_path: Path) -> None:
    other = _run(2, "/data/root-s2")
    other["corpora"]["openstack-a"]["source"] = "train -> dev windows under /data/root-s2/x/refined"
    paths = _write(tmp_path, [_run(1, "/data/root"), other])
    with pytest.raises(SystemExit, match="openstack-a's directory"):
        merge(paths, roots_verified=True)


def test_verified_roots_still_refuse_a_different_held_out_set(tmp_path: Path) -> None:
    paths = _write(tmp_path, [_run(1, "/data/root"), _run(2, "/data/root-s2", held_out=4)])
    with pytest.raises(SystemExit, match="corpora"):
        merge(paths, roots_verified=True)


def test_the_same_seed_twice_is_refused(tmp_path: Path) -> None:
    paths = _write(tmp_path, [_run(1, "/data/root"), _run(1, "/data/root")])
    with pytest.raises(SystemExit, match="same seed"):
        merge(paths)


def test_below_root_keeps_the_mode_and_windows_and_removes_only_the_root() -> None:
    from sphragis.experiment.runs import _below_root

    assert _below_root("train -> dev windows under /r1/openstack-a/refined", "openstack-a") == (
        "train -> dev windows under openstack-a/refined"
    )
    assert _below_root("holdout seed 0 over /x/y/openstack-a/refined", "openstack-a") == (
        "holdout seed 0 over openstack-a/refined"
    )
    assert _below_root("windows under /a/openstack-a/b/openstack-a/refined", "openstack-a") == (
        "windows under openstack-a/refined"
    )
    with pytest.raises(SystemExit):
        _below_root("windows under /r/openstack-a/refined", "openstack")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "train -> test windows under /data/root-s2/openstack-a/refined"),
        ("source", "train -> dev windows under /data/root-s2/openstack-a/examples"),
        ("train_examples_equalized", 9),
        ("examples", 99),
        ("dedup_removed", {"exact": 1}),
    ],
)
def test_verified_roots_refuse_any_difference_but_the_root(
    tmp_path: Path, field: str, value: Any
) -> None:
    first, second = _run(1, "/data/root"), _run(2, "/data/root-s2")
    for run in (first, second):
        run["corpora"]["openstack-a"] |= {"examples": 50, "dedup_removed": {"exact": 0}}
    second["corpora"]["openstack-a"][field] = value
    with pytest.raises(SystemExit, match="corpora"):
        merge(_write(tmp_path, [first, second]), roots_verified=True)
