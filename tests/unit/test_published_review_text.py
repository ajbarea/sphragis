"""No tracked result carries model text that could repeat a review comment.

A generation conditioned on review comments, or a summary distilled from them, can repeat a
comment word for word, and no review text is published (`docs/protocol.md`). Published results
carry a keyed digest of each such text instead (`sphragis.measure.withhold`,
`scripts/withhold_text.py`); the full texts stay in the cluster's results root. Qt's and
Chromium's generation runs stay out of git altogether, as their written grants ask (research log,
2026-10-18 and 2026-10-19).
"""

from __future__ import annotations

import gzip
import json
import re
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from sphragis.measure.withhold import SUFFIX, text_left

ROOT = Path(__file__).resolve().parents[2]
DIGEST = re.compile(r"[0-9a-f]{64}")
#: A bot-audit list of short real review comments, which no published artifact carries.
COMMENT_LISTS = frozenset({"candidate_unregistered_bots"})


def _tracked() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [ROOT / line for line in out.split("\0") if line]


def _documents(path: Path) -> Iterator[Any]:
    raw = path.read_bytes()
    if path.name.endswith(".gz"):
        raw = gzip.decompress(raw)
    if path.name.removesuffix(".gz").endswith(".jsonl"):
        yield from (json.loads(line) for line in raw.decode().splitlines() if line.strip())
    else:
        yield json.loads(raw)


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _unfit(node: Any, path: str = "") -> Iterator[str]:
    """Digest fields not holding digests, and comment lists holding comments."""
    if isinstance(node, Mapping):
        for k, v in node.items():
            if k.endswith(SUFFIX) and not all(DIGEST.fullmatch(t) for t in _strings(v)):
                yield f"{path}/{k} is not a digest"
            elif k in COMMENT_LISTS and any(isinstance(r, Mapping) and "text" in r for r in v):
                yield f"{path}/{k} carries comment text"
            else:
                yield from _unfit(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _unfit(v, f"{path}/{i}")


def offending(paths: list[Path]) -> dict[str, list[str]]:
    """Per file under the paths, where it carries model text, a bad digest or comment text."""
    found: dict[str, list[str]] = {}
    for path in paths:
        name = path.name.removesuffix(".gz")
        if not name.endswith((".json", ".jsonl")):
            continue
        where: list[str] = []
        for i, document in enumerate(_documents(path)):
            where += [f"{i}{p}" for p in text_left(document)]
            where += _unfit(document)
        if where:
            found[str(path.name)] = where[:3]
    return found


def test_no_tracked_json_carries_withheld_text() -> None:
    assert not (found := offending(_tracked())), (
        f"files carrying model text; run scripts/withhold_text.py: {found}"
    )


def test_the_guard_reads_every_json_and_jsonl_file_in_the_repo() -> None:
    tracked = {p.suffix for p in _tracked() if p.suffix in {".json", ".jsonl", ".gz"}}
    assert {".json", ".jsonl"} <= tracked and ".gz" not in tracked


def _write(path: Path, document: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(map(json.dumps, document)) if path.suffix == ".jsonl" else json.dumps(document)
    )
    return path


def test_the_check_fires_on_a_published_run_shape(tmp_path: Path) -> None:
    """The guard is only as good as its reach: each shape a run is published in trips it."""
    shapes = {
        "rq1-partition-openstack-p1-s1-n1850-rp1.0.json": {
            "results": {"base|a": [{"prediction": "x"}]}
        },
        "prompt-format-probe.json": {"raw": [{"prediction": "x"}]},
        "determinism-sym-0-gh-a-001-rp1.0.json": {
            "passes": {"bf16": {"openstack:I1:f.py:1:2": "x"}}
        },
        "rules-reviews-openstack-p10.json": {
            "files": {"openstack-a": {"map_lists": [], "file": "- a rule"}}
        },
    }
    for name, document in shapes.items():
        assert offending([_write(tmp_path / name, document)]), name


def test_a_jsonl_sidecar_with_raw_predictions_is_caught(tmp_path: Path) -> None:
    rows = _write(tmp_path / "retrieval-x.rows.jsonl", [{"id": "a", "prediction": "text"}])
    assert offending([rows])


def test_a_compressed_result_is_read_not_skipped(tmp_path: Path) -> None:
    path = tmp_path / "run.json.gz"
    path.write_bytes(gzip.compress(json.dumps({"results": [{"prediction": "t"}]}).encode()))
    assert offending([path])


def test_a_file_outside_the_results_directory_is_caught(tmp_path: Path) -> None:
    assert offending([_write(tmp_path / "docs" / "notes.json", {"prediction": "t"})])


def test_passes_in_a_file_not_named_for_determinism_is_caught(tmp_path: Path) -> None:
    assert offending([_write(tmp_path / "run.json", {"passes": {"bf16": {"a": "t"}}})])


def test_a_distillation_saved_under_another_name_is_caught(tmp_path: Path) -> None:
    distilled = {"kind": "reviews", "map_lists": ["- r"], "rules": ["- r"], "file": "- r"}
    assert offending([_write(tmp_path / "notes.json", {"files": {"o": distilled}})])


def test_a_renamed_prediction_field_is_caught(tmp_path: Path) -> None:
    assert offending([_write(tmp_path / "run.json", {"rows": [{"predictions": ["t"]}]})])


def test_raw_text_in_a_digest_field_is_caught(tmp_path: Path) -> None:
    path = _write(tmp_path / "run.json", {"rows": [{"prediction_hmac": "a comment"}]})
    assert offending([path])
    assert not offending([_write(tmp_path / "ok.json", {"prediction_hmac": "a" * 64})])
    assert offending([_write(tmp_path / "upper.json", {"prediction_hmac": "A" * 64})])


def test_bot_audit_comment_text_is_caught(tmp_path: Path) -> None:
    audit = {"candidate_unregistered_bots": [{"text": "typo", "changes": 3}]}
    assert offending([_write(tmp_path / "data-audit-x.json", audit)])
    assert not offending([_write(tmp_path / "ok.json", {"candidate_unregistered_bots": []})])


def test_rules_read_from_a_written_guide_pass(tmp_path: Path) -> None:
    guide = {"kind": "guide", "map_lists": ["- r"], "reduce_answer": None, "rules": ["- r"]}
    path = _write(tmp_path / "rules-guide-x.json", {"files": {"o": guide, "file": "a/path.json"}})
    assert not offending([path])


#: An organization token in a result's file name, for the hosts opened by written grant.
GRANTED = re.compile(r"(?:^|[-_])(?:qt|qtfull|chromium)(?:[-_.]|$)")
#: A generation row's text, plain or digested: a digest of Qt's or Chromium's predictions is
#: derived from the text the grants exclude, so those runs stay out of git either way.
PREDICTION = re.compile(r'"prediction(?:_hmac)?"')

#: Committed before the rule, from Qt's records held from before its grant. Whether they keep
#: their predictions is AJ's call (ROADMAP, "predictions in published generation runs").
BEFORE_THE_RULE = frozenset(
    {
        "projects-qt-creator_qt-creator-qt_qtbase-n788.json",
        "projects-qt-creator_qt-creator-qt_qtbase.json",
        "projects-qt-creator_qt-creator-qt_qtdeclarative.json",
        "projects-qt_qtbase-qt_qtdeclarative.json",
        "rq1-placebo-qt-s2.json",
        "rq1-placebo-qt-s3.json",
        "rq1-placebo-qt.json",
        "rq1-windows-qtfull-fp32-s2.json",
        "rq1-windows-qtfull-fp32-s3.json",
        "rq1-windows-qtfull-fp32.json",
        "rq1-windows-qtfull-s2.json",
        "rq1-windows-qtfull-s3.json",
        "rq1-windows-qtfull.json",
    }
)


def _tracked_results() -> dict[str, Path]:
    return {p.name: p for p in _tracked() if p.parent == ROOT / "datasets" / "results"}


def test_no_granted_host_result_carries_predictions() -> None:
    offending = [
        name
        for name, path in _tracked_results().items()
        if GRANTED.search(name)
        and name not in BEFORE_THE_RULE
        and PREDICTION.search(path.read_text())
    ]
    assert not offending, f"granted-host results carrying predictions: {offending}"


def test_the_listed_exceptions_still_exist_and_still_carry_predictions() -> None:
    """A file stripped or removed leaves the list, so the list never grows stale and silent."""
    tracked = _tracked_results()
    for name in BEFORE_THE_RULE:
        assert name in tracked, f"{name} is no longer tracked; drop it from BEFORE_THE_RULE"
        assert PREDICTION.search(tracked[name].read_text()), f"{name} carries none; drop it"


def test_a_digested_granted_host_run_is_refused() -> None:
    assert GRANTED.search("rq1-partition-qt-p4-n1850-rp1.0.json")
    assert PREDICTION.search('{"prediction_hmac": "ab"}') and PREDICTION.search(
        '{"prediction": "x"}'
    )
    assert not PREDICTION.search('{"predictions_kept": 3}')


def test_a_granted_host_generation_run_is_ignored() -> None:
    names = (
        "rq1-partition-qt-p4-n1850-rp1.0.json",
        "rq1-partition-chromium-p1-n1850.json",
        "rq1-windows-chromium-s2.json",
        "retrieval-halves-qt-p4.json",
        "rules-halves-qt-p4.json",
    )
    for name in names:
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", f"datasets/results/{name}"], cwd=ROOT, check=False
        )
        assert ignored.returncode == 0, f"{name} is not ignored"
