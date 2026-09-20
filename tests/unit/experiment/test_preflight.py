"""Checks that run before a job is queued, not after it fails."""

from __future__ import annotations

from sphragis.experiment.preflight import (
    check_kwargs_supported,
    check_paths_exist,
    check_repo_matches,
)


def test_unsupported_kwargs_are_named() -> None:
    # transformers 5 removed warmup_ratio from TrainingArguments. A pilot died on this
    # after a queue wait and a completed base evaluation.
    def target(*, alpha: int = 1, beta: int = 2) -> None: ...

    problems = check_kwargs_supported(target, {"alpha": 1, "gamma": 3})
    assert problems == ["gamma is not accepted by target"]


def test_supported_kwargs_pass_silently() -> None:
    def target(*, alpha: int = 1, beta: int = 2) -> None: ...

    assert check_kwargs_supported(target, {"alpha": 1, "beta": 2}) == []


def test_a_callable_taking_kwargs_accepts_anything() -> None:
    def target(**kwargs: object) -> None: ...

    assert check_kwargs_supported(target, {"whatever": 1}) == []


def test_missing_paths_are_named(tmp_path) -> None:  # noqa: ANN001
    present = tmp_path / "there.jsonl"
    present.write_text("{}\n")
    problems = check_paths_exist({"examples": present, "script": tmp_path / "missing.py"})
    assert problems == [f"script does not exist: {tmp_path / 'missing.py'}"]


def test_an_empty_file_is_reported_as_well(tmp_path) -> None:  # noqa: ANN001
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert check_paths_exist({"examples": empty}) == [f"examples is empty: {empty}"]


def test_a_stale_remote_checkout_is_detected() -> None:
    # A pilot failed with ModuleNotFoundError because the cluster copy predated the
    # package it imported.
    assert check_repo_matches(local="abc123", remote="abc123") == []
    problems = check_repo_matches(local="abc123", remote="def456")
    assert len(problems) == 1 and "abc123" in problems[0] and "def456" in problems[0]


def test_an_unknown_remote_revision_is_reported_not_assumed() -> None:
    problems = check_repo_matches(local="abc123", remote=None)
    assert len(problems) == 1 and "could not determine" in problems[0]
