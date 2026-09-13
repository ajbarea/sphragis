"""Corpus manifest: counts, hashes, and the verification that guards them."""

from __future__ import annotations

from sphragis.corpus.manifest import corpus_manifest, verify, window_hash

WINDOWS: dict[str, list[str]] = {"pilot": ["b", "a"], "train": ["c"], "dev": [], "test": []}


def test_window_hash_is_order_independent_and_content_sensitive() -> None:
    assert window_hash(["a", "b"]) == window_hash(["b", "a"])
    assert window_hash(["a", "b"]) != window_hash(["a", "c"])


def test_corpus_manifest_records_counts_hashes_and_provenance() -> None:
    m = corpus_manifest(org="openstack", windows=WINDOWS, stats={"dropped_no_comment": 4})
    assert m["org"] == "openstack"
    assert m["counts"] == {"pilot": 2, "train": 1, "dev": 0, "test": 0}
    assert m["hashes"]["pilot"] == window_hash(["a", "b"])
    assert m["stats"]["dropped_no_comment"] == 4
    assert m["git"] and m["packages"] is not None and m["generated_at"]


def test_verify_is_clean_for_the_windows_it_was_built_from() -> None:
    assert verify(corpus_manifest(org="qt", windows=WINDOWS, stats={}), WINDOWS) == []


def test_verify_reports_count_and_hash_drift() -> None:
    m = corpus_manifest(org="qt", windows=WINDOWS, stats={})
    problems = verify(m, {**WINDOWS, "train": ["c", "d"]})
    assert len(problems) == 1 and "train" in problems[0]


def test_verify_reports_a_window_absent_from_the_manifest() -> None:
    m = corpus_manifest(org="qt", windows=WINDOWS, stats={})
    problems = verify(m, {**WINDOWS, "holdout": ["z"]})
    assert len(problems) == 1 and "holdout" in problems[0]
