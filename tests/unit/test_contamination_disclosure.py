"""The contamination disclosure record against its schema, the pinned checkpoint and the windows."""

from __future__ import annotations

import ast
import json
import re
from datetime import date, timedelta
from pathlib import Path

import jsonschema

from sphragis.corpus.windows import WINDOWS

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "disclosure"


def _model_constants() -> tuple[dict[str, object], dict[str, str]]:
    """`sphragis/experiment/model.py`'s module constants and `MODEL_REVISIONS`, read from source:
    importing the module needs torch, which CI does not install."""
    tree = ast.parse((ROOT / "sphragis" / "experiment" / "model.py").read_text())
    constants: dict[str, object] = {}
    revisions: dict[str, str] = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)):
            continue
        target = node.targets[0].id
        if isinstance(node.value, ast.Constant):
            constants[target] = node.value.value
        elif target == "MODEL_REVISIONS" and isinstance(node.value, ast.Dict):
            for key, value in zip(node.value.keys, node.value.values, strict=True):
                assert isinstance(key, ast.Name) and isinstance(value, ast.Constant)
                assert isinstance(value.value, str)
                revisions[str(constants[key.id])] = value.value
    return constants, revisions


CONSTANTS, MODEL_REVISIONS = _model_constants()
MODEL_ID = str(CONSTANTS["MODEL_ID"])
RECORD = json.loads((DOCS / "contamination-disclosure.json").read_text())


def test_the_record_validates_against_the_authors_schema() -> None:
    schema = json.loads((DOCS / "disclosure.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(RECORD)


def test_the_record_names_the_pinned_checkpoint() -> None:
    system = RECORD["system_under_test"]
    assert system.startswith(f"{MODEL_ID} at Hub revision {MODEL_REVISIONS[MODEL_ID]}")


def test_every_window_starts_after_the_weights_were_written() -> None:
    """The direct and temporal controls rest on this, not on a self-reported cutoff."""
    written = re.search(
        r"weights were written by commit \w+ on (\d{4}-\d{2}-\d{2})", RECORD["system_under_test"]
    )
    assert written, "the record states no weight-upload date"
    assert all(start > written[1] for start, _ in WINDOWS.values())


def test_the_benchmark_names_the_sealed_test_window() -> None:
    start, end = WINDOWS["test"]
    last = (date.fromisoformat(end) - timedelta(days=1)).isoformat()
    assert f"created {start} to {last}" in RECORD["benchmark"]["name"]


def test_the_elicitation_budget_is_the_code_s() -> None:
    budget = RECORD["elicitation_budget"]
    assert budget["token_budget"].startswith(f"{CONSTANTS['MAX_NEW_TOKENS']} new tokens")
    assert f"repetition penalty {CONSTANTS['REPETITION_PENALTY']}" in budget["decoding"]
