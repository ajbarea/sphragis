"""Which revisions change the reviewed hunk's layout alone, by the language's own tokens."""

from __future__ import annotations

import pytest

from sphragis.experiment.layout import (
    FREE_FORM,
    LINE_BREAKS,
    language,
    layout_only,
    row_layout_only,
)

LAYOUT = [
    ("c spacing", "int a=1;", "int a = 1;", "a.cpp"),
    ("c brace style", "if (x) {\n  y();\n}", "if (x)\n{\n    y();\n}", "a.cc"),
    ("c comment spacing", "x = 1;  //  a  b", "x = 1; // a b", "a.c"),
    ("c header as C++", "int f(int a,int b);", "int f(int a, int b);", "a.h"),
    ("java wrap", "f(a,\n  b);", "f(a, b);", "A.java"),
    ("js spacing within a line", "const a=[1,2];", "const a = [1, 2];", "a.js"),
    ("js indentation", "if (x) {\n  y();\n}", "if (x) {\n    y();\n}", "a.ts"),
    ("go spacing", "a:=b+c", "a := b + c", "a.go"),
    ("php spacing", "$a=f($b,$c);", "$a = f($b, $c);", "a.php"),
    ("json spacing", '{"a":1,"b":[1,2]}', '{"a": 1, "b": [1, 2]}', "a.json"),
    ("py wrap in brackets", "f(a,\n  b)", "f(a, b)", "a.py"),
    ("py spacing", "x=f(a,b)\n", "x = f(a, b)\n", "a.py"),
    ("py comment spacing", "x = 1  # note", "x = 1 # note", "a.py"),
    ("py fstring expression spacing", 'x = f"{a + b}"\n', 'x = f"{a+b}"\n', "a.py"),
    ("py trailing whitespace", "x = 1   \ny = 2\n", "x = 1\ny = 2\n", "a.py"),
    ("pyi spacing", "def f(a,b): ...\n", "def f(a, b): ...\n", "a.pyi"),
]

NOT_LAYOUT = [
    ("identical", "same", "same", "a.cpp"),
    ("a token changed", "int a=1;", "int a = 2;", "a.cpp"),
    ("empty hunk", "", "   \n", "a.py"),
    ("whitespace only", " \n", "\n", "a.c"),
    ("py string space", 'x = "a b"\n', 'x = "ab"\n', "a.py"),
    ("py string tab", 'x = "a\tb"\n', 'x = "a b"\n', "a.py"),
    ("py fstring literal", 'x = f"{p} : "\n', 'x = f"{p}: "\n', "a.py"),
    ("py string wording", 'x = "can not"\n', 'x = "cannot"\n', "a.py"),
    ("py triple string", 'x = """a\nb"""\n', 'x = """ab"""\n', "a.py"),
    ("py out of a block", "if x:\n    y()\n    z()\n", "if x:\n    y()\nz()\n", "a.py"),
    ("py hunk shifted into a block", "        x()\n", "            x()\n", "a.py"),
    ("py hunk shifted out of a block", "    y()\n", "  y()\n", "a.py"),
    ("py re-indented block", "if x:\n  y()\n", "if x:\n    y()\n", "a.py"),
    ("py tab to spaces", "if x:\n\ty()\n", "if x:\n    y()\n", "a.py"),
    ("py comment moved to its own line", "x = 1  # a\n", "x = 1\n# a\n", "a.py"),
    ("py names merged", "return x\n", "returnx\n", "a.py"),
    ("py operator merged", "a not in b\n", "a notin b\n", "a.py"),
    ("py attribute of a number", "1 .real\n", "1.real\n", "a.py"),
    ("py untokenizable", "f(a,\n", "f(a, \n", "a.py"),
    ("py backslash removed, a character", "x = 1 + \\\n    2\n", "x = 1 + 2\n", "a.py"),
    ("py no-break space", "x = 1\n", "x\u00a0= 1\n", "a.py"),
    ("c names merged", "int x;", "intx;", "a.c"),
    ("c operators merged", "a - -b", "a--b", "a.c"),
    ("c shift from two comparisons", "a < <b", "a << b", "a.cpp"),
    ("js spread from dots", "f(. ..a)", "f(...a)", "a.js"),
    # `>>` is a shift before C++11, so the closers are kept apart.
    ("cpp template closers", "vector<vector<int> > v;", "vector<vector<int>> v;", "a.cpp"),
    ("c string space", 'puts("a b");', 'puts("ab");', "a.c"),
    ("c macro made object-like", "#define X(a) a\n", "#define X (a) a\n", "a.c"),
    ("c no-break space", "x = 1;", "x\u00a0= 1;", "a.c"),
    ("js line break ends a statement", "return\nx", "return x", "a.js"),
    ("js template literal", "s = `a b`;", "s = `ab`;", "a.js"),
    ("go line break ends a statement", "x := f(\n)\n", "x := f()\n", "a.go"),
    ("php string", "$a = 'x y';", "$a = 'xy';", "a.php"),
    ("json string", '{"a": "x y"}', '{"a": "xy"}', "a.json"),
    ("shell word split", '[ -n "$HOST_NAME"]', '[ -n "$HOST_NAME" ]', "a.sh"),
    ("patch", ' [ -n "$X"]', ' [ -n "$X" ]', "0028-fix.patch"),
    ("makefile tab", "all:\n\techo hi\n", "all:\n    echo hi\n", "Makefile"),
    ("makefile spacing", "x  =  1", "x = 1", "Makefile"),
    ("yaml nesting", "a:\n  b: 1\n", "a:\nb: 1\n", "a.yaml"),
    ("yaml spacing", "a:\n  b: 1\n", "a:\n  b:   1\n\n", "a.yml"),
    ("yaml block scalar", "a: |\n  x\n  y\n", "a: |\n  x\n\n  y\n", "a.yaml"),
    ("rst indentation", "a\n  b\n", "a\nb\n", "a.rst"),
    ("markdown code block", "x\n\n    code\n", "x\n\ncode\n", "a.md"),
    ("dockerfile", "RUN a b", "RUN ab", "Dockerfile"),
    ("toml", 'a = "x y"', 'a = "xy"', "a.toml"),
    ("text", "a  b", "a b", "README"),
    ("css descendant to compound", ".mw-a .mw-b {}", ".mw-a.mw-b {}", "a.css"),
    ("css pseudo-class", "a :hover {}", "a:hover {}", "a.css"),
    ("less list to subtraction", "@x: @a -@b;", "@x: @a - @b;", "a.less"),
    ("py self-documenting f-string", 'x = f"{port_id = }"\n', 'x = f"{port_id=}"\n', "a.py"),
    ("py self-documenting with spec", 'x = f"{a = :>4}"\n', 'x = f"{a=:>4}"\n', "a.py"),
    ("c define string", '#define MSG "Usage:  foo"\n', '#define MSG "Usage: foo"\n', "a.c"),
    ("c include path", '#include "a  b.h"\n', '#include "a b.h"\n', "a.h"),
    ("jsx text", "x = <b>{a} {b}</b>;", "x = <b>{a}{b}</b>;", "a.jsx"),
    ("js markup text", "x = <b>{a} {b}</b>;", "x = <b>{a}{b}</b>;", "a.js"),
    ("php template text", "<b>x</b> y", "<b>x</b>y", "a.php"),
    ("js line break in a comment", "return /* a\n */ x", "return /* a */ x", "a.js"),
    ("scala 3 indentation", "if x then\n  a()\n  b()\n", "if x then\n  a()\nb()\n", "a.scala"),
]


@pytest.mark.parametrize(("label", "before", "after", "path"), LAYOUT, ids=[c[0] for c in LAYOUT])
def test_a_formatters_edit_is_layout_only(label, before, after, path) -> None:
    assert layout_only(before, after, path) is True


@pytest.mark.parametrize(
    ("label", "before", "after", "path"), NOT_LAYOUT, ids=[c[0] for c in NOT_LAYOUT]
)
def test_an_edit_whitespace_can_carry_meaning_in_is_not(label, before, after, path) -> None:
    assert layout_only(before, after, path) is False


@pytest.mark.parametrize(("before", "after"), [(None, "x"), ("x", None), (None, None)])
def test_a_missing_hunk_is_not_layout_only(before, after) -> None:
    assert layout_only(before, after, "a.py") is False


def test_every_free_form_language_resolves_and_line_breaks_are_among_them() -> None:
    paths = {
        "a.c": "C",
        "a.cpp": "C++",
        "a.h": "C++",
        "a.css": None,
        "a.less": None,
        "a.jsx": None,
        "a.go": "Go",
        "a.json": "JSON",
        "a.java": "Java",
        "a.js": "JavaScript",
        "a.php": "PHP",
        "a.qml": "QML",
        "a.sql": "SQL",
        "a.scala": None,
        "a.ts": "TypeScript",
        "a.py": "python",
        "a.sh": None,
        "a.yaml": None,
        "Makefile": None,
        "a.rst": None,
        "a.patch": None,
    }
    assert {path: language(path) for path in paths} == paths
    assert LINE_BREAKS <= FREE_FORM


IN_PLACE = [
    # A hunk inside a string its context does not open: the closer reads as an opener.
    (
        "py inside a docstring",
        "    usage: tool\n    options:\n    -h help",
        "    Use  --force to skip.",
        "    Use --force to skip.",
        '    """)',
        "a.py",
        False,
    ),
    ("js inside a template literal", "x\ny\nz", "a  b", "a b", "w\n`;\nf();", "a.js", False),
    ("java inside a text block", "a\nb", "c  d", "c d", 'e\n""";\nf();', "A.java", False),
    (
        "py f-string expression in place",
        "def f(a, b):",
        '    return f"{a + b}"',
        '    return f"{a+b}"',
        "",
        "a.py",
        True,
    ),
    # A continuation line re-indented inside a bracket the context opens: layout.
    ("py list item re-indented", "x = [", '     ("a", b),', '    ("a", b),', "]", "a.py", True),
    ("py call wrapped in place", "log(", '  "a %s",\n  b)', '    "a %s", b)', "", "a.py", True),
    # Judged against the lines around it, a hunk shifted into a block is not layout.
    ("py moved into a block", "if a:\n    b()", "    c()", "        c()", "d()", "a.py", False),
    ("py moved out of a block", "if a:\n    b()", "    c()", "c()", "    d()", "a.py", False),
    # Inside a string the context opens, its spaces are the string's.
    ("js inside a template literal", "s = `a", "b c", "b  c", "`;", "a.js", False),
    ("php inside a heredoc", "$s = <<<EOT", "a b", "a  b", "EOT;", "a.php", False),
    ("c spacing in place", "int f() {", "  return a+b;", "  return a + b;", "}", "a.c", True),
]


@pytest.mark.parametrize(
    ("label", "context_before", "before", "after", "context_after", "path", "expected"),
    IN_PLACE,
    ids=[c[0] for c in IN_PLACE],
)
def test_a_revision_is_judged_between_its_unchanged_lines(
    label, context_before, before, after, context_after, path, expected
) -> None:
    assert layout_only(before, after, path, context_before, context_after) is expected
    row = {"before": before, "after": after, "path": path}
    row |= {"context_before": context_before, "context_after": context_after}
    assert row_layout_only(row) is expected


def test_a_revision_that_reads_in_place_only_once_is_not_layout() -> None:
    """The hunk dedents to a level the context never opened, so only the revision reads in place."""
    context_before, context_after = "if a:\n        b()", ""
    before, after = "      c()", "        c()"
    assert layout_only(before, after, "a.py") is False
    assert layout_only(before, after, "a.py", context_before, context_after) is False
