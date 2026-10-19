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
from typing import Any, NamedTuple

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


#: Matched case-insensitively, optionally gzipped; the line-delimited kinds hold a document a line.
JSON_SUFFIXES = (".json", ".jsonl", ".ndjson")
LINE_SUFFIXES = (".jsonl", ".ndjson")
#: A key this long in words is prose; the longest id or label a tracked file keys on is six.
PROSE_WORDS = 8


def _stem(path: Path) -> str:
    return path.name.lower().removesuffix(".gz")


def _is_json(path: Path) -> bool:
    return _stem(path).endswith(JSON_SUFFIXES)


def _documents(path: Path) -> Iterator[Any]:
    raw = path.read_bytes()
    if path.name.lower().endswith(".gz"):
        raw = gzip.decompress(raw)
    if _stem(path).endswith(LINE_SUFFIXES):
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


def _counts_only(value: Any) -> bool:
    """The published shape of a comment list: objects of counts, so no comment text anywhere."""
    return isinstance(value, list) and all(
        isinstance(row, Mapping)
        and all(isinstance(n, int) and not isinstance(n, bool) for n in row.values())
        for row in value
    )


def _unfit(node: Any, path: str = "") -> Iterator[str]:
    """Digest fields not holding digests, comment lists holding comments, and prose as a key."""
    if isinstance(node, Mapping):
        for k, v in node.items():
            if len(k.split()) >= PROSE_WORDS:
                yield f"{path}/{k[:40]} is prose held as a key"
            if k.endswith(SUFFIX) and not all(DIGEST.fullmatch(t) for t in _strings(v)):
                yield f"{path}/{k} is not a digest"
            elif k in COMMENT_LISTS and not _counts_only(v):
                yield f"{path}/{k} carries comment text"
            else:
                yield from _unfit(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _unfit(v, f"{path}/{i}")


class Scan(NamedTuple):
    found: dict[str, list[str]]
    read: list[Path]


def scan(paths: list[Path]) -> Scan:
    """Per JSON file among the paths, where it carries model text, a bad digest or comment text,
    and which files were read."""
    found: dict[str, list[str]] = {}
    read: list[Path] = []
    for path in filter(_is_json, paths):
        read.append(path)
        where: list[str] = []
        for i, document in enumerate(_documents(path)):
            where += [f"{i}{p}" for p in text_left(document)]
            where += _unfit(document)
        if where:
            found[str(path.name)] = where[:3]
    return Scan(found, read)


def offending(paths: list[Path]) -> dict[str, list[str]]:
    return scan(paths).found


def test_no_tracked_json_carries_withheld_text() -> None:
    assert not (found := offending(_tracked())), (
        f"files carrying model text; run scripts/withhold_text.py: {found}"
    )


def test_the_guard_reads_every_json_file_in_the_repo() -> None:
    tracked = _tracked()
    read = scan(tracked).read
    assert set(read) == {p for p in tracked if _is_json(p)}
    assert {p.suffix for p in read} >= {".json", ".jsonl"}


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
    for i, shape in enumerate(
        (
            [{"text": "typo", "changes": 3}],
            ["typo"],
            [{"comment": "typo", "changes": 3}],
            {"typo": 3},
        )
    ):
        audit = {"candidate_unregistered_bots": shape}
        assert offending([_write(tmp_path / f"data-audit-{i}.json", audit)]), shape
    for i, ok in enumerate(([], [{"changes": 3}, {"changes": 25}])):
        audit = {"candidate_unregistered_bots": ok}
        assert not offending([_write(tmp_path / f"ok-{i}.json", audit)]), ok


def test_rules_read_from_a_written_guide_pass(tmp_path: Path) -> None:
    guide = {"kind": "guide", "map_lists": ["- r"], "reduce_answer": None, "rules": ["- r"]}
    path = _write(tmp_path / "rules-guide-x.json", {"files": {"o": guide, "file": "a/path.json"}})
    assert not offending([path])


#: An organization token in a result's file name, for the hosts opened by written grant.
GRANTED = re.compile(r"(?:^|[-_])(?:qt|qtfull|chromium)(?:[-_.]|$)")
#: A generation row's text, plain or digested: a digest of Qt's or Chromium's predictions is
#: derived from the text the grants exclude, so those runs stay out of git either way.
PREDICTION = re.compile(r'"prediction(?:_hmac)?"')

#: Committed before the rule, from Qt's records held from before its grant; their predictions are
#: published as digests (research log, 2026-10-19).
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


def _digested(node: Any) -> bool:
    """Whether a document carries a `withheld_text` block or any `*_hmac` key."""
    if isinstance(node, Mapping):
        return any(
            k == "withheld_text" or k.endswith(SUFFIX) or _digested(v) for k, v in node.items()
        )
    return isinstance(node, list) and any(_digested(v) for v in node)


def granted_offending(paths: list[Path]) -> list[str]:
    """Granted-host JSON files outside the exceptions that carry predictions or any digest."""
    results = ROOT / "datasets" / "results"
    return [
        p.name
        for p in filter(_is_json, paths)
        if GRANTED.search(p.name)
        and not (p.name in BEFORE_THE_RULE and p.parent == results)
        and any(_digested(d) or PREDICTION.search(json.dumps(d)) for d in _documents(p))
    ]


def test_no_granted_host_result_carries_predictions_or_digests() -> None:
    offending = granted_offending(_tracked())
    assert not offending, f"granted-host files carrying predictions or digests: {offending}"


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
        "retrieval-halves-chromium-p4.json",
        "rules-halves-chromium-p4.json",
        "rules-reviews-qt-p4.json",
        "rules-reviews-chromium-p1.json",
    )
    for name in names:
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", f"datasets/results/{name}"], cwd=ROOT, check=False
        )
        assert ignored.returncode == 0, f"{name} is not ignored"


def _carrying(tmp_path: Path, name: str, document: Any) -> list[str]:
    return granted_offending([_write(tmp_path / name, document)])


def test_a_digested_granted_host_file_of_any_shape_is_refused(tmp_path: Path) -> None:
    shapes = {
        "rules-reviews-qt-p1.json": {"files": {"qt-a": {"file_hmac": "a" * 64}}},
        "determinism-sym-0-gh-qt-001.json": {"passes_hmac": {"bf16": {"a": "a" * 64}}},
        "rules-reviews-chromium-p1.json": {"withheld_text": {"texts": 1}, "x": 1},
        "rq1-windows-chromium-s9.json": {"rows": [{"reduce_answer_hmac": "a" * 64}]},
    }
    for name, document in shapes.items():
        assert _carrying(tmp_path, name, document) == [name], name
    assert not _carrying(tmp_path, "coverage-qt.json", {"estimate": 0.5})
    assert not _carrying(tmp_path, "rules-reviews-openstack-p1.json", {"a_hmac": "a" * 64})


def test_a_granted_host_exception_name_elsewhere_is_not_exempt(tmp_path: Path) -> None:
    name = next(iter(BEFORE_THE_RULE))
    assert _carrying(tmp_path / "docs", name, {"prediction_hmac": "a" * 64}) == [name]


def test_a_json_file_is_read_whatever_its_case_or_line_layout(tmp_path: Path) -> None:
    upper = _write(tmp_path / "RUN.JSON", {"prediction": "t"})
    lines = tmp_path / "rows.ndjson"
    lines.write_text(json.dumps({"id": "a", "prediction": "t"}) + "\n")
    packed = tmp_path / "rows.NDJSON.GZ"
    packed.write_bytes(gzip.compress(lines.read_bytes()))
    for path in (upper, lines, packed):
        result = scan([path])
        assert result.read == [path] and result.found, path.name


def test_a_file_the_guard_does_not_read_is_reported_unread(tmp_path: Path) -> None:
    other = tmp_path / "notes.txt"
    other.write_text("x")
    assert scan([other, _write(tmp_path / "a.json", {})]).read == [tmp_path / "a.json"]


def test_text_held_as_dict_keys_is_caught(tmp_path: Path) -> None:
    comment = "please use a context manager here instead of closing the file by hand"
    for i, document in enumerate(
        ({comment: 1}, {"rows": [{"summary": {comment: ["x"]}}]}, {"prediction": {comment: 0}})
    ):
        assert offending([_write(tmp_path / f"keys-{i}.json", document)]), document
    ids = {
        "openstack:Ib3c:groups/Wikimedia/ Lingua.yaml:1:2": 1,
        "same organization and content": 2,
    }
    assert not offending([_write(tmp_path / "ids.json", ids)])


def test_a_distillation_of_only_rules_and_file_is_caught(tmp_path: Path) -> None:
    distilled = {"kind": "reviews", "rules": ["- r"], "file": "- r"}
    assert offending([_write(tmp_path / "notes.json", {"files": {"o": distilled}})])


def test_a_renamed_generation_field_is_caught(tmp_path: Path) -> None:
    for i, field in enumerate(("output", "outputs", "generation", "generations")):
        document = {"rows": [{field: ["t"] if field.endswith("s") else "t"}]}
        assert offending([_write(tmp_path / f"renamed-{i}.json", document)]), field


def test_a_guide_holding_text_is_not_exempt(tmp_path: Path) -> None:
    base = {"kind": "guide", "map_lists": ["- r"], "reduce_answer": None, "rules": ["- r"]}
    for i, bad in enumerate(
        (
            {"reduce_answer": "- a rule from comments"},
            {"evidence": ["a comment"]},
            {"recurrence": [{"rule": "a comment"}]},
        )
    ):
        guide = {**base, **bad}
        assert offending([_write(tmp_path / f"guide-{i}.json", {"files": {"o": guide}})]), bad
    real = {**base, "evidence": [], "recurrence": []}
    assert not offending([_write(tmp_path / "real.json", {"files": {"o": real}})])
