"""Plant a convention of known strength, and find out whether the contrast can see it.

A null is ambiguous between no organizational house style and an instrument that cannot see
one. The positive control (an organization's adapter against the base model) shows adaptation
works; it does not show that RQ1's contrast, matched adapter against mismatched, can detect a
difference known to be there.

So one organization's corpus is split into two statistically identical halves, a mechanical
convention is imposed on a fraction of one half (or each half its own,
`symmetric_planted_corpora`), and the same contrast is run. Sweeping the fraction gives the
detection floor, and a null becomes a bound: any house style is weaker than a convention
applied to that share of refinements.

The convention must be mechanical, semantics-preserving, visible to the binding metric, and
one organizations enforce. Quote style is all four: linters enforce it, it changes the target
text exactly where exact match reads, and it changes no behaviour.
"""

from __future__ import annotations

import io
import random
import re
import tokenize
from collections.abc import Callable, Mapping, Sequence
from typing import Any

# A Python string literal with no escapes and no quote of the other kind inside it. Narrow
# on purpose: a transform that mangles a literal it half-understands would plant noise
# rather than a convention, and the point is that the planted signal is exactly known.
#
# The lookbehind is load-bearing. Without it the scan resumes inside an escaped literal and
# pairs the wrong quotes: `x = 'it\'s'` came out as `x = 'it\"s"`, which matches no convention,
# old or new. It also refuses a quote glued to a word character, which is how an apostrophe
# inside prose gets read as an opening delimiter.
SINGLE_QUOTED = re.compile(r"(?<![\\\w])'([^'\"\\\n]*)'")


def flip_quotes(text: str) -> str:
    """Single-quoted Python string literals become double-quoted. Semantics unchanged."""
    return SINGLE_QUOTED.sub(r'"\1"', text)


# The two halves of the symmetric quote-style plant (`PLANT_CONVENTIONS`). Literals are found by
# Python's own tokenizer, not a pattern: a pattern rewrites the quotes of a pair nested inside a
# literal of the other kind (`"Instance '%s'"`), and inside an f-string's braces it nests same-kind
# quotes, which is a syntax error before 3.12. Only a plain single-line literal whose text holds no
# quote or backslash is restyled; prefixed literals and anything inside an f- or t-string are left
# alone, and a fragment that does not tokenize is not restyled at all. `flip_quotes` keeps its
# pattern for the stored calibration.
_OPEN = {getattr(tokenize, n) for n in ("FSTRING_START", "TSTRING_START") if hasattr(tokenize, n)}
_CLOSE = {getattr(tokenize, n) for n in ("FSTRING_END", "TSTRING_END") if hasattr(tokenize, n)}


def _restyle(text: str, quote: str) -> str:
    """Every plain literal quoted the other way rewritten with `quote`, or the text unchanged."""
    other = "'" if quote == '"' else '"'
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return text
    # The tokenizer's rows are the lines readline gives, split on newlines alone; splitlines also
    # splits on form feeds and line separators, which would shift every later edit.
    offsets = [0]
    for line in io.StringIO(text).readlines():
        offsets.append(offsets[-1] + len(line))
    edits, depth = [], 0
    for token in tokens:
        if token.type in _OPEN:
            depth += 1
        elif token.type in _CLOSE:
            depth -= 1
        elif token.type == tokenize.STRING and depth == 0:
            literal = token.string
            inner = literal[1:-1]
            if (
                len(literal) >= 2
                and literal[0] == other == literal[-1]
                and not any(c in inner for c in "'\"\\\n")
            ):
                (row, col), (end_row, end_col) = token.start, token.end
                edits.append(
                    (offsets[row - 1] + col, offsets[end_row - 1] + end_col, quote + inner + quote)
                )
    for start, end, new in reversed(edits):
        text = text[:start] + new + text[end:]
    return text


def to_double(text: str) -> str:
    """Single-quoted plain literals become double-quoted: one half's quote style."""
    return _restyle(text, '"')


def to_single(text: str) -> str:
    """Double-quoted plain literals become single-quoted: the other half's quote style."""
    return _restyle(text, "'")


def is_python(row: Mapping[str, Any]) -> bool:
    """Quote style is a convention in Python; in PHP or shell the two quotes differ in meaning."""
    return str(row.get("path") or "").endswith(".py")


MARKER = "  # reviewed"
# The other half's convention in the symmetric design. Same shape and length as MARKER so the
# two are equally easy to learn and neither side is handed the simpler rule.
MARKER_OTHER = "  # approved"


def _append(text: str, marker: str) -> str:
    if not text or text.rstrip().endswith(marker.strip()):
        return text
    return text.rstrip("\n") + marker


def append_marker_other(text: str) -> str:
    """The second half's annotation in the symmetric design; see `symmetric_planted_corpora`."""
    return _append(text, MARKER_OTHER)


def append_marker(text: str) -> str:
    """Append a fixed annotation to the refinement's last line.

    The ceiling case, and deliberately artificial. Quote style is realistic but eligible on
    only 14.8% of OpenStack's refinements and 1.7% of Qt's, so a sweep over it cannot reach
    a strong enough signal to find where detection fails. This applies to every refinement
    and is the easiest thing a model could learn, so the contrast failing to see it would
    mean the contrast cannot see anything. Reported as a ceiling, never as a realistic
    organizational convention.
    """
    return _append(text, MARKER)


def would_change(text: str, transform: Callable[[str], str] = flip_quotes) -> bool:
    return transform(text) != text


def halves(
    rows: Sequence[Mapping[str, Any]], *, seed: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split into two halves by change, so no change contributes to both sides.

    Splitting by example would put a change's own refinements on both sides, and the halves
    would share vocabulary for a reason that has nothing to do with the planted convention.
    """
    by_change: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_change.setdefault(str(row["change_id"]), []).append(dict(row))
    ids = sorted(by_change)
    rng = random.Random(seed)
    rng.shuffle(ids)
    cut = len(ids) // 2
    left = [row for cid in ids[:cut] for row in by_change[cid]]
    right = [row for cid in ids[cut:] for row in by_change[cid]]
    return left, right


def plant(
    rows: Sequence[Mapping[str, Any]],
    *,
    fraction: float,
    seed: int,
    field: str = "after",
    transform: Callable[[str], str] = flip_quotes,
    applies: Callable[[Mapping[str, Any]], bool] | None = None,
    record_ids: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Impose the convention on a fraction of rows, and report what was actually imposed.

    The realised rate is not the nominal one and must never be reported as if it were. The
    transform is a no-op on a refinement containing no single-quoted literal, so asking for
    half and getting a fifth is the normal case. Every number this calibration produces is
    indexed by the realised rate. A row `applies` refuses is never eligible. With `record_ids`, the
    ids of the rows changed are reported, so a reader can take the planted examples apart.
    """
    if not 0.0 <= fraction <= 1.0:
        raise ValueError(f"fraction must lie in [0, 1], got {fraction}")
    rng = random.Random(seed)
    out: list[dict[str, Any]] = []
    eligible = changed = 0
    changed_ids: list[str] = []
    for row in rows:
        copy = dict(row)
        text = str(copy.get(field, ""))
        new = transform(text) if applies is None or applies(row) else text
        if new != text:
            eligible += 1
            if rng.random() < fraction:
                copy[field] = new
                changed += 1
                changed_ids.append(str(row.get("id")))
        out.append(copy)
    total = len(out)
    return out, {
        "nominal_fraction": fraction,
        "examples": float(total),
        "eligible": float(eligible),
        "changed": float(changed),
        "realised_fraction": changed / total if total else 0.0,
        "realised_of_eligible": changed / eligible if eligible else 0.0,
        **({"changed_ids": changed_ids} if record_ids else {}),
    }


def planted_corpora(
    rows: Sequence[Mapping[str, Any]],
    *,
    fraction: float,
    seed: int,
    field: str = "after",
    transform: Callable[[str], str] = flip_quotes,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, float]]:
    """Two halves of one corpus, the convention imposed on the second only.

    The first half is returned untouched rather than given a complementary convention, so
    the contrast measures one planted difference and not two.
    """
    left, right = halves(rows, seed=seed)
    planted_right, report = plant(
        right, fraction=fraction, seed=seed + 1, field=field, transform=transform
    )
    return left, planted_right, report


def symmetric_planted_corpora(
    rows: Sequence[Mapping[str, Any]],
    *,
    fraction: float,
    seed: int,
    field: str = "after",
    transform_a: Callable[[str], str] = append_marker_other,
    transform_b: Callable[[str], str] = append_marker,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, float]]]:
    """Two halves, each carrying its OWN convention at the same rate.

    The asymmetric design leaves one half convention-free, and that is not what two
    organizations look like: each has its own habits. It also produced the result this exists
    to test. At a quarter, the planted adapter over-applied its convention and lost on its own
    half, because the unplanted adapter never added anything and so matched every reference
    that lacked the annotation. With a convention on both sides, neither adapter has that
    free win, and whether the matched adapter now wins on both sides is the question the
    gate actually asks of two organizations.
    """
    left, right = halves(rows, seed=seed)
    planted_left, report_a = plant(
        left, fraction=fraction, seed=seed + 2, field=field, transform=transform_a
    )
    planted_right, report_b = plant(
        right, fraction=fraction, seed=seed + 1, field=field, transform=transform_b
    )
    return planted_left, planted_right, {"a": report_a, "b": report_b}


#: Each convention the symmetric plant can impose: the two halves' transforms, in side order,
#: and which rows it may touch. `marker` is check 5's; `quotes` is a code-level convention, the
#: realistic signal the exchange rate between exact match and s is read on (review panel item 15).
PLANT_CONVENTIONS: dict[
    str,
    tuple[
        tuple[Callable[[str], str], Callable[[str], str]],
        Callable[[Mapping[str, Any]], bool] | None,
    ],
] = {
    "marker": ((append_marker_other, append_marker), None),
    "quotes": ((to_single, to_double), is_python),
}
