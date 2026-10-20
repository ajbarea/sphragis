"""No tracked result carries model text that could repeat a review comment.

A generation conditioned on review comments, or a summary distilled from them, can repeat a
comment word for word, and no review text is published (`docs/protocol.md`). Published results
carry a keyed digest of each such text instead (`sphragis.measure.withhold`,
`scripts/withhold_text.py`); the full texts stay in the cluster's results root. The hosts opened
by written grant stay out of git altogether, as their grants ask (research log, 2026-10-18 and
2026-10-19).
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
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from sphragis.measure import withhold as w

ROOT = Path(__file__).resolve().parents[2]
RESULTS = "datasets/results"
DIGEST = re.compile(r"[0-9a-f]{64}")
#: A bot-audit list of short real review comments, which no published artifact carries.
COMMENT_LISTS = frozenset({"candidate_unregistered_bots"})
#: The only key of a row in that list: `scripts/data_audit.py` writes `{"changes": n}`.
COMMENT_ROW_KEYS = frozenset({"changes"})
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
        ".gitignore", ".gitkeep", "license", "makefile",
    }
)  # fmt: skip

#: Fields whose keys hold whitespace, from a survey of the tracked JSON: the field the key sits
#: under (empty at the top of a file), what it may be, and the script that writes it.
WHITESPACE_KEYS: tuple[tuple[str, str, str], ...] = (
    ("ai_trailer_values", r"(?:Assisted|Co-Authored|Generated)-By: [\w. -]{1,24}", "ai_trailers"),
    (
        "relations",
        "other organization and content|other organization, same content"
        "|same organization and content, other project|same organization, other content"
        "|same project",
        "client_attribution",
    ),
    ("separation", r"\w+ - \w+", "contamination_blind"),
    (
        "automated_comments_by_bot",
        "Qt QUIP-23 security review integration|Qt Sanity Bot|flake8 lint output",
        "data_audit",
    ),
    (
        "pairs",
        r"(?:human|[ABC])~[ABC](?: (?:first|second) half)?"
        r"(?: (?:outside_names|valid_vs_rest|on checked items))?(?: without slips)?",
        "label_audit_agreement",
    ),
    (
        "cuts",
        "attention, all four|mlp, all three|[a-z_]+ alone|pfadapter (?:kept local \\(v,o\\)"
        "|transmitted \\(q,k\\))",
        "module_split",
    ),
    ("", r"within:\w+:[\w/.\-]+ vs [\w/.\-]+", "separability"),
)
_WHITESPACE_KEYS = tuple((field, re.compile(rx)) for field, rx, _ in WHITESPACE_KEYS)

#: A string of this many words under any key of an object carrying an example id is model text
#: under a name the guard does not know; the longest a tracked file holds outside the two
#: battery fields below is two (a `lexer`, a `path`).
PROSE_VALUE_WORDS = 3
#: The contamination battery's completions of code and its targets, from prompts without review
#: text (`scripts/contamination_blind.py`); never allowed in a granted host's file.
BATTERY_FIELDS = frozenset({"predicted", "reference"})


def _tracked() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [ROOT / line for line in out.split("\0") if line]


def _zstd() -> Any:
    try:
        from compression import zstd  # ty: ignore[unresolved-import]
    except ImportError:
        pytest.skip("zstd needs Python 3.14")
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


def _documents(path: Path) -> list[Any]:
    return _parsed(_decoded(path.read_bytes()).decode())


def _classify(path: Path) -> str | None:
    """`json` for a file the guard reads, `other` for a listed suffix, None for the rest.

    Read: a JSON name, compression magic, or a first byte of `{` or `[` where the whole file
    then parses, whatever the name says. A log that opens with `[` stays `other`.
    """
    with path.open("rb") as handle:
        head = handle.read(8)
    if _stem(path).endswith(JSON_SUFFIXES) or head.startswith(tuple(MAGIC)):
        return "json"
    if head.lstrip()[:1] in (b"{", b"["):
        try:
            _documents(path)
        except ValueError:
            pass
        else:
            return "json"
    return "other" if _suffix(path) in OTHER_SUFFIXES else None


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
    """The published shape of a comment list: rows of counts under the one key the audit writes."""
    return isinstance(value, list) and all(
        isinstance(row, Mapping)
        and set(row) <= COMMENT_ROW_KEYS
        and all(isinstance(n, int) and not isinstance(n, bool) for n in row.values())
        for row in value
    )


def _listed_key(field: str, key: str) -> bool:
    return any(f == field and rx.fullmatch(key) for f, rx in _WHITESPACE_KEYS)


def _unfit(node: Any, path: str = "", field: str = "") -> Iterator[str]:
    """Digest fields not holding digests, comment lists holding comments, keys of prose."""
    if isinstance(node, Mapping):
        for k, v in node.items():
            if re.search(r"\s", k) and not w.EXAMPLE_ID.match(k) and not _listed_key(field, k):
                yield f"{path}/{k[:40]} is a key of prose"
            if k.endswith(w.SUFFIX) and not all(DIGEST.fullmatch(t) for t in _strings(v)):
                yield f"{path}/{k} is not a digest"
            elif k in COMMENT_LISTS and not _counts_only(v):
                yield f"{path}/{k} carries comment text"
            else:
                yield from _unfit(v, f"{path}/{k}", k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _unfit(v, f"{path}/{i}", field)


def _prose(node: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """(key, where) for a string of several words in an object that carries an example id."""
    if isinstance(node, Mapping):
        carries = any(w.EXAMPLE_ID.match(k) for k in node) or any(
            isinstance(v, str) and w.EXAMPLE_ID.match(v) for v in node.values()
        )
        for k, v in node.items():
            if carries and isinstance(v, str) and len(v.split()) >= PROSE_VALUE_WORDS:
                if not w.EXAMPLE_ID.match(v):
                    yield k, f"{path}/{k} holds prose"
            else:
                yield from _prose(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _prose(v, f"{path}/{i}")


class Analysis(NamedTuple):
    read: bool
    unfit: list[str]
    refused: list[str]
    granted: bool
    blob: str


@functools.cache
def _analyse(path: Path, base: Path) -> Analysis:
    """Everything the guard asks of one file, from one parse of it."""
    if _classify(path) != "json":
        return Analysis(False, [], [], False, "")
    rel = path.relative_to(base).as_posix() if path.is_relative_to(base) else path.name
    unfit: list[str] = []
    prose: list[tuple[str, str]] = []
    refused: list[str] = []
    granted = w.granted_path(rel)
    for i, document in enumerate(_documents(path)):
        unfit += [f"{i}{p}" for p in w.text_left(document)]
        unfit += [f"{i}{p}" for p in _unfit(document)]
        prose += [(k, f"{i}{p}") for k, p in _prose(document)]
        refused += [f"{i}{p}" for p in w.granted_findings(document)]
        granted = granted or w.granted_content(document)
    unfit += [p for k, p in prose if granted or k not in BATTERY_FIELDS]
    return Analysis(True, unfit, refused, granted, w.blob(path.read_bytes()))


class Scan(NamedTuple):
    found: dict[str, list[str]]
    read: list[Path]


def scan(paths: list[Path], base: Path = ROOT) -> Scan:
    """Per JSON file among the paths, where it carries model text, a bad digest or comment text,
    and which files were read."""
    found: dict[str, list[str]] = {}
    read: list[Path] = []
    for path in paths:
        if (analysis := _analyse(path, base)).read:
            read.append(path)
            if analysis.unfit:
                found[path.name] = analysis.unfit[:3]
    return Scan(found, read)


def offending(paths: list[Path], base: Path = ROOT) -> dict[str, list[str]]:
    return scan(paths, base).found


def granted_offending(paths: list[Path], base: Path = ROOT) -> dict[str, list[str]]:
    """Per granted-derived file outside the pinned exceptions, what it carries that the grants
    exclude: model text, any digest, a plain hash of text."""
    out: dict[str, list[str]] = {}
    for path in paths:
        analysis = _analyse(path, base)
        rel = path.relative_to(base).as_posix() if path.is_relative_to(base) else path.name
        if analysis.granted and analysis.refused and w.BEFORE_THE_RULE.get(rel) != analysis.blob:
            out[rel] = analysis.refused[:3]
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
        assert offending_in([_write(tmp_path / f"data-audit-{i}.json", audit)]), shape
    for i, ok in enumerate(([], [{"changes": 3}, {"changes": 25}])):
        audit = {"candidate_unregistered_bots": ok}
        assert not offending_in([_write(tmp_path / f"ok-{i}.json", audit)]), ok


def test_rules_read_from_a_written_guide_pass(tmp_path: Path) -> None:
    path = _write(tmp_path / "rules-guide-x.json", {"files": {"o": GUIDE, "file": "a/path.json"}})
    assert not offending_in([path])


CHANGE = "I" + "a1" * 20


def _row(org: str, **fields: Any) -> dict[str, Any]:
    return {"id": f"{org}:{CHANGE}:src/a.cpp:1:2", **fields}


def _refused(tmp_path: Path, name: str, document: Any) -> dict[str, list[str]]:
    return granted_offending([_write(tmp_path / name, document)], tmp_path)


def test_the_granted_orgs_are_read_from_the_registry() -> None:
    assert w.granted_orgs() == {"qt", "chromium", "aosp", "fuchsia"}


@pytest.mark.parametrize(
    "rel",
    [
        "datasets/results/rq1-partition-qt-p1.json",
        "datasets/results/RQ1-QT-P1.JSON",
        "datasets/results/rq1-windows-qtfull.json",
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
        "datasets/results/qt5-notes.json",
        "datasets/results/quantum-qtile.json",
    ],
)
def test_a_path_without_a_granted_token_is_not_granted(rel: str) -> None:
    assert not w.granted_path(rel)


def test_a_document_names_a_granted_org_by_its_content() -> None:
    row = _row("qt", prediction_hmac="a" * 64)
    assert w.granted_content({"results": {"base": [row]}})
    assert w.granted_content({"results": {f"qt:{CHANGE}:f.py:1:2": 1}})
    assert w.granted_content({"note": f"yocto {CHANGE} aosp:{CHANGE}:x:1"})
    assert not w.granted_content({"note": f"openstack:{CHANGE}:x:1 qt: {CHANGE}"})
    for field, value in (
        ("org", "Chromium"),
        ("foreign", "qt"),
        ("foreign_source", "fuchsia"),
        ("orgs", ["openstack", "aosp"]),
    ):
        assert w.granted_content({"cells": [{field: value}]}), field
    assert not w.granted_content({"org": "openstack", "results": [_row("openstack")]})


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
    }
    assert not _refused(tmp_path, "pool-order-fuchsia.json", counts)
    assert not _refused(tmp_path, "coverage-qt.json", {"k": 3, "intervals": [[0.1, 0.2]]})
    assert not _refused(
        tmp_path, "rq1-windows.json", {"rows": [_row("openstack", p_hmac="a" * 64)]}
    )


def test_the_script_refuses_to_digest_a_granted_document() -> None:
    spec = importlib.util.spec_from_file_location(
        "withhold_text", ROOT / "scripts" / "withhold_text.py"
    )
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    document = {"results": {"b": [_row("qt", prediction="a comment")]}}
    raw = json.dumps(document).encode()
    for name in ("rq1-pilot.json", "rq1-partition-qt-p1.json"):
        with pytest.raises(SystemExit, match="written grant"):
            script.refuse_granted(ROOT / RESULTS / name, raw, document)
    other = {"results": {"b": [_row("openstack", prediction="a comment")]}}
    script.refuse_granted(ROOT / RESULTS / "rq1-partition-openstack-p1.json", raw, other)
    name = "rq1-placebo-qt.json"
    pinned = ROOT / RESULTS / name
    script.refuse_granted(pinned, pinned.read_bytes(), json.loads(pinned.read_bytes()))
    with pytest.raises(SystemExit, match="written grant"):
        script.refuse_granted(pinned, pinned.read_bytes() + b" ", document)


def test_the_exceptions_are_pinned_by_the_blob_of_each_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = "datasets/results/rq1-placebo-qt.json"
    original = json.dumps({"rows": [_row("qt", prediction_hmac="a" * 64)]})
    pins = {rel: w.blob(original.encode())}
    monkeypatch.setattr(w, "BEFORE_THE_RULE", pins)
    path = tmp_path / rel
    path.parent.mkdir(parents=True)
    path.write_text(original)
    assert not granted_offending([path], tmp_path)
    path.write_text(original.replace("a" * 64, "b" * 64))
    _analyse.cache_clear()
    assert granted_offending([path], tmp_path)
    elsewhere = tmp_path / "docs" / "rq1-placebo-qt.json"
    elsewhere.parent.mkdir()
    elsewhere.write_text(original)
    assert granted_offending([elsewhere], tmp_path)


def test_the_listed_exceptions_still_exist_and_still_carry_predictions() -> None:
    """A file stripped, removed or changed leaves the list, so the list never grows stale."""
    tracked = {p.relative_to(ROOT).as_posix(): p for p in _tracked()}
    assert len(w.BEFORE_THE_RULE) == 20
    for rel, pinned in w.BEFORE_THE_RULE.items():
        assert rel in tracked, f"{rel} is no longer tracked; drop it from BEFORE_THE_RULE"
        raw = tracked[rel].read_bytes()
        assert w.blob(raw) == pinned, f"{rel} changed; re-pin it or drop it"
        assert re.search(rb'"prediction(?:_hmac)?"', raw), f"{rel} carries none; drop it"
        assert _analyse(tracked[rel], ROOT).granted, rel


def test_the_flagged_set_on_the_tree_is_the_exceptions() -> None:
    """Before the exceptions: every granted-derived file carrying text, digests or hashes."""
    flagged = {
        p.relative_to(ROOT).as_posix()
        for p in _tracked()
        if (a := _analyse(p, ROOT)).granted and a.refused
    }
    assert flagged == set(w.BEFORE_THE_RULE)


def test_every_tracked_file_is_classified_and_every_json_one_is_read() -> None:
    tracked = _tracked()
    unclassified = [p.relative_to(ROOT).as_posix() for p in tracked if _classify(p) is None]
    assert not unclassified, f"list the suffixes in OTHER_SUFFIXES or fix the names: {unclassified}"
    by_name = re.compile(r"\.(?:json|jsonl|ndjson)(?:\.(?:gz|bz2|xz|zst))?$", re.IGNORECASE)
    named = {p for p in tracked if by_name.search(p.name)}
    read = set(scan(tracked).read)
    assert named <= read and {p.suffix for p in named} >= {".json", ".jsonl"}
    assert all(_analyse(p, ROOT).read for p in read)


def test_a_file_the_guard_cannot_classify_fails(tmp_path: Path) -> None:
    odd = tmp_path / "notes.weird"
    odd.write_text("plain words")
    assert _classify(odd) is None
    assert _classify(_write(tmp_path / "a.json", {})) == "json"


def test_json_content_under_an_odd_name_is_read(tmp_path: Path) -> None:
    odd = tmp_path / "notes.weird"
    odd.write_text(json.dumps({"rows": [{"prediction": "t"}]}))
    assert _classify(odd) == "json" and offending_in([odd])
    log = tmp_path / "run.log"
    log.write_text("[2026-10-19] started\n")
    assert _classify(log) == "other" and scan([log], tmp_path).read == []
    packed = tmp_path / "rows.dat"
    packed.write_bytes(bz2.compress(b'{"prediction": "t"}'))
    assert _classify(packed) == "json" and offending_in([packed])
    xz = tmp_path / "rows.bin"
    xz.write_bytes(lzma.compress(b'{"prediction": "t"}'))
    assert offending_in([xz])


def test_a_partial_sidecar_holding_predictions_is_refused(tmp_path: Path) -> None:
    rows = "".join(json.dumps({"arm": "a", "id": "i", "prediction": "t"}) + "\n" for _ in range(2))
    for name in ("retrieval-x.rows.partial", "rules-reviews-x.qt-a.part"):
        sidecar = tmp_path / name
        sidecar.write_text(rows)
        assert offending_in([sidecar]), name


def test_a_text_file_is_not_a_guarded_file_but_a_json_named_one_must_parse(tmp_path: Path) -> None:
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
    monkeypatch.setitem(globals(), "_documents", lambda p: calls.append(p) or real(p))
    _analyse.cache_clear()
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
        )
    ):
        assert offending_in([_write(tmp_path / f"keys-{i}.json", document)]), document
    labels = {"passes": {"fp32": {f"qt:{CHANGE}:f.py:1:2": {}}}}
    assert not offending_in([_write(tmp_path / "labels.json", labels)])


def test_bot_audit_row_keys_are_the_ones_the_audit_writes(tmp_path: Path) -> None:
    bad = {"candidate_unregistered_bots": [{"nit: typo in the docstring": 3}]}
    assert offending_in([_write(tmp_path / "data-audit-a.json", bad)])
    bad = {"candidate_unregistered_bots": [{"changes": 3, "text": 4}]}
    assert offending_in([_write(tmp_path / "data-audit-b.json", bad)])


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
            {"x": {"wikimedia:Ib3c:groups/Wikimedia/ Lingua.yaml:2:23": 1}},
        )
    ):
        assert not offending_in([_write(tmp_path / f"wsok-{i}.json", ok)]), ok


def test_every_whitespace_key_in_the_tree_is_listed_with_its_producer() -> None:
    for *_, producer in WHITESPACE_KEYS:
        assert (ROOT / "scripts" / f"{producer}.py").is_file(), producer


@pytest.mark.parametrize(
    "field", ["completion", "response", "answer", "generated", "model_output", "output"]
)
def test_a_renamed_model_text_field_in_an_example_row_is_caught(tmp_path: Path, field: str) -> None:
    sentence = "Please rename this variable to something clearer"
    document = {"rows": [_row("openstack", **{field: sentence})]}
    assert offending_in([_write(tmp_path / f"{field}.json", document)]), field
    keyed = {"rows": {f"openstack:{CHANGE}:f.py:1:2": sentence}}
    assert offending_in([_write(tmp_path / f"{field}-keyed.json", keyed)]), field


def test_prose_is_not_flagged_where_the_tree_keeps_it(tmp_path: Path) -> None:
    for i, ok in enumerate(
        (
            {"output": "datasets/results/x.json"},
            {"rows": [_row("openstack", lexer="Text only", path="groups/W/ L.yaml")]},
            {"output": "three whole words", "rows": [{"n": 1}]},
        )
    ):
        assert not offending_in([_write(tmp_path / f"fine-{i}.json", ok)]), ok
    battery = {"rows": [_row("openstack", predicted="set to false in the file", reference="x y z")]}
    assert not offending_in([_write(tmp_path / "battery.json", battery)])
    granted = {"rows": [_row("qt", predicted="set to false in the file")]}
    assert offending_in([_write(tmp_path / "rq1-windows-qt.json", granted)])


#: The families a granted host's runs are written under (`scripts/*.sbatch`), the shapes a name
#: takes (a result, its rows sidecar, a job's temporary file, a per-file part), and a name no
#: pattern may reach.
FAMILIES = (
    "rq1-partition",
    "rq1-placebo",
    "rq1-windows",
    "retrieval-halves",
    "rules-halves",
    "rules-reviews",
    "retrieval-foreign",
    "rules-foreign",
)
SHAPES = (".json", "-p4-n1850-rp1.0.json", "-p4.rows.jsonl", "-p4.partial", "-p4.qt-a.part.json")


def _granted_block() -> list[str]:
    lines = (ROOT / ".gitignore").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("# Generation runs"))
    block = itertools.takewhile(bool, lines[start:])
    return [line for line in block if not line.startswith("#")]


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


def _matches(pattern: str, name: str) -> bool:
    """A gitignore pattern against a path: one with a slash is anchored, one without is a name."""
    return fnmatch.fnmatchcase(name if "/" in pattern else name.rsplit("/", 1)[-1], pattern)


def _tokens() -> list[str]:
    return sorted(w.granted_orgs() | set(w.ALIASES))


def test_a_granted_host_generation_run_is_ignored() -> None:
    names = [f"{RESULTS}/{f}-{t}{s}" for f in FAMILIES for t in _tokens() for s in SHAPES]
    assert {"qt", "chromium", "aosp", "fuchsia", "qtfull"} <= set(_tokens())
    assert not (missed := set(names) - _ignored(names)), f"not ignored: {sorted(missed)[:5]}"


def test_every_pattern_of_the_granted_block_is_exercised_by_a_test_name() -> None:
    names = [f"{RESULTS}/{f}-{t}{s}" for f in FAMILIES for t in _tokens() for s in SHAPES]
    patterns = _granted_block()
    assert len(patterns) >= len(FAMILIES) * len(w.granted_orgs()) + 1
    for pattern in patterns:
        assert any(_matches(pattern, n) for n in names), f"no test name exercises {pattern}"


def test_a_name_without_a_granted_org_is_not_ignored() -> None:
    names = [f"{RESULTS}/{f}-openstack{s}" for f in FAMILIES for s in SHAPES[:3]]
    assert _ignored(names) == set()
