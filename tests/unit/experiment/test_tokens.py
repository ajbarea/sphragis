"""The split of a reference's log-probability by token class adds back up to s exactly."""

from __future__ import annotations

import math
import re

import pytest

from sphragis.experiment.tokens import (
    CELLS,
    COMPONENTS,
    char_classes,
    lexer_for,
    split,
    target_cells,
)

_PIECE = re.compile(r"\w+|\s+|.", re.DOTALL)


class OffsetTokenizer:
    """Words, runs of space and single characters, with their offsets, as a fast tokenizer gives."""

    def __call__(self, text, add_special_tokens=True, return_offsets_mapping=False):
        pieces = [(m.start(), m.end()) for m in _PIECE.finditer(text)]
        out = {"input_ids": [hash(text[a:b]) % 1000 for a, b in pieces]}
        if return_offsets_mapping:
            out["offset_mapping"] = pieces
        return out


def _row(before: str, after: str, path: str = "m.py") -> dict:
    return {"before": before, "after": after, "path": path}


def test_a_renamed_name_is_a_changed_identifier_and_the_frame_is_copied() -> None:
    after = "def f(value):\n    return value\n"
    classes, lexed = char_classes("def f(x):\n    return x\n", after, "m.py")
    assert lexed == {"lexer": "Python", "lex_error_chars": 0}
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


def test_a_path_no_lexer_claims_reads_every_word_as_a_name() -> None:
    assert lexer_for("BUILD.unknownext").name == "words"
    classes, _ = char_classes("a b", "a c", "BUILD.unknownext")
    assert classes == ["identifier_copied", "other_copied", "identifier_changed"]


def test_every_target_token_is_classed_and_the_end_token_last() -> None:
    row = _row("x = 1\n", "x = compute(1)\n")
    cells, _ = target_cells(OffsetTokenizer(), row)
    ids = OffsetTokenizer()(row["after"], add_special_tokens=False)["input_ids"]
    assert len(cells) == len(ids) + 1
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

    cells, _ = target_cells(Coarse(), _row("(", "(x"))
    assert cells == ["identifier_changed", "end"]


def test_offsets_that_change_the_ids_are_refused() -> None:
    class Drifting(OffsetTokenizer):
        def __call__(self, text, add_special_tokens=True, return_offsets_mapping=False):
            out = super().__call__(text, add_special_tokens, return_offsets_mapping)
            if return_offsets_mapping:
                out["input_ids"] = [i + 1 for i in out["input_ids"]]
            return out

    with pytest.raises(ValueError, match="offsets changed the tokenization"):
        target_cells(Drifting(), _row("a", "b"))


def test_the_components_add_back_to_the_mean_per_token_both_ways() -> None:
    cells, _ = target_cells(OffsetTokenizer(), _row("x = f(1)\n", "x = g(1, y)\n"))
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
