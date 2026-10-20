"""Withholding model text: keyed digests in place, every other byte of the result kept."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from sphragis.measure import withhold as w

KEY = bytes(range(32))


def test_a_digest_is_keyed_so_a_public_text_cannot_be_looked_up() -> None:
    text = "Please use a context manager here."
    assert w.digest(text, KEY) == hmac.new(KEY, text.encode(), hashlib.sha256).hexdigest()
    assert w.digest(text, KEY) != hashlib.sha256(text.encode()).hexdigest()
    assert w.digest(text, KEY) != w.digest(text, bytes(32))


def test_the_key_id_names_the_key_without_revealing_it() -> None:
    assert w.key_id(KEY) == w.key_id(bytes(range(32)))
    assert w.key_id(KEY) != w.key_id(bytes(32))
    assert KEY.hex()[:16] != w.key_id(KEY)


@pytest.mark.parametrize("value", ["", "zz" * 32, "ab" * 16, "ab" * 33])
def test_a_missing_or_wrong_size_key_is_refused(value: str) -> None:
    with pytest.raises(SystemExit, match=w.KEY_ENV):
        w.load_key({w.KEY_ENV: value})


def test_a_run_keeps_its_ids_and_scores_and_loses_its_text() -> None:
    run = {
        "model_id": "m",
        "results": {"base|a": [{"id": "i", "prediction": "p", "exact_match": 1.0}]},
    }
    published, count = w.withhold(run, "rq1-partition-openstack-p1.json", KEY)
    row = published["results"]["base|a"][0]
    assert count == 1 and list(row) == ["id", "prediction_hmac", "exact_match"]
    assert row["prediction_hmac"] == w.digest("p", KEY)
    assert published["withheld_text"]["fields"] == ["prediction"]
    assert published["withheld_text"]["key_id"] == w.key_id(KEY)
    assert run["results"]["base|a"][0]["prediction"] == "p", "the input is not mutated"
    assert w.text_left(published) == []


def test_a_prediction_interval_is_not_text() -> None:
    across = {"estimate": 0.1, "prediction": [0.0, 0.2], "other": {"prediction": None}}
    assert w.withhold(across, "across-orgs.json", KEY) == (across, 0)


def test_fields_belong_to_their_objects() -> None:
    """`file` is a rule list in a review distillation and a path everywhere else."""
    rules = {
        "files": {
            "openstack-a": {
                "map_lists": ["- m"],
                "file": "- a rule",
                "rules": ["- a rule"],
                "chunks": 2,
            }
        }
    }
    published, count = w.withhold(rules, "any-name.json", KEY)
    assert count == 3 and published["files"]["openstack-a"]["chunks"] == 2
    assert w.withhold({"file": "datasets/results/x.json"}, "k-coverage-qt.json", KEY)[1] == 0
    assert w.text_left(rules) != []


GUIDE = {
    "kind": "guide",
    "chunks": 1,
    "part_pages": [1],
    "map_lists": ["- m"],
    "evidence": [],
    "reduce_answer": None,
    "merge_chunks": None,
    "recurrence": [],
    "rule_pages": [1],
    "rules": ["- r"],
    "file": "- r",
}


def test_a_distillation_of_a_written_guide_is_not_withheld() -> None:
    assert w.withhold({"files": {"o": GUIDE}}, "rules-guide-openstack.json", KEY)[1] == 0


@pytest.mark.parametrize(
    "change",
    [
        {"reduce_answer": "- a rule"},
        {"reduce_answer_hmac": "a" * 64},
        {"evidence": ["a comment"]},
        {"evidence": None},
        {"evidence": 0},
        {"recurrence": [{"rule": "a comment"}]},
        {"recurrence": 0},
        {"recurrence": None},
        {"merge_chunks": [1]},
        {"rules_hmac": ["a" * 64]},
        {"chunks": 2},
        {"chunks": 0, "part_pages": []},
        {"part_pages": None},
        {"rule_pages": [1, 1]},
        {"kind": "Guide"},
    ],
)
def test_a_guide_is_exempt_only_in_the_shape_a_written_guide_has(change: dict) -> None:
    assert w.withhold({"files": {"o": {**GUIDE, **change}}}, "g.json", KEY)[1] > 0, change


@pytest.mark.parametrize("gone", ["evidence", "recurrence", "chunks", "part_pages", "rule_pages"])
def test_a_guide_missing_part_of_its_shape_is_not_exempt(gone: str) -> None:
    bare = {k: v for k, v in GUIDE.items() if k != gone}
    assert w.withhold({"o": bare}, "g.json", KEY)[1] > 0


def test_a_reviews_distillation_is_known_by_its_kind_in_any_case() -> None:
    for kind in ("reviews", "Reviews", "REVIEWS"):
        distilled = {"kind": kind, "rules": ["- r"], "file": "- r"}
        assert w.withhold({"o": distilled}, "any.json", KEY)[1] == 2, kind
    sources = {"kind": "reviews", "sources": ["a review", "another"], "pool_ids": ["i", "j"]}
    published, count = w.withhold({"o": sources}, "any.json", KEY)
    assert count == 2 and published["o"]["pool_ids"] == ["i", "j"]


def test_a_reviews_distillation_keeps_the_counts_it_records() -> None:
    counts = {
        "kind": "reviews",
        "evidence": [{"listed": 15, "kept": 2}],
        "recurrence": [[2, 5], [1]],
        "chunks": 2,
    }
    assert w.withhold({"o": counts}, "any.json", KEY) == ({"o": counts}, 0)


def test_a_field_no_producer_writes_is_not_withheld_by_name() -> None:
    config = {"output": "datasets/results/x.json", "generation": "greedy", "outputs": ["a"]}
    assert w.withhold(config, "r.json", KEY) == (config, 0)


def _id(n: str = "a", path: str = "f.py") -> str:
    return f"openstack:I{n * 40}:{path}:1:2"


def test_a_key_the_document_lists_is_a_label() -> None:
    listed = {"ids": ["i0", "i1"], "passes": {"bf16": {"i0": "x", "i1": "y"}}}
    published, count = w.withhold(listed, "r.json", KEY)
    assert count == 2 and list(published["passes_hmac"]["bf16"]) == ["i0", "i1"]
    row = {"results": [{"id": "i0", "prediction": {"i0": "x"}}]}
    assert w.withhold(row, "r.json", KEY)[1] == 1
    unlisted = {"ids": ["i0"], "passes": {"bf16": {"i9": "x"}}}
    assert w.text_left(unlisted) == ["/passes"]


def test_keys_that_are_text_are_reported_and_refused_never_digested() -> None:
    for doc in (
        {"prediction": {"nit: typo": 0}},
        {"prediction": {"lower": 0.1, "upper": 0.2}},
        {"rows": [{"id": "i0", "predictions": {"LGTM": ["x"]}}]},
    ):
        assert w.text_left(doc), doc
        with pytest.raises(ValueError, match="text as keys"):
            w.withhold(doc, "r.json", KEY)


def test_ids_and_dtype_labels_keep_their_place_as_keys() -> None:
    doc = {"prediction": {_id(): "x"}, "passes": {"bf16_fresh": {_id("b"): "y"}, "fp32": {}}}
    published, count = w.withhold(doc, "r.json", KEY)
    assert count == 2 and list(published["prediction_hmac"]) == [_id()]
    assert w.text_left(published) == []


def test_keys_under_a_digest_field_are_labels_or_digests() -> None:
    ids = frozenset({"i0"})
    assert not w.unlabelled_keys({"bf16": {"i0": "d", _id(): "d", "fp32": {}}}, ids)
    assert w.unlabelled_keys({"bf16": {"LGTM": "d"}}, ids) == ["LGTM"]
    assert not w.unlabelled_keys({"a" * 64: "d"}, ids)


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        (f"qt:I{'a' * 40}:src/a.cpp:1:2", True),
        (f"qt:I{'A' * 40}:src/a b/c.cpp:1", True),
        (f"wikimedia:I{'a' * 40}:doc/adr/0008. Filtering data with Scribunto.md:3", True),
        ("x:o/r#12:src/a.py:3:4", True),
        (f"qt:I{'a' * 40}:Please rename this variable to something clearer:1", False),
        (f"qt:I{'a' * 40}:Please rename this variable now. Thanks:1", False),
        (f"qt:I{'a' * 39}:f.py:1", False),
        (f"qt:Ig{'a' * 39}:f.py:1", False),
        (f"qt:I{'a' * 40}:f.py:x", False),
    ],
)
def test_one_example_id_pattern(value: str, ok: bool) -> None:
    assert w.is_example_id(value) is ok, value


def test_the_pass_labels_are_the_dtype_runs_the_determinism_check_writes() -> None:
    assert {"bf16", "bf16_fresh", "bf16_repeat", "fp32"} == w.PASS_LABELS


def test_determinism_passes_keep_their_ids() -> None:
    a, b = (f"openstack:I{n * 40}:f.py:1:2" for n in "ab")
    doc = {"passes": {"bf16": {a: "x", b: "y"}}}
    published, count = w.withhold(doc, "determinism-sym-0-gh-a-001-rp1.0.json", KEY)
    assert count == 2 and published["passes_hmac"]["bf16"] == {
        a: w.digest("x", KEY),
        b: w.digest("y", KEY),
    }


def test_withholding_twice_changes_nothing() -> None:
    once, _ = w.withhold({"results": {"a": [{"prediction": "p"}]}}, "r.json", KEY)
    assert w.withhold(once, "r.json", KEY) == (once, 0)


def test_a_reader_that_needs_text_refuses_a_published_copy() -> None:
    published, _ = w.withhold({"results": {"a": [{"prediction": "p"}]}}, "r.json", KEY)
    with pytest.raises(SystemExit, match="full-text copy"):
        w.require_text(published, "r.json")
    w.require_text({"results": {}}, "r.json")


def test_a_burned_key_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    environ = {w.KEY_ENV: KEY.hex()}
    assert w.load_key(environ) == KEY
    monkeypatch.setitem(w.BURNED_KEY_IDS, w.key_id(KEY), "its texts are in history")
    with pytest.raises(SystemExit, match="burned key.*Generate a new one"):
        w.load_key(environ)


def test_the_key_whose_texts_are_in_history_is_burned() -> None:
    assert "659eedab9a778439" in w.BURNED_KEY_IDS


def test_documents_compare_only_on_one_basis() -> None:
    plain = {"results": {}}
    one, _ = w.withhold({"prediction": "p"}, "r.json", KEY)
    two, _ = w.withhold({"prediction": "p"}, "r.json", bytes(32))
    assert w.basis({"a": plain, "b": plain}) == ""
    assert w.basis({"a": one, "b": one}) == w.SUFFIX
    for mixed, match in (
        ({"a": plain, "b": one}, "published and full-text"),
        ({"a": one, "b": two}, "keys"),
    ):
        with pytest.raises(SystemExit, match=match):
            w.basis(mixed)


def test_a_summary_keeps_every_file_withheld_so_far_with_its_key_id() -> None:
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "withhold_text", root / "scripts/withhold_text.py"
    )
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    entry = {"fields": ["prediction"], "texts": 3}
    old = {"key_id": "old", "files": 2, "texts": 6, "by_file": {"a.json": entry, "b.json": entry}}
    merged = script.merged_summary(old, {"c.json": {"fields": ["passes"], "texts": 4}}, "new")
    assert merged["files"] == 3 and merged["texts"] == 10
    assert merged["key_ids"] == ["new", "old"]
    assert merged["by_file"]["a.json"]["key_id"] == "old"
    assert merged["by_file"]["c.json"]["key_id"] == "new"
    rerun = script.merged_summary(merged, {"a.json": entry}, "new")
    assert rerun["files"] == 3 and rerun["by_file"]["a.json"]["key_id"] == "new"
    assert script.merged_summary(None, {}, "k")["files"] == 0


PLAIN = hashlib.sha256(b"a prompt").hexdigest()
OTHER_PLAIN = hashlib.sha256(b"another prompt").hexdigest()


def test_a_plain_hash_is_rekeyed_by_hmac_of_its_hex_with_its_shape_kept() -> None:
    job = {
        "rules_files": {"base:openstack-a": PLAIN, "base:openstack-b": OTHER_PLAIN},
        "base_marks": {"i0": PLAIN},
        "results": {"base|a": [{"id": "i0", "fingerprint": PLAIN, "exact_match": 1.0}]},
    }
    published, count = w.withhold(job, "retrieval-foreign-openstack.json", KEY)
    assert count == 4
    assert list(published["rules_files_hmac"]) == ["base:openstack-a", "base:openstack-b"]
    assert published["rules_files_hmac"]["base:openstack-a"] == w.digest(PLAIN, KEY)
    assert published["base_marks_hmac"] == {"i0": w.digest(PLAIN, KEY)}
    row = published["results"]["base|a"][0]
    assert list(row) == ["id", "fingerprint_hmac", "exact_match"]
    assert row["fingerprint_hmac"] == w.digest(PLAIN, KEY) != PLAIN
    assert not {"rules_files", "base_marks"} & published.keys()
    assert published["withheld_text"]["fields"] == ["base_marks", "fingerprint", "rules_files"]
    assert published["withheld_text"]["key_id"] == w.key_id(KEY)
    assert job["base_marks"] == {"i0": PLAIN}, "the input is not mutated"


def test_a_rekeyed_file_is_left_as_it_is() -> None:
    published, _ = w.withhold({"rows": [{"id": "i", "fingerprint": PLAIN}]}, "r.json", KEY)
    assert w.withhold(published, "r.json", KEY) == (published, 0)


def test_a_plain_hash_digests_differently_under_each_key() -> None:
    one, _ = w.withhold({"fingerprint": PLAIN}, "r.json", KEY)
    other, _ = w.withhold({"fingerprint": PLAIN}, "r.json", bytes(range(1, 33)))
    assert one["fingerprint_hmac"] != other["fingerprint_hmac"]


@pytest.mark.parametrize(
    "value",
    ["m0", "AB" * 32, "ab" * 31, None, 3, {"i": "not a hash"}, ["ab" * 32, 1], {"i": {"j": PLAIN}}],
)
def test_a_plain_hash_field_that_is_not_hashes_is_refused(value: object) -> None:
    for field in sorted(w.PLAIN_HASHES):
        with pytest.raises(ValueError, match=field):
            w.withhold({"rows": [{field: value}]}, "r.json", KEY)


def test_a_plain_hash_is_found_by_the_walk_the_guard_uses() -> None:
    document = {"rules_files": {"a": PLAIN}, "rows": [{"fingerprint": PLAIN}], "x": {"y": 1}}
    assert w.plain_left(document) == ["/rules_files", "/rows/0/fingerprint"]
    assert w.plain_left({"rules_files_hmac": {"a": PLAIN}}) == []


@pytest.mark.parametrize(
    "meta", [None, "text", ["fields"], {"key_id": "k", "fields": "base_marks"}, {"fields": 3}]
)
def test_a_malformed_withheld_text_is_refused_not_misread(meta: object) -> None:
    doc = {"withheld_text": meta}
    with pytest.raises(SystemExit, match="withheld_text"):
        w.hash_basis({"a": (doc, "base_marks")})
    if not isinstance(meta, dict):
        with pytest.raises(SystemExit, match="withheld_text"):
            w.basis({"a": doc})


def test_a_field_is_keyed_only_when_it_is_listed_whole() -> None:
    doc = {"withheld_text": {"key_id": "k", "fields": ["base_marks_extra"]}}
    assert w.hash_basis({"a": (doc, "base_marks")}) == ""
    assert (
        w.hash_basis({"a": ({"withheld_text": {"key_id": "k", "fields": ["x"]}}, "x")}) == w.SUFFIX
    )


def test_a_digested_plain_hash_field_holds_only_digests() -> None:
    for bad in ({"rules-distilled:openstack-a": None}, {"rules-written:qt": "m0"}, [None], None):
        document = {"base_marks_hmac": bad}
        assert w.refusals("r.json", b"", [document]), bad
    assert not w.refusals(
        "r.json", b"", [{"base_marks_hmac": {"rules-distilled:openstack-a": "a" * 64}}]
    )
