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


def test_runs_decoded_at_different_penalties_are_not_seeds_of_one_study(tmp_path: Path) -> None:
    """A run from before penalties were recorded decoded at the checkpoint's 1.1."""
    unrecorded, explicit, off = _run(1, "/r"), _run(2, "/r"), _run(2, "/r")
    explicit["repetition_penalty"], off["repetition_penalty"] = [1.1], [1.0]
    assert merge(_write(tmp_path, [unrecorded, explicit]))[1] == (1, 2)
    with pytest.raises(SystemExit, match="repetition_penalty"):
        merge(_write(tmp_path, [unrecorded, off]))


def _stored_adapter(tmp_path: Path, *, rank: int = 32) -> tuple[Path, Path]:
    run = _run(1, "/r") | {"train_size": 10, "lora_rank": 32}
    run["outcome_neutral"] = {
        "checks": [{"name": "manipulation:openstack-a|s1", "passed": True, "evidence": {"x": 1}}]
    }
    stored = tmp_path / "stored.json"
    stored.write_text(json.dumps(run))
    adapter = tmp_path / "openstack-a-s1"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text(json.dumps({"r": rank}))
    (adapter / "adapter_model.safetensors").write_bytes(b"weights")
    return stored, adapter


def test_a_reused_adapter_carries_its_stored_report_and_test_3(tmp_path: Path) -> None:
    from sphragis.experiment.runs import reused_adapter

    stored, adapter = _stored_adapter(tmp_path)
    report, check = reused_adapter(stored, "openstack-a-s1", adapter, train_size=10, rank=32)
    assert report["items"] == 10 and report["reused_from"] == str(stored)
    assert check["passed"] and check["evidence"]["x"] == 1
    assert check["evidence"]["adapter_sha256"] == report["adapter_sha256"]


@pytest.mark.parametrize(
    ("key", "train_size", "rank", "saved_rank"),
    [
        ("openstack-b-s1", 10, 32, 32),  # the stored run never trained it
        ("openstack-a-s1", 20, 32, 32),  # another training size
        ("openstack-a-s1", 10, 256, 32),  # another rank asked for
        ("openstack-a-s1", 10, 32, 256),  # the saved adapter is another rank
    ],
)
def test_a_reused_adapter_from_another_configuration_is_refused(
    tmp_path: Path, key: str, train_size: int, rank: int, saved_rank: int
) -> None:
    from sphragis.experiment.runs import reused_adapter

    stored, adapter = _stored_adapter(tmp_path, rank=saved_rank)
    with pytest.raises(ValueError):
        reused_adapter(stored, key, adapter, train_size=train_size, rank=rank)
