"""JSON lines are redacted through their decoded strings, and ignored inputs are left alone."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

from sphragis.corpus.redact import addresses_in, redact_file

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "redact_identities", ROOT / "scripts" / "redact_identities.py"
)
assert _spec is not None and _spec.loader is not None
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

#: A decorator after two escaped newlines: raw, it reads as `n@functools.cache`.
CODE = {"id": "a", "after": "x = 1\n\n@functools.cache\ndef f(self): ..."}
PERSON = {"id": "b", "after": "# contact jane.doe@redhat.com for access"}


def _jsonl(path: Path, *rows: dict) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def test_an_escaped_newline_before_a_decorator_is_not_an_address(tmp_path: Path) -> None:
    path = _jsonl(tmp_path / "inputs.jsonl", CODE)
    assert addresses_in(path) == set()
    assert redact_file(path) == (set(), None)


def test_a_jsonl_file_keeps_its_escapes_and_its_untouched_lines(tmp_path: Path) -> None:
    path = _jsonl(tmp_path / "inputs.jsonl", CODE, PERSON)
    original = path.read_text().splitlines()
    found, redacted = redact_file(path)
    assert found == {"jane.doe@redhat.com"}
    assert redacted is not None and redacted.endswith("\n")
    lines = redacted.splitlines()
    assert lines[0] == original[0]  # no address: byte for byte
    rows = [json.loads(line) for line in lines]  # every line still parses
    assert rows[0] == CODE
    assert "jane.doe@redhat.com" not in rows[1]["after"] and rows[1]["identities_redacted"]


def test_a_line_that_is_not_json_is_scanned_as_text(tmp_path: Path) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text('{"id": "c", "after": "mail jane.doe@redhat.com\n')
    assert addresses_in(path) == {"jane.doe@redhat.com"}


def test_an_ignored_input_is_not_an_artifact(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("results/inputs/\n")
    results = tmp_path / "results"
    (results / "inputs").mkdir(parents=True)
    _jsonl(results / "inputs" / "window.jsonl", PERSON)
    (results / "published.json").write_text(json.dumps(PERSON))
    (results / "new.txt").write_text("jane.doe@redhat.com")  # untracked, not ignored
    assert script.artifacts(results) == [results / "new.txt", results / "published.json"]
