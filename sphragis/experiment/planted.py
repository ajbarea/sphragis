"""Plant a fingerprint of known strength, and find out whether the contrast can see it.

Every null this study has produced is ambiguous between two statements: there is no
organizational fingerprint, or this instrument cannot see fingerprints of any size. The
existing positive control does not separate them. It compares an organization's adapter
against the base model, which establishes that adaptation works; RQ1's contrast is matched
adapter against MISMATCHED adapter, and nothing establishes that this contrast can detect a
difference known to be there.

So: split one organization's corpus into two halves that are statistically identical, impose
a mechanical convention on a fraction of one half, and run the identical contrast. Sweeping
the fraction gives the instrument's detection floor, and a null then stops being an absence
and becomes a bound: the organizational fingerprint, if any, is weaker than a convention
applied to this share of refinements.

The convention has to be mechanical, semantics-preserving, visible to the binding metric,
and the kind of thing an organization actually enforces. Quote style is all four: linters
enforce it, it changes the target text exactly where exact match reads, and it changes no
behaviour.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

# A Python string literal with no escapes and no quote of the other kind inside it. Narrow
# on purpose: a transform that mangles a literal it half-understands would plant noise
# rather than a convention, and the point is that the planted signal is exactly known.
#
# The lookbehind is load-bearing. Without it the scan resumes inside an escaped literal and
# pairs the wrong quotes: `x = 'it\'s'` came out as `x = 'it\"s"`, which is neither the old
# convention nor the new one. It also refuses a quote glued to a word character, which is
# how an apostrophe inside prose gets read as an opening delimiter.
SINGLE_QUOTED = re.compile(r"(?<![\\\w])'([^'\"\\\n]*)'")


def flip_quotes(text: str) -> str:
    """Single-quoted Python string literals become double-quoted. Semantics unchanged."""
    return SINGLE_QUOTED.sub(r'"\1"', text)


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
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Impose the convention on a fraction of rows, and report what was actually imposed.

    The realised rate is not the nominal one and must never be reported as if it were. The
    transform is a no-op on a refinement containing no single-quoted literal, so asking for
    half and getting a fifth is the normal case. Every number this calibration produces is
    indexed by the realised rate.
    """
    if not 0.0 <= fraction <= 1.0:
        raise ValueError(f"fraction must lie in [0, 1], got {fraction}")
    rng = random.Random(seed)
    out: list[dict[str, Any]] = []
    eligible = changed = 0
    for row in rows:
        copy = dict(row)
        text = str(copy.get(field, ""))
        if would_change(text, transform):
            eligible += 1
            if rng.random() < fraction:
                copy[field] = transform(text)
                changed += 1
        out.append(copy)
    total = len(out)
    return out, {
        "nominal_fraction": fraction,
        "examples": float(total),
        "eligible": float(eligible),
        "changed": float(changed),
        "realised_fraction": changed / total if total else 0.0,
        "realised_of_eligible": changed / eligible if eligible else 0.0,
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
