"""The split of a reference's log-probability by token class adds back up to s exactly."""

from __future__ import annotations

import math
import re

import pytest
from pygments.lexer import Lexer
from pygments.token import Name

from sphragis.experiment.likelihood import COMPONENTS
from sphragis.experiment.tokens import (
    CELLS,
    char_classes,
    lexemes,
    lexer_for,
    lexer_unsure,
    split,
    target_cells,
)

_PIECE = re.compile(r"\w+|\s+|.", re.DOTALL)


class OffsetTokenizer:
    """Words, runs of space and single characters, with their offsets, as a fast tokenizer gives."""

    eos_token_id = 7

    def __call__(self, text, add_special_tokens=True, return_offsets_mapping=False):
        pieces = [(m.start(), m.end()) for m in _PIECE.finditer(text)]
        out = {"input_ids": [hash(text[a:b]) % 1000 for a, b in pieces]}
        if return_offsets_mapping:
            out["offset_mapping"] = pieces
        return out


def _row(before: str, after: str, path: str = "m.py") -> dict:
    return {"before": before, "after": after, "path": path}


def _target(row: dict) -> list[int]:
    tokenizer = OffsetTokenizer()
    return [*tokenizer(row["after"], add_special_tokens=False)["input_ids"], tokenizer.eos_token_id]


def test_a_renamed_name_is_a_changed_identifier_and_the_frame_is_copied() -> None:
    after = "def f(value):\n    return value\n"
    classes, lexed = char_classes("def f(x):\n    return x\n", after, "m.py")
    assert lexed == {"lexer": "Python", "lex_error_chars": 0, "line_disagreement": 0.0}
    by_char = dict(zip(range(len(after)), classes, strict=True))
    assert by_char[after.index("value")] == "identifier_changed"
    assert by_char[after.index(" f(") + 1] == "identifier_copied"
    assert by_char[after.index("return")] == "other_copied"
    assert by_char[after.index(":")] == "other_copied"


def test_a_php_fragment_without_its_open_tag_is_still_lexed_as_php() -> None:
    after = "$x = bar($y);"
    classes, lexed = char_classes("$x = foo($y);", after, "a.php")
    assert lexed["lexer"] == "PHP"
    assert classes[after.index("bar")] == "identifier_changed"
    assert classes[after.index("$y")] == "identifier_copied"


def test_a_path_no_lexer_claims_is_read_as_text() -> None:
    assert lexer_for("BUILD.unknownext").name == "Text only"
    classes, _ = char_classes("a b", "a c", "BUILD.unknownext")
    assert classes == ["other_copied", "other_copied", "other_changed"]


def test_ambiguous_suffixes_take_the_registered_language() -> None:
    assert lexer_for("src/corelib/qobject.h").name == "C++"
    assert lexer_for("qtbase.pro").name == "Text only"
    classes, _ = char_classes("", "class Foo : public Bar {};", "a.h")
    assert classes[0] == "other_changed"  # `class` is a keyword, not a name


def test_a_one_word_edit_inside_a_comment_changes_that_word_only() -> None:
    before = "x = 1  # the quick fox jumps over\n"
    after = "x = 1  # the quick fox jumped over\n"
    classes, _ = char_classes(before, after, "m.py")
    changed = {i for i, c in enumerate(classes) if c.endswith("changed")}
    assert changed == set(range(after.index("jumped"), after.index("jumped") + len("jumped")))


def test_a_hunk_opening_inside_a_docstring_is_flagged() -> None:
    before = '    the result.\n    """\n    return foo(a, b)\n'
    after = '    the result.\n    """\n    return foo(a, c)\n'
    _, lexed = char_classes(before, after, "m.py")
    assert lexed["line_disagreement"] > 0
    _, clean = char_classes("return foo(a, b)\n", "return foo(a, c)\n", "m.py")
    assert clean["line_disagreement"] == 0.0


def test_lexemes_that_do_not_tile_the_text_are_refused() -> None:
    class Shifted(Lexer):
        name = "shifted"

        def get_tokens_unprocessed(self, text):
            yield 1, Name, text

    with pytest.raises(ValueError, match="does not tile the text at offset 0"):
        lexemes("abc", Shifted())


def test_every_target_token_is_classed_and_the_end_token_last() -> None:
    row = _row("x = 1\n", "x = compute(1)\n")
    cells, _ = target_cells(OffsetTokenizer(), row, _target(row))
    assert len(cells) == len(_target(row))
    assert cells[-1] == "end"
    assert set(cells) <= set(CELLS)
    assert cells[2] == "other_copied"  # "="
    assert cells[4] == "identifier_changed"  # "compute"


def test_a_token_touching_a_name_counts_as_an_identifier() -> None:
    class Coarse(OffsetTokenizer):
        def __call__(self, text, add_special_tokens=True, return_offsets_mapping=False):
            out = {"input_ids": [1]}
            if return_offsets_mapping:
                out["offset_mapping"] = [(0, len(text))]
            return out

    cells, _ = target_cells(Coarse(), _row("(", "(x"), [1, 7])
    assert cells == ["identifier_changed", "end"]


def test_a_target_other_than_the_references_tokens_is_refused() -> None:
    row = _row("a", "b")
    shifted = [i + 1 for i in _target(row)]
    with pytest.raises(ValueError, match="not the supervised target's tokens"):
        target_cells(OffsetTokenizer(), row, shifted)


def test_the_components_add_back_to_the_mean_per_token_both_ways() -> None:
    row = _row("x = f(1)\n", "x = g(1, y)\n")
    cells, _ = target_cells(OffsetTokenizer(), row, _target(row))
    logprobs = [-0.1 * (i + 1) for i in range(len(cells))]
    out = split(logprobs, cells)
    mean = sum(logprobs) / len(logprobs)
    parts = lambda *names: sum(out[f"logprob_per_token:{n}"] for n in names)  # noqa: E731
    assert math.isclose(parts("identifier", "other", "end"), mean)
    assert math.isclose(parts("changed", "copied", "end"), mean)
    assert sum(c["tokens"] for c in out["cells"].values()) == len(cells)
    assert set(COMPONENTS) >= {"identifier", "other", "changed", "copied", "end"}


def test_a_count_mismatch_is_refused() -> None:
    with pytest.raises(ValueError, match="2 log-probabilities for 1 tokens"):
        split([-1.0, -2.0], ["end"])


def test_suffixes_pygments_misreads_take_their_registered_language() -> None:
    assert lexer_for("api-ref/source/servers.inc").name == "reStructuredText"
    assert lexer_for("tools/check.pl").name == "Perl"
    assert lexer_for("sql/patch.sql").name == "SQL"
    assert lexer_for("skins/Foo.tpl").name == "Text only"


def test_a_language_outside_the_lexed_set_is_read_as_text() -> None:
    assert lexer_for("scene.pov").name == "Text only"


def test_builtins_are_not_the_codes_own_names() -> None:
    classes, _ = char_classes("self.x = len(a)\n", "self.x = len(b)\n", "m.py")
    after = "self.x = len(b)\n"
    assert classes[after.index("self")] == "other_copied"
    assert classes[after.index("len")] == "other_copied"
    assert classes[after.index("x")] == "identifier_copied"


def test_an_inserted_line_is_changed_whole_and_its_neighbours_copied() -> None:
    before = "def f(x):\n    return x\n"
    after = "def f(x):\n    y = compute(x)\n    return x\n"
    classes, _ = char_classes(before, after, "m.py")
    inserted = range(after.index("    y"), after.index("    return"))
    assert all(classes[i].endswith("changed") for i in inserted)
    assert all(c.endswith("copied") for i, c in enumerate(classes) if i not in inserted)


def test_a_row_its_lexer_cannot_tile_is_read_as_text(monkeypatch) -> None:
    class Shifted(Lexer):
        name = "shifted"

        def get_tokens_unprocessed(self, text):
            yield 1, Name, text

    monkeypatch.setattr("sphragis.experiment.tokens.lexer_for", lambda path: Shifted())
    classes, lexed = char_classes("a b", "a c", "m.py")
    assert lexed["lexer"] == "text, shifted could not tile it"
    assert classes == ["other_copied", "other_copied", "other_changed"]


def test_a_row_is_lexer_unsure_on_error_characters_or_line_disagreement() -> None:
    assert not lexer_unsure({"lex_error_chars": 0, "line_disagreement": 0.0})
    assert lexer_unsure({"lex_error_chars": 2, "line_disagreement": 0.0})
    assert lexer_unsure({"lex_error_chars": 0, "line_disagreement": 0.01})
    _, clean = char_classes("x = 1\n", "x = 2\n", "a.py")
    assert not lexer_unsure(clean)
    for missing in ({}, {"lex_error_chars": 0}, {"line_disagreement": 0.0}):
        with pytest.raises(ValueError, match="no lexer record"):
            lexer_unsure({"id": "x", **missing})
