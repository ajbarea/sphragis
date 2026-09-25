"""Every script that trains through `build_supervised` must read its corpus through the loader.

`derived_file_rows`/`refined_examples` (`sphragis.corpus.load`) refuse a corpus file that is
stale, unrecorded or empty; a bare `json.load(s)` over a file's lines bypasses that refusal
silently, and `built_examples` reads a month before the label rules touch it. The rule enforced
here: a script that calls `build_supervised` must call `derived_file_rows` or `refined_examples`,
must not call `json.load`/`json.loads`, and must not import `built_examples` -- each resolved
through any import form (a direct name, an aliased name, or a module alias's attribute).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = sorted(
    p for p in (_ROOT / "scripts").glob("*.py") if "build_supervised" in p.read_text()
)

# Scripts that legitimately call json.loads on something that is not a training corpus (a prior
# result, a config file). Named here, narrowly, rather than weakening the rule for everyone; empty
# because no script currently needs it.
_JSON_LOADS_ALLOWED: dict[str, str] = {}


def _module_aliases(tree: ast.AST, module: str) -> set[str]:
    """Local names bound to `module` itself: `import module` or `import module as x`."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module:
                    names.add(alias.asname or module.split(".")[0])
    return names


def _from_import_aliases(tree: ast.AST, module: str, wanted: set[str]) -> set[str]:
    """Local names bound directly to one of `wanted`'s members of `module`."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            for alias in node.names:
                if alias.name in wanted:
                    names.add(alias.asname or alias.name)
    return names


def _calls_any(tree: ast.AST, *, direct: set[str], attrs: set[str], via: set[str]) -> bool:
    """A call to a name in `direct`, or to `x.attr` where `attr` is in `attrs`, `x` in `via`."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in direct:
            return True
        if (
            isinstance(func, ast.Attribute)
            and func.attr in attrs
            and isinstance(func.value, ast.Name)
            and func.value.id in via
        ):
            return True
    return False


_LOADERS = {"derived_file_rows", "refined_examples"}


def _calls_a_loader(tree: ast.AST) -> bool:
    """`derived_file_rows(...)` or `refined_examples(...)`, resolved through any import form."""
    return _calls_any(
        tree,
        direct=_from_import_aliases(tree, "sphragis.corpus.load", _LOADERS),
        attrs=_LOADERS,
        via=_module_aliases(tree, "sphragis.corpus.load"),
    )


_JSON_READERS = {"load", "loads"}


def _calls_json_loads(tree: ast.AST) -> bool:
    """`json.load(s)(...)`, resolved through any import form."""
    return _calls_any(
        tree,
        direct=_from_import_aliases(tree, "json", _JSON_READERS),
        attrs=_JSON_READERS,
        via=_module_aliases(tree, "json"),
    )


def _imports_built_examples(tree: ast.AST) -> bool:
    """`built_examples`, imported directly or reached through a module alias's attribute."""
    if _from_import_aliases(tree, "sphragis.corpus.load", {"built_examples"}):
        return True
    aliases = _module_aliases(tree, "sphragis.corpus.load")
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "built_examples"
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
        ):
            return True
    return False


@pytest.mark.parametrize("script", _SCRIPTS, ids=lambda p: p.name)
def test_reads_the_corpus_through_the_loader(script: Path) -> None:
    tree = ast.parse(script.read_text())
    assert _calls_a_loader(tree), f"{script.name}: does not call derived_file_rows/refined_examples"
    if script.name not in _JSON_LOADS_ALLOWED:
        assert not _calls_json_loads(tree), f"{script.name}: calls json.load(s) directly"
    assert not _imports_built_examples(tree), f"{script.name}: reads built_examples"


def test_there_are_scripts_to_check() -> None:
    assert len(_SCRIPTS) >= 5


# --- Probes: each escape the rule above exists to catch --------------------------------------


def _tree(source: str) -> ast.AST:
    return ast.parse(source)


def test_an_unused_loader_import_beside_a_json_import_is_still_caught() -> None:
    source = (
        "from json import loads\n"
        "from sphragis.corpus.load import derived_file_rows\n"
        "def f(path):\n"
        "    return [loads(x) for x in path.read_text().splitlines()]\n"
    )
    tree = _tree(source)
    assert not _calls_a_loader(tree), "derived_file_rows is imported but never called"
    assert _calls_json_loads(tree), "loads(...) reads the corpus around the guard"


def test_a_renamed_json_module_is_still_caught() -> None:
    source = (
        "import json as j\n"
        "def f(path):\n"
        "    return [j.loads(x) for x in path.read_text().splitlines()]\n"
    )
    assert _calls_json_loads(_tree(source))


def test_built_examples_is_refused_however_it_is_reached() -> None:
    direct = _tree(
        "from sphragis.corpus.load import built_examples\nrows = built_examples(root, 'o')\n"
    )
    assert _imports_built_examples(direct)
    via_alias = _tree("import sphragis.corpus.load as L\nrows = L.built_examples(root, 'o')\n")
    assert _imports_built_examples(via_alias)


def test_a_loader_reached_through_a_module_alias_passes() -> None:
    source = (
        "import sphragis.corpus.load as L\ndef f(path):\n    return L.derived_file_rows(path)\n"
    )
    tree = _tree(source)
    assert _calls_a_loader(tree)
    assert not _calls_json_loads(tree)
    assert not _imports_built_examples(tree)


# --- The rest of what these scripts must get right about --legacy-corpus ----------------------


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


def test_legacy_corpus_help_text_matches_dual_adapter_updates() -> None:
    reference = _legacy_corpus_help(_ROOT / "scripts" / "dual_adapter_updates.py")
    assert reference is not None
    assert _legacy_corpus_help(_ROOT / "scripts" / "fdlora_schedule.py") == reference


def test_preflight_pilot_still_offers_a_legacy_corpus_flag() -> None:
    # preflight writes no output, so its help text says what it checks rather than what it
    # records; it need not match dual_adapter_updates.py's, only exist and name the flag it mimics.
    help_text = _legacy_corpus_help(_ROOT / "scripts" / "preflight_pilot.py")
    assert help_text is not None
    assert "--legacy-corpus" in help_text


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
