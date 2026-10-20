"""No tracked result carries model text that could repeat a review comment.

A generation conditioned on review comments, or a summary distilled from them, can repeat a
comment word for word, and no review text is published (`docs/protocol.md`). Published results
carry a keyed digest of each such text instead (`sphragis.measure.withhold`,
`scripts/withhold_text.py`); the full texts stay in the cluster's results root. The hosts opened
by written grant stay out of git altogether, as their grants ask (research log, 2026-10-18 and
2026-10-19). The rule itself is `withhold.refusals`; this module reads the tracked files and asks.
"""

from __future__ import annotations

import bz2
import fnmatch
import functools
import gzip
import importlib.util
import itertools
import json
import lzma
import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from sphragis.measure import withhold as w

ROOT = Path(__file__).resolve().parents[2]
RESULTS = "datasets/results"
GUIDE: dict[str, Any] = {
    "kind": "guide",
    "chunks": 1,
    "part_pages": [1],
    "map_lists": ["- r"],
    "evidence": [],
    "reduce_answer": None,
    "merge_chunks": None,
    "recurrence": [],
    "rule_pages": [1],
    "rules": ["- r"],
}

#: Read as JSON by name, in any case and under any compression; `.partial` and `.part` are the
#: sidecars a job writes before it renames them (`scripts/retrieval_comparator.py`,
#: `scripts/rules_distil.py`).
JSON_SUFFIXES = (".json", ".jsonl", ".ndjson", ".partial", ".part")
COMPRESSION = (".gz", ".bz2", ".xz", ".zst")
#: Leading bytes of each compression, read whatever the file is called.
MAGIC: Mapping[bytes, Callable[[bytes], bytes]] = {
    b"\x1f\x8b": gzip.decompress,
    b"BZh": bz2.decompress,
    b"\xfd7zXZ\x00": lzma.decompress,
    b"\x28\xb5\x2f\xfd": lambda raw: _zstd().decompress(raw),
}
#: Every other tracked suffix, from a survey of the tree; a new one fails until it is listed.
OTHER_SUFFIXES = frozenset(
    {
        ".log", ".py", ".sbatch", ".md", ".txt", ".sh", ".npz", ".yml", ".svg", ".css", ".html",
        ".js", ".toml", ".example", ".cff", ".java", ".lock", ".png", ".pdf", ".tex",
        ".gitignore", ".gitkeep", ".gitattributes", "license", "makefile",
    }
)  # fmt: skip


def _tracked() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [ROOT / line for line in out.split("\0") if line]


def _zstd() -> Any:
    try:
        from compression import zstd  # ty: ignore[unresolved-import]
    except ImportError as error:
        raise AssertionError("a zstd file is tracked: read it on Python 3.14") from error
    return zstd


def _suffix(path: Path) -> str:
    """The name's last suffix, or the whole name where it has none (`LICENSE`, `.gitignore`)."""
    return (path.suffix or path.name).lower()


def _stem(path: Path) -> str:
    name = path.name.lower()
    return name.removesuffix(next((c for c in COMPRESSION if name.endswith(c)), ""))


def _decoded(raw: bytes) -> bytes:
    for mark, decompress in MAGIC.items():
        if raw.startswith(mark):
            return decompress(raw)
    return raw


def _parsed(text: str) -> list[Any]:
    """One document, or one a line; a line that is not JSON raises."""
    try:
        return [json.loads(text)]
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def _documents(raw: bytes) -> list[Any]:
    return _parsed(_decoded(raw).decode())


def _json_looking(raw: bytes) -> bool:
    """Opens with an object or array that parses, whole or as the first line of a line file."""
    if raw[:64].lstrip()[:1] not in (b"{", b"["):
        return False
    text = raw.decode(errors="replace")
    for attempt in (text, text.lstrip().splitlines()[0]):
        try:
            json.loads(attempt)
        except json.JSONDecodeError:
            continue
        return True
    return False


def _classify(path: Path, raw: bytes) -> str | None:
    """`json` for a file the guard reads, `other` for a listed suffix, None for the rest.

    Read: a JSON name, compression magic, or content that opens as JSON, whatever the name says.
    A log that opens with `[timestamp]` and does not parse stays `other`.
    """
    if _stem(path).endswith(JSON_SUFFIXES) or raw.startswith(tuple(MAGIC)) or _json_looking(raw):
        return "json"
    return "other" if _suffix(path) in OTHER_SUFFIXES else None


class Analysis(NamedTuple):
    read: bool
    verdict: w.Verdict


@functools.cache
def _analyse(path: Path, base: Path) -> Analysis:
    """Everything the guard asks of one file, from one read and one parse of it."""
    raw = path.read_bytes()
    if _classify(path, raw) != "json":
        return Analysis(False, w.Verdict([], False, [], False, [], False))
    rel = path.relative_to(base).as_posix() if path.is_relative_to(base) else path.name
    return Analysis(True, w.examine(rel, raw, _documents(raw)))


class Scan(NamedTuple):
    found: dict[str, list[str]]
    read: list[Path]


def scan(paths: list[Path], base: Path = ROOT) -> Scan:
    """Per JSON file among the paths, why it may not be published, and which files were read."""
    found: dict[str, list[str]] = {}
    read: list[Path] = []
    for path in paths:
        if (analysis := _analyse(path, base)).read:
            read.append(path)
            if analysis.verdict.unfit:
                found[path.name] = analysis.verdict.unfit[:3]
    return Scan(found, read)


def offending(paths: list[Path], base: Path = ROOT) -> dict[str, list[str]]:
    return scan(paths, base).found


def granted_offending(paths: list[Path], base: Path = ROOT) -> dict[str, list[str]]:
    """Per granted-derived file outside the pinned exceptions, what it carries that the grants
    exclude: model text, any digest, a plain hash of text."""
    out: dict[str, list[str]] = {}
    for path in paths:
        verdict = _analyse(path, base).verdict
        if verdict.granted and verdict.findings and not verdict.pinned:
            rel = path.relative_to(base).as_posix() if path.is_relative_to(base) else path.name
            out[rel] = verdict.findings[:3]
    return out


def test_no_tracked_json_carries_withheld_text() -> None:
    assert not (found := offending(_tracked())), (
        f"files carrying model text; run scripts/withhold_text.py: {found}"
    )


def test_no_granted_host_file_carries_text_digests_or_hashes() -> None:
    assert not (found := granted_offending(_tracked())), (
        f"granted-host files outside w.BEFORE_THE_RULE carrying text, digests or hashes: {found}"
    )


def _write(path: Path, document: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(map(json.dumps, document)) if path.suffix == ".jsonl" else json.dumps(document)
    )
    return path


def offending_in(paths: list[Path]) -> dict[str, list[str]]:
    return offending(paths, paths[0].parent)


CHANGE = "I" + "a1" * 20


def _row(org: str, **fields: Any) -> dict[str, Any]:
    return {"id": f"{org}:{CHANGE}:src/a.cpp:1:2", **fields}


def _refused(tmp_path: Path, name: str, document: Any) -> dict[str, list[str]]:
    return granted_offending([_write(tmp_path / name, document)], tmp_path)


def test_the_check_fires_on_a_published_run_shape(tmp_path: Path) -> None:
    """The guard is only as good as its reach: each shape a run is published in trips it."""
    shapes = {
        "rq1-partition-openstack-p1-s1-n1850-rp1.0.json": {
            "results": {"base|a": [{"prediction": "x"}]}
        },
        "prompt-format-probe.json": {"raw": [{"prediction": "x"}]},
        "determinism-sym-0-gh-a-001-rp1.0.json": {
            "passes": {"bf16": {f"openstack:{CHANGE}:f.py:1:2": "x"}}
        },
        "rules-reviews-openstack-p10.json": {
            "files": {"openstack-a": {"map_lists": [], "file": "- a rule"}}
        },
    }
    for name, document in shapes.items():
        assert offending_in([_write(tmp_path / name, document)]), name


def test_a_jsonl_sidecar_with_raw_predictions_is_caught(tmp_path: Path) -> None:
    rows = _write(tmp_path / "retrieval-x.rows.jsonl", [{"id": "a", "prediction": "text"}])
    assert offending_in([rows])


def test_a_compressed_result_is_read_not_skipped(tmp_path: Path) -> None:
    path = tmp_path / "run.json.gz"
    path.write_bytes(gzip.compress(json.dumps({"results": [{"prediction": "t"}]}).encode()))
    assert offending_in([path])


def test_a_file_outside_the_results_directory_is_caught(tmp_path: Path) -> None:
    assert offending_in([_write(tmp_path / "docs" / "notes.json", {"prediction": "t"})])


def test_passes_in_a_file_not_named_for_determinism_is_caught(tmp_path: Path) -> None:
    assert offending_in([_write(tmp_path / "run.json", {"passes": {"bf16": {"a": "t"}}})])


def test_a_distillation_saved_under_another_name_is_caught(tmp_path: Path) -> None:
    distilled = {"kind": "reviews", "map_lists": ["- r"], "rules": ["- r"], "file": "- r"}
    assert offending_in([_write(tmp_path / "notes.json", {"files": {"o": distilled}})])


def test_a_renamed_prediction_field_is_caught(tmp_path: Path) -> None:
    assert offending_in([_write(tmp_path / "run.json", {"rows": [{"predictions": ["t"]}]})])


def test_raw_text_in_a_digest_field_is_caught(tmp_path: Path) -> None:
    path = _write(tmp_path / "run.json", {"rows": [{"prediction_hmac": "a comment"}]})
    assert offending_in([path])
    assert not offending_in([_write(tmp_path / "ok.json", {"prediction_hmac": "a" * 64})])
    assert offending_in([_write(tmp_path / "upper.json", {"prediction_hmac": "A" * 64})])


def test_text_keys_under_a_digest_field_are_caught(tmp_path: Path) -> None:
    digest = "a" * 64
    row = f"openstack:{CHANGE}:f.py:1:2"
    for i, bad in enumerate(
        (
            {"prediction_hmac": {"Done.": digest}},
            {"passes_hmac": {"bf16": {"LGTM": digest}}},
            {"ids": ["i0"], "passes_hmac": {"bf16": {"i9": digest}}},
        )
    ):
        assert offending_in([_write(tmp_path / f"hmac-{i}.json", bad)]), bad
    ok = {"ids": ["i0"], "passes_hmac": {"bf16": {"i0": digest}, "fp32": {row: digest}}}
    assert not offending_in([_write(tmp_path / "ok.json", ok)])


def test_bot_audit_comment_text_is_caught(tmp_path: Path) -> None:
    for i, shape in enumerate(
        (
            [{"text": "typo", "changes": 3}],
            ["typo"],
            [{"comment": "typo", "changes": 3}],
            {"typo": 3},
            [{"nit: typo in the docstring": 3}],
            [{"changes": 3, "text": 4}],
        )
    ):
        audit = {"candidate_unregistered_bots": shape}
        assert offending_in([_write(tmp_path / f"data-audit-{i}.json", audit)]), shape
    for i, ok in enumerate(([], [{"changes": 3}, {"changes": 25}])):
        audit = {"candidate_unregistered_bots": ok}
        assert not offending_in([_write(tmp_path / f"ok-{i}.json", audit)]), ok


def test_rules_read_from_a_written_guide_pass(tmp_path: Path) -> None:
    path = _write(tmp_path / "rules-guide-x.json", {"files": {"o": GUIDE, "file": "a/path.json"}})
    assert not offending_in([path])


def test_the_granted_orgs_are_read_from_the_registry() -> None:
    assert w.granted_orgs() == {"qt", "chromium", "aosp", "fuchsia"}
    assert w.aliases()["android"] == "aosp" and w.aliases()["qtfull"] == "qt"


@pytest.mark.parametrize(
    "rel",
    [
        "datasets/results/rq1-partition-qt-p1.json",
        "datasets/results/RQ1-QT-P1.JSON",
        "datasets/results/rq1-windows-qtfull.json",
        "datasets/results/rq1-windows-qt6.json",
        "datasets/results/qtbase-x.json",
        "datasets/results/android-census.json",
        "datasets/results/host-scoping-chromium/a.jsonl",
        "datasets/results/rq1-x-foreignchromium-rp1.0.json",
        "datasets/results/rq1-x-foreign_aosp.json",
        "datasets/gerrit/fuchsia/manifest.json",
        "datasets/results/aosp-census.json",
    ],
)
def test_a_path_names_a_granted_org_at_a_token_boundary(rel: str) -> None:
    assert w.granted_path(rel)


@pytest.mark.parametrize(
    "rel",
    [
        "datasets/results/rq1-windows.json",
        "datasets/results/rq1-pilot.json",
        "datasets/results/retrieval-foreign-openstack-rp1.0.json",
        "datasets/results/quantum-qtile.json",
        "datasets/results/androids-x.json",
    ],
)
def test_a_path_without_a_granted_token_is_not_granted(rel: str) -> None:
    assert not w.granted_path(rel)


@pytest.mark.parametrize(
    "document",
    [
        {"results": {"base": [_row("qt", prediction_hmac="a" * 64)]}},
        {"results": {f"qt:{CHANGE}:f.py:1:2": 1}},
        {"results": {f"QTFULL:{CHANGE.lower()}:f.py:1:2": 1}},
        {"note": f"yocto {CHANGE} aosp:{CHANGE}:x:1"},
        {"cells": [{"org": "Chromium"}]},
        {"cells": [{"foreign": "qt"}]},
        {"cells": [{"foreign_source": "fuchsia"}]},
        {"cells": [{"orgs": ["openstack", "aosp"]}]},
        {"by_org": {"qt": {"n": 3}}},
        {"corpora": {"base|qt": 1}},
        {"source": "qt:cpp/qtbase"},
        {"url": "https://codereview.qt-project.org/c/x"},
        {"url": "https://chromium-review.googlesource.com/c/x", "n": 1},
        {"git": "https://android.googlesource.com/platform"},
    ],
)
def test_a_document_names_a_granted_org_by_its_content(document: Any) -> None:
    assert w.granted_content(document)


@pytest.mark.parametrize(
    "document",
    [
        {"org": "openstack", "results": [_row("openstack")]},
        {"note": f"openstack:{CHANGE}:x:1 qt: {CHANGE}"},
        {"by_org": {"openstack": {"n": 3}}, "source": "openstack:nova"},
        {"url": "https://review.opendev.org/c/x"},
    ],
)
def test_a_document_of_other_orgs_is_not_granted(document: Any) -> None:
    assert not w.granted_content(document)


def test_a_granted_run_is_refused_whatever_its_name(tmp_path: Path) -> None:
    digested = {"results": {"base": [_row("qt", prediction_hmac="a" * 64)]}}
    for name in ("rq1-pilot.json", "QT-run.JSON", "retrieval-foreign-openstack-rp1.0.json"):
        document = digested | ({"foreign": "qt"} if "foreign" in name else {})
        assert _refused(tmp_path, name, document), name
    assert _refused(tmp_path, "host-scoping-chromium/a.jsonl", [{"rules_hmac": ["a" * 64]}])
    assert _refused(tmp_path, "run-foreignchromium.json", {"withheld_text": {"texts": 1}})
    assert _refused(tmp_path, "projects-yocto_meta-boot2qt.json", {"rows": [_row("qt", x_hmac="")]})


def test_a_granted_file_is_refused_for_text_hashes_and_digests_alike(tmp_path: Path) -> None:
    for i, bad in enumerate(
        (
            {"rows": [_row("aosp", prediction="a comment")]},
            {"rows": [_row("aosp", predictions=["a comment"])]},
            {"rules_files": {"base:qt-a": "a" * 64}, "org": "qt"},
            {"base_marks": {"i": "a" * 64}, "org": "qt"},
            {"rows": [_row("fuchsia", fingerprint="a" * 64)]},
            {"rows": [_row("fuchsia", passes_hmac={"bf16": {"i": "a" * 64}})]},
        )
    ):
        assert _refused(tmp_path, f"pool-order-{i}.json", bad), bad


def test_a_granted_file_of_counts_passes(tmp_path: Path) -> None:
    counts = {
        "org": "fuchsia",
        "prediction": [0.0, 0.2],
        "other": {"prediction": None},
        "rows": [_row("fuchsia", exact_match=1.0, tokens=12)],
        "git": {"commit": "a" * 40},
        "raw_digest": "a" * 64,
    }
    assert not _refused(tmp_path, "pool-order-fuchsia.json", counts)
    assert not _refused(tmp_path, "coverage-qt.json", {"k": 3, "intervals": [[0.1, 0.2]]})
    assert not _refused(
        tmp_path, "rq1-windows.json", {"rows": [_row("openstack", p_hmac="a" * 64)]}
    )


def test_the_exceptions_are_pinned_by_the_blob_of_each_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = "datasets/results/rq1-placebo-qt.json"
    original = json.dumps({"rows": [_row("qt", prediction_hmac="a" * 64)]})
    monkeypatch.setattr(w, "BEFORE_THE_RULE", {rel: w.blob(original.encode())})
    same = tmp_path / "same" / rel
    changed = tmp_path / "changed" / rel
    elsewhere = tmp_path / "elsewhere" / "docs" / "rq1-placebo-qt.json"
    for path, text in (
        (same, original),
        (changed, original.replace("a" * 64, "b" * 64)),
        (elsewhere, original),
    ):
        path.parent.mkdir(parents=True)
        path.write_text(text)
    assert not granted_offending([same], tmp_path / "same")
    assert granted_offending([changed], tmp_path / "changed")
    assert granted_offending([elsewhere], tmp_path / "elsewhere")


def test_the_listed_exceptions_still_exist_and_still_carry_predictions() -> None:
    """A file stripped, removed or changed leaves the list, so the list never grows stale."""
    tracked = {p.relative_to(ROOT).as_posix(): p for p in _tracked()}
    assert len(w.BEFORE_THE_RULE) == 20
    for rel, pinned in w.BEFORE_THE_RULE.items():
        assert rel in tracked, f"{rel} is no longer tracked; drop it from BEFORE_THE_RULE"
        raw = tracked[rel].read_bytes()
        assert w.blob(raw) == pinned, f"{rel} changed; re-pin it or drop it"
        assert re.search(rb'"prediction(?:_hmac)?"', raw), f"{rel} carries none; drop it"
        assert _analyse(tracked[rel], ROOT).verdict.granted, rel


def plain_offending(paths: list[Path], base: Path = ROOT) -> dict[str, list[str]]:
    """Per file outside the pinned set, where it carries a plain hash of generated text."""
    out: dict[str, list[str]] = {}
    for path in paths:
        verdict = _analyse(path, base).verdict
        if verdict.plain and not verdict.plain_pinned:
            rel = path.relative_to(base).as_posix() if path.is_relative_to(base) else path.name
            out[rel] = verdict.plain[:3]
    return out


def test_no_tracked_file_outside_the_pinned_set_carries_a_plain_hash() -> None:
    assert not (found := plain_offending(_tracked())), (
        f"files carrying a plain hash of generated text outside "
        f"w.PLAIN_HASHES_BEFORE_THE_RULE; run scripts/withhold_text.py: {found}"
    )


@pytest.mark.parametrize("field", sorted(w.PLAIN_HASHES))
def test_a_new_file_carrying_a_plain_hash_field_is_refused(tmp_path: Path, field: str) -> None:
    value = {"i": "a" * 64} if field != "fingerprint" else "a" * 64
    for n, document in enumerate(
        (
            {field: value},
            {"results": {"b": [{"id": "x", field: value}]}},
            {"org": "openstack", field: value},
        )
    ):
        source = _write(tmp_path / f"retrieval-new-{n}.json", document)
        assert plain_offending([source], tmp_path), document
        assert w.refusals(source.name, source.read_bytes(), [document]), document
    keyed = _write(tmp_path / "keyed.json", {f"{field}_hmac": value})
    assert not plain_offending([keyed], tmp_path)


def test_a_pinned_plain_file_whose_blob_changed_or_moved_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = "datasets/results/retrieval-halves-openstack-p2.json"
    original = json.dumps({"rules_files": {"rules-distilled:openstack-a": "a" * 64}})
    monkeypatch.setattr(w, "PLAIN_HASHES_BEFORE_THE_RULE", {rel: w.blob(original.encode())})
    same = tmp_path / "same" / rel
    changed = tmp_path / "changed" / rel
    elsewhere = tmp_path / "elsewhere" / "docs" / "retrieval-halves-openstack-p2.json"
    for path, text in (
        (same, original),
        (changed, original.replace("a" * 64, "b" * 64)),
        (elsewhere, original),
    ):
        path.parent.mkdir(parents=True)
        path.write_text(text)
    assert not plain_offending([same], tmp_path / "same")
    assert plain_offending([changed], tmp_path / "changed")
    assert plain_offending([elsewhere], tmp_path / "elsewhere")


def test_the_plain_pins_still_exist_and_still_carry_plain_hashes() -> None:
    """A file rekeyed, removed or changed leaves the list, so the list never grows stale."""
    tracked = {p.relative_to(ROOT).as_posix(): p for p in _tracked()}
    assert len(w.PLAIN_HASHES_BEFORE_THE_RULE) == 120
    for rel, pinned in w.PLAIN_HASHES_BEFORE_THE_RULE.items():
        assert rel in tracked, f"{rel} is no longer tracked; drop it from the plain pins"
        raw = tracked[rel].read_bytes()
        assert w.blob(raw) == pinned, f"{rel} changed; re-pin it or drop it"
        assert _analyse(tracked[rel], ROOT).verdict.plain, f"{rel} carries none; drop it"


def test_the_plain_hash_files_on_the_tree_are_the_plain_pins() -> None:
    carrying = {
        p.relative_to(ROOT).as_posix() for p in _tracked() if _analyse(p, ROOT).verdict.plain
    }
    assert carrying == set(w.PLAIN_HASHES_BEFORE_THE_RULE)


def test_the_flagged_set_on_the_tree_is_the_exceptions() -> None:
    """Before the exceptions: every granted-derived file carrying text, digests or hashes."""
    flagged = set()
    for p in _tracked():
        verdict = _analyse(p, ROOT).verdict
        if verdict.granted and verdict.findings:
            flagged.add(p.relative_to(ROOT).as_posix())
    assert flagged == set(w.BEFORE_THE_RULE)


def test_the_pinned_files_are_not_converted_by_line_endings() -> None:
    """A blob pin holds under `core.autocrlf` only where git leaves the file's bytes alone."""
    pins = [*w.BEFORE_THE_RULE, *w.PLAIN_HASHES_BEFORE_THE_RULE]
    out = subprocess.run(
        ["git", "check-attr", "text", "--", *pins],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert len(lines := out.splitlines()) == len(pins)
    assert all(line.endswith("text: unset") for line in lines), lines


def test_every_tracked_file_is_classified_and_every_json_one_is_read() -> None:
    tracked = _tracked()
    unclassified = [
        p.relative_to(ROOT).as_posix() for p in tracked if _classify(p, p.read_bytes()) is None
    ]
    assert not unclassified, f"list the suffixes in OTHER_SUFFIXES or fix the names: {unclassified}"
    by_name = re.compile(r"\.(?:json|jsonl|ndjson)(?:\.(?:gz|bz2|xz|zst))?$", re.IGNORECASE)
    named = {p for p in tracked if by_name.search(p.name)}
    read = set(scan(tracked).read)
    assert named <= read and {p.suffix for p in named} >= {".json", ".jsonl"}


def test_a_file_the_guard_cannot_classify_fails(tmp_path: Path) -> None:
    odd = tmp_path / "notes.weird"
    odd.write_text("plain words")
    assert _classify(odd, odd.read_bytes()) is None
    assert _classify(tmp_path / "a.json", b"{}") == "json"


def test_json_content_under_an_odd_name_is_read(tmp_path: Path) -> None:
    odd = tmp_path / "notes.weird"
    odd.write_text(json.dumps({"rows": [{"prediction": "t"}]}))
    assert _classify(odd, odd.read_bytes()) == "json" and offending_in([odd])
    packed = tmp_path / "rows.dat"
    packed.write_bytes(bz2.compress(b'{"prediction": "t"}'))
    assert offending_in([packed])
    xz = tmp_path / "rows.bin"
    xz.write_bytes(lzma.compress(b'{"prediction": "t"}'))
    assert offending_in([xz])


def test_a_text_file_of_json_lines_is_read_whole_or_it_fails(tmp_path: Path) -> None:
    lines = tmp_path / "run.txt"
    lines.write_text('{"id": "a", "prediction": "t"}\n{"id": "b", "prediction": "u"}\n')
    assert offending_in([lines])
    broken = tmp_path / "run.log"
    broken.write_text('{"id": "a"}\nnot json\n')
    with pytest.raises(ValueError, match="Expecting"):
        offending_in([broken])
    log = tmp_path / "job.log"
    log.write_text("[2026-10-19] started\n[2026-10-19] done\n")
    assert _classify(log, log.read_bytes()) == "other" and scan([log], tmp_path).read == []


def test_a_zstd_file_fails_where_it_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(__import__("sys").modules, "compression", None)
    with pytest.raises(AssertionError, match="zstd"):
        _decoded(b"\x28\xb5\x2f\xfd" + b"\0" * 8)


def test_a_partial_sidecar_holding_predictions_is_refused(tmp_path: Path) -> None:
    rows = "".join(json.dumps({"arm": "a", "id": "i", "prediction": "t"}) + "\n" for _ in range(2))
    for name in ("retrieval-x.rows.partial", "rules-reviews-x.qt-a.part"):
        sidecar = tmp_path / name
        sidecar.write_text(rows)
        assert offending_in([sidecar]), name


def test_a_json_named_file_that_does_not_parse_fails(tmp_path: Path) -> None:
    broken = tmp_path / "a.json"
    broken.write_text("not json")
    with pytest.raises(ValueError, match="Expecting"):
        offending_in([broken])


def test_each_document_is_parsed_once_whatever_the_asks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path / "a.json", {"rows": [_row("qt", prediction_hmac="a" * 64)]})
    calls = []
    real = _documents
    monkeypatch.setitem(globals(), "_documents", lambda raw: calls.append(1) or real(raw))
    offending([path], tmp_path)
    granted_offending([path], tmp_path)
    scan([path], tmp_path)
    assert len(calls) == 1


def test_text_held_as_keys_is_caught_under_a_withheld_field_and_elsewhere(tmp_path: Path) -> None:
    comment = "nit: typo in the docstring"
    for i, document in enumerate(
        (
            {"prediction": {comment: 0}},
            {"rows": [{"predictions": {comment: ["x"]}}]},
            {"passes": {"bf16": {comment: "x"}}},
            {"prediction": {"LGTM": 0}},
        )
    ):
        assert offending_in([_write(tmp_path / f"keys-{i}.json", document)]), document
    labels = {"passes": {"fp32": {f"qt:{CHANGE}:f.py:1:2": {}}}}
    assert not offending_in([_write(tmp_path / "labels.json", labels)])


def test_a_key_holding_whitespace_must_be_one_the_survey_found(tmp_path: Path) -> None:
    for i, document in enumerate(
        ({"fix typo": 1}, {"relations": {"same project today": 1}}, {"other": {"same project": 1}})
    ):
        assert offending_in([_write(tmp_path / f"ws-{i}.json", document)]), document
    for i, ok in enumerate(
        (
            {"relations": {"same project": 1}},
            {"separation": {"bag_of_words - latest_year": 0.5}},
            {"ai_trailer_values": {"Generated-By: claude": 2}},
            {"pairs": {"human~A first half valid_vs_rest without slips": 1.0}},
            {"cuts": {"pfadapter kept local (v,o)": 1, "q_proj alone": 2}},
            {"within:qt:qt/qtbase vs qt/qtdoc": 1},
            {"x": {f"wikimedia:{CHANGE}:groups/Wikimedia/ Lingua.yaml:2:23": 1}},
        )
    ):
        assert not offending_in([_write(tmp_path / f"wsok-{i}.json", ok)]), ok


def test_every_whitespace_key_is_listed_with_a_producer_that_writes_its_field() -> None:
    for field, _, producer in w.WHITESPACE_KEYS:
        source = (ROOT / "scripts" / f"{producer}.py").read_text()
        needle = field or "within:"
        assert needle in source, f"{producer}.py never writes {needle!r}"


def test_the_pass_labels_are_one_constant_the_determinism_check_shares() -> None:
    spec = importlib.util.spec_from_file_location(
        "determinism_check", ROOT / "scripts" / "determinism_check.py"
    )
    assert spec is not None and spec.loader is not None
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    assert check.PASS_DTYPES is w.PASS_DTYPES
    written = set(
        re.findall(r'passes\["(\w+)"\]', (ROOT / "scripts" / "determinism_check.py").read_text())
    )
    assert written == w.PASS_LABELS


@pytest.mark.parametrize("field", ["completion", "response", "answer", "generated", "model_output"])
def test_a_renamed_model_text_field_in_an_example_row_is_caught(tmp_path: Path, field: str) -> None:
    sentence = "Please rename this variable to something clearer"
    document = {"rows": [_row("openstack", **{field: sentence})]}
    assert offending_in([_write(tmp_path / f"{field}.json", document)]), field


def test_prose_under_an_example_is_caught_however_deep(tmp_path: Path) -> None:
    sentence = "Please rename this variable to something clearer"
    row = f"openstack:{CHANGE}:f.py:1:2"
    for i, document in enumerate(
        (
            {"id": row, "outputs": [sentence]},
            {"id": row, "gen": {"text": sentence}},
            {row: {"completion": sentence}},
            {row: [sentence]},
            {"id": row, "output": sentence},
        )
    ):
        assert offending_in([_write(tmp_path / f"deep-{i}.json", document)]), document


def test_prose_is_not_flagged_where_the_tree_keeps_it(tmp_path: Path) -> None:
    row = f"qt:{CHANGE}:best practices/a b.md:1:2"
    for i, ok in enumerate(
        (
            {"output": "datasets/results/x.json"},
            {"output": "three whole words", "rows": [{"n": 1}]},
            {"id": row, "path": "best practices/a b.md", "lexer": "Debian Control file"},
        )
    ):
        assert not offending_in([_write(tmp_path / f"fine-{i}.json", ok)]), ok
    battery = {"rows": [_row("openstack", predicted="set to false in the file", reference="x y z")]}
    assert not offending_in([_write(tmp_path / "battery.json", battery)])
    granted = {"rows": [_row("qt", predicted="set to false in the file")]}
    assert offending_in([_write(tmp_path / "rq1-windows-qt.json", granted)])
    other_path = {"id": row, "path": "some other file with spaces.md"}
    assert offending_in([_write(tmp_path / "otherpath.json", other_path)])
    sentence = {"id": row, "lexer": "Please rename this variable"}
    assert offending_in([_write(tmp_path / "lexersentence.json", sentence)])


def _lexers() -> list[str]:
    from sphragis.experiment.tokens import LEXED, UNTILED

    return [*sorted(LEXED), "Text only", UNTILED.format("Pygments' Foo")]


@pytest.mark.parametrize("lexer", _lexers())
def test_a_lexer_the_tokenizer_writes_is_not_prose(tmp_path: Path, lexer: str) -> None:
    document = {"rows": [_row("openstack", lexer=lexer)]}
    assert not offending_in([_write(tmp_path / "lexer.json", document)]), lexer


#: Where the script that digests a result lives, loaded as a module.
def _script() -> Any:
    spec = importlib.util.spec_from_file_location(
        "withhold_text", ROOT / "scripts" / "withhold_text.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


KEY = bytes(range(32))


def test_the_script_refuses_to_digest_a_granted_document(tmp_path: Path) -> None:
    script = _script()
    document = {"results": {"b": [_row("qt", prediction="a comment")]}}
    for name in ("rq1-pilot.json", "rq1-partition-qt-p1.json"):
        source = _write(tmp_path / name, document)
        with pytest.raises(SystemExit, match="may not be published"):
            script.convert(source, tmp_path / "out" / name, KEY)
    other = _write(tmp_path / "o.json", {"results": {"b": [_row("openstack", prediction="a b")]}})
    script.convert(other, tmp_path / "out" / "o.json", KEY)
    pinned = ROOT / RESULTS / "rq1-placebo-qt.json"
    script.convert(pinned, pinned, KEY)
    copy = tmp_path / "rq1-placebo-qt.json"
    copy.write_bytes(pinned.read_bytes())
    with pytest.raises(SystemExit, match="may not be published"):
        script.convert(copy, copy, KEY)


def test_the_script_rekeys_plain_hashes_but_leaves_a_pinned_file_as_it_is(tmp_path: Path) -> None:
    script = _script()
    document = {"rules_files": {"rules-distilled:openstack-a": "a" * 64}, "org": "openstack"}
    source = _write(tmp_path / "retrieval-new.json", document)
    out, count, published = script.convert(source, tmp_path / "out" / source.name, KEY)
    assert count == 1 and not w.plain_left(published)
    assert json.loads(out)["rules_files_hmac"] == {
        "rules-distilled:openstack-a": w.digest("a" * 64, KEY)
    }
    assert script.convert(source, source, KEY)[2] == published, "re-running changes nothing"
    again, recount, _ = script.convert(
        _write(tmp_path / "again.json", published), tmp_path / "again.json", KEY
    )
    assert recount == 0 and json.loads(again) == published
    tracked = ROOT / RESULTS / "retrieval-halves-openstack-p2.json"
    out, count, _ = script.convert(tracked, tracked, KEY)
    assert out == tracked.read_bytes() and count == 0
    copy = tmp_path / tracked.name
    copy.write_bytes(tracked.read_bytes())
    with pytest.raises(SystemExit, match="already a withheld copy"):
        script.convert(copy, copy, KEY)


def test_the_script_judges_a_path_outside_the_checkout_by_every_component(tmp_path: Path) -> None:
    script = _script()
    source = _write(
        tmp_path / "host-scoping-chromium" / "run.json", {"rows": [{"prediction": "t"}]}
    )
    with pytest.raises(SystemExit, match="may not be published"):
        script.convert(source, tmp_path / "out" / "run.json", KEY)


def test_the_script_refuses_prose_under_a_renamed_field(tmp_path: Path) -> None:
    script = _script()
    sentence = "Please rename this variable to something clearer"
    source = _write(tmp_path / "run.json", {"rows": [_row("openstack", completion=sentence)]})
    with pytest.raises(SystemExit, match="may not be published"):
        script.convert(source, tmp_path / "out" / "run.json", KEY)


def test_the_script_refuses_text_it_cannot_digest_in_place(tmp_path: Path) -> None:
    script = _script()
    source = _write(tmp_path / "run.json", {"prediction": {"nit: typo": 0}})
    with pytest.raises(SystemExit, match="text as keys"):
        script.convert(source, tmp_path / "out" / "run.json", KEY)


def test_a_refusal_partway_through_leaves_every_output_and_the_summary_unwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    good = _write(tmp_path / "a.json", {"rows": [{"id": "i", "prediction": "t"}]})
    bad = _write(tmp_path / "host-scoping-chromium" / "b.json", {"rows": [{"prediction": "t"}]})
    before = good.read_bytes()
    summary = tmp_path / "summary.json"
    monkeypatch.setenv(w.KEY_ENV, KEY.hex())
    monkeypatch.setattr(
        "sys.argv", ["x", "--in-place", "--summary", str(summary), str(good), str(bad)]
    )
    with pytest.raises(SystemExit, match="may not be published"):
        script.main()
    assert good.read_bytes() == before and not summary.exists()
    monkeypatch.setattr("sys.argv", ["x", "--in-place", "--summary", str(summary), str(good)])
    script.main()
    assert w.text_left(json.loads(good.read_text())) == [] and summary.exists()


#: Jobs whose model run is a rescoring of stored rows (ids and scores, committed), and results
#: that are written guides (public text): neither is a generation of review text.
NOT_GENERATIONS = ("likelihood_score.sbatch", "rules-guide-")
#: Where an organization sits in a result's name: the template's own slots.
SLOTS = ("ORG", "FOREIGN_SOURCE")
#: The ways a name is saved besides its result: rows beside it, a job's temporary file, a part.
SIDECARS = (".json", ".rows.jsonl", ".partial", ".qt-a.part.json")


def _alternatives(var: str, job: str) -> list[str] | None:
    """The values a job's variable takes: listed in a comment, the labels of a `case`, or the
    strings it is assigned."""
    if listed := re.search(rf"^#\s*{var}=([\w.\-]+(?:\|[\w.\-]+)+)", job, re.MULTILINE):
        return listed[1].split("|")
    if block := re.search(rf'case "\${var}" in\n(.*?)esac', job, re.DOTALL):
        return re.findall(r"^\s*([\w.\-]+)\)", block[1], re.MULTILINE) or None
    return re.findall(rf'^\s*{var}="([^"$]*(?:\$[A-Z_]+[^"$]*)*)"', job, re.MULTILINE) or None


def _expand(template: str, job: str, depth: int = 0) -> list[str]:
    for var in re.findall(r"\$\{?(\w+)", template):
        if var not in SLOTS and (alternatives := _alternatives(var, job)) and depth < 4:
            filled = [
                re.sub(rf"\$\{{{var}\}}|\${var}\b", lambda _, a=a: a, template)
                for a in alternatives
            ]
            return [name for t in filled for name in _expand(t, job, depth + 1)]
    return [template]


def _fill(template: str, **slots: str) -> str:
    for var, value in slots.items():
        template = re.sub(rf"\$\{{{var}\}}|\${var}\b", lambda _, v=value: v, template)
    template = re.sub(r"\$\{RESULT_SUFFIX\}|\$RESULT_SUFFIX\b", "-s2", template)
    return re.sub(r"\$\{\w+\}|\$\w+", "x", template)


def generation_names() -> dict[str, list[str]]:
    """Concrete result names per kind of family, read from the `claim_result` templates of the jobs
    that run the model (a script that builds an `HFGenerator`) outside `NOT_GENERATIONS`:
    `org` the families whose name holds the organization, `always` the ones every run of which
    reads a granted org (the job's code names one, and every variable of the template is
    listed or has a default). Families whose name tells neither are left to the content check."""
    found: dict[str, list[str]] = {"org": [], "always": []}
    orgs = sorted(w.granted_orgs())
    for job in sorted((ROOT / "scripts").glob("*.sbatch")):
        text = job.read_text()
        if job.name in NOT_GENERATIONS:
            continue
        scripts = re.findall(r"python (scripts/\w+\.py)", text)
        if not any("HFGenerator" in (ROOT / s).read_text() for s in scripts):
            continue
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        named = re.search(rf"\b(?:{'|'.join(orgs)})\b", code) is not None
        for raw in re.findall(r'^\s*claim_result \w+ "\$SPHRAGIS_RESULTS/([^"]+)"', text, re.M):
            for template in _expand(raw, text):
                if template.startswith(NOT_GENERATIONS):
                    continue
                slots = [s for s in SLOTS if f"${s}" in template or f"${{{s}}}" in template]
                for slot in slots:
                    for org in orgs:
                        others = {s: "x" for s in SLOTS if s != slot}
                        found["org"].append(_fill(template, **{slot: org}, **others))
                if not slots and named:
                    unknown = [
                        v
                        for v in re.findall(r"\$\{?(\w+)", template)
                        if v != "RESULT_SUFFIX" and not _alternatives(v, text)
                    ]
                    if not unknown:
                        found["always"].append(_fill(template))
    return found


def _ignored(names: list[str]) -> set[str]:
    out = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=ROOT,
        input="\n".join(names),
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return set(out.split())


def _granted_block() -> list[str]:
    lines = (ROOT / ".gitignore").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("# Generation runs"))
    block = itertools.takewhile(bool, lines[start:])
    return [line for line in block if not line.startswith("#")]


def _matches(pattern: str, name: str) -> bool:
    """A gitignore pattern against a path: one with a slash is anchored, one without is a name."""
    return fnmatch.fnmatchcase(name if "/" in pattern else name.rsplit("/", 1)[-1], pattern)


def _ignorable() -> list[str]:
    """Every name and sidecar a generation job can write for a granted org."""
    stems = [n.removesuffix(".json") for names in generation_names().values() for n in names]
    return [f"{RESULTS}/{stem}{sidecar}" for stem in stems for sidecar in SIDECARS]


def test_the_families_come_from_the_jobs_that_run_the_model() -> None:
    found = generation_names()
    for start in ("rq1-partition-qt-", "rq1-placebo-aosp", "retrieval-halves-chromium-"):
        assert any(n.startswith(start) for n in found["org"]), start
    assert any(n.startswith("rules-foreign-fuchsia") for n in found["org"])
    assert any("-foreignqt" in n for n in found["org"])
    assert {n.removesuffix("-s2.json") for n in found["always"]} == {"rq1-pilot", "rq1-windows"}
    every = [*found["org"], *found["always"]]
    assert not any(n.startswith(("likelihood", "projects-", "rules-guide")) for n in every)


def test_every_name_a_generation_job_writes_for_a_granted_org_is_ignored() -> None:
    names = _ignorable()
    assert len(names) > 100
    assert not (missed := set(names) - _ignored(names)), f"not ignored: {sorted(missed)[:5]}"
    qtfull = [
        f"{RESULTS}/{f}-qtfull{s}" for f in ("rules-reviews", "rq1-windows") for s in SIDECARS
    ]
    assert not (set(qtfull) - _ignored(qtfull))


def test_every_pattern_of_the_granted_block_is_exercised_by_a_generated_name() -> None:
    names = _ignorable()
    for pattern in _granted_block():
        assert any(_matches(pattern, n) for n in names), f"no generated name exercises {pattern}"


def test_a_name_without_a_granted_org_is_not_ignored() -> None:
    names = [
        f"{RESULTS}/{f}-openstack{s}"
        for f in ("rq1-partition", "rules-reviews")
        for s in (".json",)
    ]
    assert _ignored(names) == set()
