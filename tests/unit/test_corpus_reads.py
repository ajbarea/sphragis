"""Every script that trains through `build_supervised` must read its corpus through the loader.

`derived_file_rows` (`sphragis.corpus.load`) refuses a corpus file that is stale, unrecorded or
empty; a bare `json.loads` over a file's lines bypasses that refusal silently. The rule enforced
here, deliberately simple: a script that calls `build_supervised` must import `derived_file_rows`
and must not call `json.loads` at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = sorted(
    p for p in (_ROOT / "scripts").glob("*.py") if "build_supervised" in p.read_text()
)


def _imports_derived_file_rows(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "sphragis.corpus.load"
        and any(alias.name == "derived_file_rows" for alias in node.names)
        for node in ast.walk(tree)
    )


def _calls_json_loads(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "loads"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "json"
        for node in ast.walk(tree)
    )


@pytest.mark.parametrize("script", _SCRIPTS, ids=lambda p: p.name)
def test_reads_the_corpus_through_the_loader(script: Path) -> None:
    tree = ast.parse(script.read_text())
    assert _imports_derived_file_rows(tree), f"{script.name}: does not import derived_file_rows"
    assert not _calls_json_loads(tree), f"{script.name}: calls json.loads directly"


def test_there_are_scripts_to_check() -> None:
    assert len(_SCRIPTS) >= 5


def _legacy_corpus_help(path: Path) -> str | None:
    for node in ast.walk(ast.parse(path.read_text())):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "--legacy-corpus"
        ):
            continue
        for kw in node.keywords:
            if kw.arg == "help" and isinstance(kw.value, ast.Constant):
                value = kw.value.value
                return value if isinstance(value, str) else None
    return None


@pytest.mark.parametrize("script", ["fdlora_schedule.py", "preflight_pilot.py"])
def test_legacy_corpus_help_text_matches_dual_adapter_updates(script: str) -> None:
    reference = _legacy_corpus_help(_ROOT / "scripts" / "dual_adapter_updates.py")
    assert reference is not None
    assert _legacy_corpus_help(_ROOT / "scripts" / script) == reference


def test_fdlora_schedule_records_legacy_corpus_in_its_output() -> None:
    text = (_ROOT / "scripts" / "fdlora_schedule.py").read_text()
    assert '"legacy_corpus": args.legacy_corpus' in text


def test_preflight_pilot_reports_a_refused_corpus_rather_than_exiting() -> None:
    """`derived_file_rows` raises `SystemExit` on a stale corpus; the loop's own `except
    Exception` does not catch it, so every other organization's problems would go unreported
    unless `SystemExit` is caught ahead of it and turned into a normal `problems` entry."""
    text = (_ROOT / "scripts" / "preflight_pilot.py").read_text()
    loop = text[text.index("for org, path in corpora.items():") :]
    system_exit_at = loop.index("except SystemExit")
    generic_at = loop.index("except Exception")
    assert system_exit_at < generic_at, "SystemExit must be caught before the generic Exception"
    assert "problems.append" in loop[system_exit_at:generic_at]
