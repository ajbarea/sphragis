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


def test_a_distillation_of_a_written_guide_is_not_withheld() -> None:
    guide = {"kind": "guide", "map_lists": ["- m"], "reduce_answer": None, "file": "- r"}
    assert w.withhold({"files": {"o": guide}}, "rules-guide-openstack.json", KEY)[1] == 0


def test_a_guide_is_exempt_only_in_the_shape_a_written_guide_has() -> None:
    guide = {"kind": "guide", "map_lists": ["- m"], "reduce_answer": None, "file": "- r"}
    real = {**guide, "evidence": [], "recurrence": []}
    assert w.withhold({"files": {"o": real}}, "g.json", KEY)[1] == 0
    for bad in (
        {"reduce_answer": "- a rule"},
        {"evidence": ["a comment"]},
        {"recurrence": [{"rule": "a comment"}]},
    ):
        assert w.withhold({"files": {"o": {**real, **bad}}}, "g.json", KEY)[1] > 0, bad


def test_a_distillation_is_known_by_its_kind_and_a_renamed_field_is_withheld() -> None:
    distilled = {"kind": "reviews", "rules": ["- r"], "file": "- r"}
    assert w.withhold({"o": distilled}, "any.json", KEY)[1] == 2
    assert w.withhold({"rows": [{"output": "t", "generation": ["u", "v"]}]}, "r.json", KEY)[1] == 3


def test_determinism_passes_keep_their_ids() -> None:
    doc = {"passes": {"bf16": {"a": "x", "b": "y"}}}
    published, count = w.withhold(doc, "determinism-sym-0-gh-a-001-rp1.0.json", KEY)
    assert count == 2 and published["passes_hmac"]["bf16"] == {
        "a": w.digest("x", KEY),
        "b": w.digest("y", KEY),
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
