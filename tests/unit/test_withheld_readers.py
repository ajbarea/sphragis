"""Readers of results published with their model text as keyed digests.

`decoder_read` and `determinism_check --compare` only compare predictions, and digests compare
as well as text does; `decoding_check` greps the text for the planted marker, so it cannot read
a digested copy.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from sphragis.measure import withhold as w

ROOT = Path(__file__).resolve().parents[2]
KEY = bytes(range(32))
OTHER = bytes(range(1, 33))


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


decoder_read = _script("decoder_read")
determinism_check = _script("determinism_check")

INTERVAL = {"estimate": 0.1, "low": 0.0, "high": 0.2}


def _run(texts: list[str], penalty: float) -> dict:
    return {
        "max_new_tokens": 8,
        "repetition_penalty": [penalty],
        "results": {
            "base|a": [
                {"id": f"i{n}", "prediction": t, "exact_match": 1.0} for n, t in enumerate(texts)
            ]
        },
        "verdict": {"verdict": "pass", "binding": {"openstack": INTERVAL}},
    }


def _save(directory: Path, name: str, document: dict, key: bytes | None = None) -> None:
    if key is not None:
        document, _ = w.withhold(document, name, key)
    (directory / name).write_text(json.dumps(document))


def test_decoder_read_compares_a_digested_pair(tmp_path: Path) -> None:
    stored, rerun = _run(["a", "b", "c"], 1.1), _run(["a", "b", "x"], 1.0)
    plain = tmp_path / "plain"
    digested = tmp_path / "digested"
    plain.mkdir()
    digested.mkdir()
    for directory, key in ((plain, None), (digested, KEY)):
        _save(directory, "s.json", stored, key)
        _save(directory, "r.json", rerun, key)
    from_text = decoder_read.pair("s.json", "r.json", plain)
    from_digests = decoder_read.pair("s.json", "r.json", digested)
    assert from_digests == from_text
    assert from_digests["arms"]["base|a"]["predictions_kept"] == 2


def test_decoder_read_refuses_a_mixed_or_differently_keyed_pair(tmp_path: Path) -> None:
    _save(tmp_path, "plain.json", _run(["a"], 1.1))
    _save(tmp_path, "k1.json", _run(["a"], 1.0), KEY)
    _save(tmp_path, "k2.json", _run(["a"], 1.0), OTHER)
    with pytest.raises(SystemExit, match="published and full-text"):
        decoder_read.pair("plain.json", "k1.json", tmp_path)
    with pytest.raises(SystemExit, match="different keys"):
        decoder_read.pair("k1.json", "k2.json", tmp_path)


def _job(node: str, passes: dict) -> dict:
    return {
        "ids": ["i0", "i1"],
        "half": "a",
        "repetition_penalty": 1.0,
        "passes": passes,
        "provenance": {"slurm": {"node": node}},
    }


def _compare(capsys: pytest.CaptureFixture[str], paths: list[Path]) -> str:
    args = determinism_check.parser.parse_args(["--compare", *map(str, paths)])
    determinism_check.compare(args)
    return capsys.readouterr().out


def test_determinism_compare_reads_a_digested_pair(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    jobs = {
        "n1.json": _job("n1", {"bf16": {"i0": "a", "i1": "b"}}),
        "n2.json": _job("n2", {"bf16": {"i0": "a", "i1": "c"}}),
    }
    plain = tmp_path / "plain"
    digested = tmp_path / "digested"
    plain.mkdir()
    digested.mkdir()
    for directory, key in ((plain, None), (digested, KEY)):
        for name, job in jobs.items():
            _save(directory, name, job, key)
    outputs = [_compare(capsys, [d / "n1.json", d / "n2.json"]) for d in (plain, digested)]
    assert outputs[0] == outputs[1]
    assert "   1" in outputs[1]


def test_determinism_compare_refuses_a_mixed_pair(tmp_path: Path) -> None:
    job = _job("n1", {"bf16": {"i0": "a", "i1": "b"}})
    _save(tmp_path, "plain.json", job)
    _save(tmp_path, "digested.json", _job("n2", {"bf16": {"i0": "a", "i1": "b"}}), KEY)
    args = determinism_check.parser.parse_args(
        ["--compare", str(tmp_path / "plain.json"), str(tmp_path / "digested.json")]
    )
    with pytest.raises(SystemExit, match="published and full-text"):
        determinism_check.compare(args)


def test_decoding_check_refuses_a_digested_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The script imports the model stack at module level; it is refused before any model loads.
    model = ModuleType("sphragis.experiment.model")
    model.REPETITION_PENALTY = 1.1  # type: ignore[attr-defined]
    model.HFGenerator = object  # type: ignore[attr-defined]
    model.run_provenance = dict  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sphragis.experiment.model", model)
    decoding_check = _script("decoding_check")
    run = _run(["a"], 1.0)
    _save(tmp_path, "cal.json", run, KEY)
    argv = ["--results", str(tmp_path / "cal.json"), "--corpus-dir", str(tmp_path)]
    argv += ["--adapter", str(tmp_path), "--out", str(tmp_path / "o.json")]
    saved = sys.argv
    sys.argv = ["decoding_check", *argv]
    try:
        with pytest.raises(SystemExit, match="full-text copy"):
            decoding_check.main()
    finally:
        sys.argv = saved
