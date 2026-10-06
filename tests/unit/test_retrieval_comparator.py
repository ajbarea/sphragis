"""scripts/retrieval_comparator.py and scripts/retrieval_read.py: the retrieval comparator's wiring.

Generation is exercised as a dry run, which builds every pool and prompt without a model, over
partition corpora `placebo_corpus.py` builds; the reader is exercised on synthetic job reports.
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path
from types import ModuleType

import pytest

from sphragis.corpus.load import refined_dir, write_build_record, write_source_record
from sphragis.experiment.grid import EvalRun, run_id
from sphragis.experiment.holdout import equalize_training, window_split
from sphragis.experiment.retrieval import KS, PARTITIONS, arm_key, resumable

ROOT = Path(__file__).resolve().parents[2]


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


comparator = _script("retrieval_comparator")
reader = _script("retrieval_read")
placebo = _script("placebo_corpus")


def _run(module: ModuleType, argv: list[str]) -> None:
    saved = sys.argv
    sys.argv = [module.__name__, *argv]
    try:
        module.main()
    finally:
        sys.argv = saved


def _words(rng: random.Random, n: int) -> str:
    return " ".join(f"w{rng.randrange(10**9)}" for _ in range(n))


def _source(root: Path, org: str, seed: int) -> None:
    """Six projects with training-window and dev-window changes, no duplicates, and a seventh
    with dev-window changes only, which no partition keeps."""
    rng = random.Random(seed)
    rows = []
    for i in range(106):
        day = "2025-01-10" if i < 72 else "2025-09-15"
        rows.append(
            {
                "id": f"{org}-{i}",
                "change_id": f"{org}-c{i}",
                "project": f"{org}/p{i % 6}" if i < 96 else f"{org}/dev-only",
                "org": org,
                "created": f"{day} 00:00:00.000000000",
                "comments": [_words(rng, 6)],
                "before": _words(rng, 12),
                "after": _words(rng, 12),
            }
        )
    text = "".join(json.dumps(r) + "\n" for r in rows)
    for month in ("2025-01.jsonl", "2025-09.jsonl"):
        part = "".join(
            line + "\n" for line in text.splitlines() if month[:7] in json.loads(line)["created"]
        )
        built = root / org / "examples" / month
        built.parent.mkdir(parents=True, exist_ok=True)
        built.write_text(part)
        write_build_record(built, None, complete=True)
        refined = refined_dir(root, org) / month
        refined.parent.mkdir(parents=True, exist_ok=True)
        refined.write_text(part)
        write_source_record(root, org, built, refined)


def _partition(src: Path, out: Path, org: str, seed: int) -> None:
    argv = ["--root", str(src), "--org", org, "--out-root", str(out), "--dedup-org"]
    _run(placebo, [*argv, "--partition-seed", str(seed)])


ORDER = [3, 1, 4, 5, 9, 2, 6, 8, 7, 10]
SIZE = 20


def _run_file(results: Path, org: str, position: int, root: Path) -> None:
    """The adapters' partition run on the position-th partition, named as its job names it."""
    seeds = "" if position == 1 else f"-s{position}"
    halves = (f"{org}-a", f"{org}-b")
    corpora = {h: {"source": f"train -> dev windows under {root}/{h}/refined"} for h in halves}
    run = {"seeds": [position], "train_size": SIZE, "corpora": corpora, "results": {}}
    name = f"rq1-partition-{org}-p{ORDER[position - 1]}{seeds}-n{SIZE}.json"
    (results / name).write_text(json.dumps(run))


@pytest.fixture
def corpora(tmp_path: Path) -> dict[str, Path]:
    """Both organizations' first ten partitions, built as partition_run.sbatch builds them, and
    the adapters' run on each, naming the corpus it read."""
    paths = {"src": tmp_path / "src", "results": tmp_path / "results"}
    paths["results"].mkdir()
    for org, seed in (("openstack", 1), ("wikimedia", 2)):
        _source(paths["src"], org, seed)
        for position, partition in enumerate(ORDER, start=1):
            root = tmp_path / f"corpus-partition-{org}-p{partition}-n{SIZE}"
            _partition(paths["src"], root, org, partition)
            _run_file(paths["results"], org, position, root)
        paths[org] = tmp_path / f"corpus-partition-{org}-p{ORDER[0]}-n{SIZE}"
        listing = {"org": org, "admissible": ORDER, "size_floor": SIZE}
        paths[f"{org}-list"] = tmp_path / f"admissible-{org}.json"
        paths[f"{org}-list"].write_text(json.dumps(listing))
    return paths


def _halves(paths: dict[str, Path], out: Path, *extra: str) -> dict:
    _run(
        comparator,
        [
            "--pools", "halves", "--results", str(paths["results"]), "--org", "openstack",
            "--admissible", str(paths["openstack-list"]), "--dry-run", "--out", str(out),
            *extra,
        ],
    )  # fmt: skip
    return json.loads(out.read_text())


def test_each_pool_is_its_adapters_training_set(corpora: dict[str, Path], tmp_path: Path) -> None:
    report = _halves(corpora, tmp_path / "h.json", "--partition", str(ORDER[0]))
    train = {h: window_split(corpora["openstack"], h)[0] for h in ("openstack-a", "openstack-b")}
    # The partition run's own cut: equalized at its seed over both halves, at the list's size.
    expected = equalize_training(train, seed=0, size=SIZE)
    assert report["pool_sizes"] == {h: len(rows) for h, rows in expected.items()}
    assert report["pool_ids"] == {h: [r["id"] for r in rows] for h, rows in expected.items()}
    assert report["train_size"] == SIZE and report["partition"] == ORDER[0]
    keys = set(report["prompt_chars"])
    for k in KS:
        for pool in ("openstack-a", "openstack-b"):
            for evaluated in ("openstack-a", "openstack-b"):
                assert arm_key(k, pool, evaluated) in keys
    assert len(keys) == 2 * 2 * len(KS)
    assert all(check["passed"] for check in report["leakage"])


def test_a_partition_outside_the_first_ten_is_refused(
    corpora: dict[str, Path], tmp_path: Path
) -> None:
    with pytest.raises(SystemExit, match="not among the first"):
        _halves(corpora, tmp_path / "h.json", "--partition", "11")


def test_a_run_whose_corpus_is_another_partition_is_refused(
    corpora: dict[str, Path], tmp_path: Path
) -> None:
    # Named for partition 1, built as partition 3: the corpus's own manifest decides.
    wrong = tmp_path / f"corpus-partition-openstack-p{ORDER[1]}-x-n{SIZE}"
    _partition(corpora["src"], wrong, "openstack", ORDER[0])
    _run_file(corpora["results"], "openstack", 2, wrong)
    with pytest.raises(SystemExit, match="partition_seed"):
        _halves(corpora, tmp_path / "h.json", "--partition", str(ORDER[1]))


def _foreign(paths: dict[str, Path], out: Path, foreign_list: Path | None = None) -> dict:
    _run(
        comparator,
        [
            "--pools", "foreign", "--results", str(paths["results"]), "--org", "openstack",
            "--admissible", str(paths["openstack-list"]), "--foreign", "wikimedia",
            "--foreign-admissible", str(foreign_list or paths["wikimedia-list"]),
            "--dry-run", "--out", str(out),
        ],
    )  # fmt: skip
    return json.loads(out.read_text())


def test_the_foreign_job_prompts_every_scored_example_once_per_arm(
    corpora: dict[str, Path], tmp_path: Path
) -> None:
    report = _foreign(corpora, tmp_path / "f.json")
    halves = _halves(corpora, tmp_path / "h.json", "--partition", str(ORDER[0]))
    # Exactly what the partitions score: the dev-only project is in neither half, so not here.
    assert report["targets"] == {"openstack": sum(halves["targets"].values())}
    held_out = window_split(corpora["src"], "openstack")[1]
    assert len(held_out) - report["targets"]["openstack"] == 10
    expected = {run_id(EvalRun("base", "openstack", None))}
    expected |= {arm_key(k, p, "openstack") for k in KS for p in ("wikimedia-a", "wikimedia-b")}
    assert set(report["prompt_chars"]) == expected
    assert report["foreign_partition"] == ORDER[0]
    assert report["pool_ids"].keys() == {"wikimedia-a", "wikimedia-b"}


def test_a_foreign_pool_of_another_size_is_refused(
    corpora: dict[str, Path], tmp_path: Path
) -> None:
    larger = tmp_path / "larger.json"
    larger.write_text(json.dumps({"org": "wikimedia", "admissible": ORDER, "size_floor": 30}))
    with pytest.raises(SystemExit, match="trains at 30"):
        _foreign(corpora, tmp_path / "f.json", larger)


# The reader, on synthetic jobs: 40 examples, which half each falls in moving with the partition.
IDS = [f"x{i}" for i in range(40)]


def _row(i: str, em: float) -> dict:
    return {"id": i, "change_id": f"c{i}", "exact_match": em}


def _sides(partition: int) -> dict[str, list[str]]:
    shuffled = random.Random(partition).sample(IDS, len(IDS))
    return {"openstack-a": shuffled[:20], "openstack-b": shuffled[20:]}


def _halves_job(partition: int, own_wins: bool) -> dict:
    results = {}
    for k in KS:
        for pool in _sides(partition):
            for evaluated, ids in _sides(partition).items():
                hit = own_wins and pool == evaluated
                results[arm_key(k, pool, evaluated)] = [
                    _row(i, 1.0 if hit and int(i[1:]) % 2 == 0 else 0.0) for i in ids
                ]
    return {"pools": "halves", "partition": partition, **_fixed(), "results": results}


def _adapter_run(position: int, partition: int) -> dict:
    """The adapters' partition run: own never beats sibling."""
    results = {}
    for window, ids in _sides(partition).items():
        for trained in _sides(partition):
            results[run_id(EvalRun(f"adapter:{trained}", window, position))] = [
                _row(i, 0.0) for i in ids
            ]
    root = f"/d/corpus-partition-openstack-p{partition}-n{SIZE}"
    corpora = {h: {"source": f"train -> dev windows under {root}/{h}/refined"} for h in _sides(1)}
    return {"seeds": [position], "train_size": SIZE, "corpora": corpora, "results": results}


def _fixed() -> dict:
    return {"org": "openstack", "train_size": SIZE, "ks": list(KS), "limit": None}


def _foreign_job(drop: str | None = None) -> dict:
    ids = [i for i in IDS if i != drop]
    results = {run_id(EvalRun("base", "openstack", None)): [_row(i, 0.0) for i in ids]}
    for k in KS:
        for pool in ("wikimedia-a", "wikimedia-b"):
            results[arm_key(k, pool, "openstack")] = [_row(i, 0.0) for i in ids]
    return {"pools": "foreign", "foreign": "wikimedia", **_fixed(), "results": results}


def _read(
    tmp_path: Path,
    jobs: list[dict],
    foreign: dict,
    adapters: list[dict] | None = None,
    listing_org: str = "openstack",
) -> dict:
    adapters = adapters or [_adapter_run(n, p) for n, p in enumerate(ORDER, start=1)]
    results = tmp_path / "results"
    results.mkdir(exist_ok=True)
    halves = []
    for n, (job, run) in enumerate(zip(jobs, adapters, strict=False)):
        halves.append(tmp_path / f"h{n}.json")
        halves[-1].write_text(json.dumps(job))
        seeds = "" if n == 0 else f"-s{n + 1}"
        name = f"rq1-partition-openstack-p{ORDER[n]}{seeds}-n{SIZE}.json"
        (results / name).write_text(json.dumps(run))
    (tmp_path / "f.json").write_text(json.dumps(foreign))
    listing = tmp_path / "list.json"
    listing.write_text(json.dumps({"org": listing_org, "admissible": ORDER, "size_floor": SIZE}))
    out = tmp_path / "read.json"
    argv = ["--org", "openstack", "--foreign", "wikimedia", "--admissible", str(listing)]
    argv += ["--foreign-job", str(tmp_path / "f.json"), "--halves", *map(str, halves)]
    argv += ["--results", str(results), "--resamples", "200", "--out", str(out)]
    _run(reader, argv)
    return json.loads(out.read_text())


def test_an_own_pool_that_wins_reads_as_a_half_split_contrast(tmp_path: Path) -> None:
    report = _read(tmp_path, [_halves_job(p, True) for p in ORDER], _foreign_job())
    for k in KS:
        cell = report["ks"][str(k)]
        # Own matches every even example, sibling and foreign none: +0.5 own minus sibling and
        # own minus none, and sibling minus foreign exactly zero.
        assert cell["own_minus_sibling"]["estimate"] == pytest.approx(0.5)
        assert cell["own_minus_none"]["estimate"] == pytest.approx(0.5)
        assert cell["sibling_minus_foreign"]["estimate"] == pytest.approx(0.0)
        assert cell["own_minus_sibling"]["reading"] == "carries a half-split contrast"
        assert cell["examples"] == len(IDS) and cell["dropped"] == 0
        assert len(cell["own_minus_sibling"]["per_run"]) == PARTITIONS
    # The adapters are read over the same partitions and examples, by the same rule.
    adapters = report["adapters"]
    assert adapters["examples"] == len(IDS)
    assert len(adapters["own_minus_sibling"]["per_run"]) == PARTITIONS
    assert adapters["own_minus_sibling"]["reading"] == "carries none as large as the SESOI"


def test_pools_that_never_differ_read_as_no_contrast(tmp_path: Path) -> None:
    report = _read(tmp_path, [_halves_job(p, False) for p in ORDER], _foreign_job())
    readings = {report["ks"][str(k)]["own_minus_sibling"]["reading"] for k in KS}
    assert readings == {"carries none as large as the SESOI"}


def test_the_reader_refuses_a_foreign_job_missing_an_example(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="lacks 1"):
        _read(tmp_path, [_halves_job(p, True) for p in ORDER], _foreign_job(drop="x3"))


def test_the_reader_refuses_runs_out_of_admissible_order_or_a_dry_run(tmp_path: Path) -> None:
    swapped = [_halves_job(p, True) for p in [ORDER[1], ORDER[0], *ORDER[2:]]]
    with pytest.raises(SystemExit, match="partition"):
        _read(tmp_path, swapped, _foreign_job())
    dry = [_halves_job(p, True) for p in ORDER]
    del dry[4]["results"]
    with pytest.raises(SystemExit, match="dry run"):
        _read(tmp_path, dry, _foreign_job())


def test_the_reader_refuses_adapters_from_another_partition(tmp_path: Path) -> None:
    runs = [_adapter_run(n, p) for n, p in enumerate(ORDER, start=1)]
    runs[2] = _adapter_run(3, ORDER[3])
    with pytest.raises(SystemExit, match="admissible partition"):
        _read(tmp_path, [_halves_job(p, True) for p in ORDER], _foreign_job(), runs)


def test_the_reader_refuses_another_organizations_list(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="wikimedia's partitions"):
        _read(tmp_path, [_halves_job(p, True) for p in ORDER], _foreign_job(), None, "wikimedia")


def test_the_reader_reads_exactly_the_fixed_number_of_partitions(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="not the fixed"):
        _read(tmp_path, [_halves_job(p, True) for p in ORDER[:9]], _foreign_job())


# Resuming a generation job.
def _rows(arm: str, marks: list[tuple[str, str]]) -> list[str]:
    return [
        json.dumps({"arm": arm, "id": i, "fingerprint": f, "exact_match": 0.0}) for i, f in marks
    ]


def test_a_resume_keeps_only_arms_it_would_generate_identically() -> None:
    same, changed, short = [("x1", "f1"), ("x2", "f2")], [("x1", "g1")], [("x1", "f1")]
    lines = _rows("a", same) + _rows("b", [("x1", "old")]) + _rows("c", short)
    kept, dropped = resumable(lines, {"a": same, "b": changed, "c": same})
    assert list(kept) == ["a"] and dropped == ["b", "c"]


def test_a_line_a_kill_cut_short_drops_only_its_arm() -> None:
    marks = [("x1", "f1"), ("x2", "f2")]
    lines = _rows("a", marks) + _rows("b", marks)
    lines[-1] = lines[-1][:15]
    kept, dropped = resumable(lines, {"a": marks, "b": marks})
    assert list(kept) == ["a"] and "b" in dropped
