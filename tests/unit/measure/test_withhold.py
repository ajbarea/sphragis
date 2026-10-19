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
    assert w.text_left(published, "rq1-partition-openstack-p1.json") == []


def test_a_prediction_interval_is_not_text() -> None:
    across = {"estimate": 0.1, "prediction": [0.0, 0.2], "other": {"prediction": None}}
    assert w.withhold(across, "across-orgs.json", KEY) == (across, 0)


def test_fields_belong_to_their_files() -> None:
    """`file` is a rule list in a reviews distillation and a path everywhere else."""
    rules = {"files": {"openstack-a": {"file": "- a rule", "rules": ["- a rule"], "chunks": 2}}}
    published, count = w.withhold(rules, "rules-reviews-openstack-p10.json", KEY)
    assert count == 2 and published["files"]["openstack-a"]["chunks"] == 2
    assert w.withhold({"file": "datasets/results/x.json"}, "k-coverage-qt.json", KEY)[1] == 0
    assert w.withhold(rules, "rules-guide-openstack.json", KEY)[1] == 0


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
