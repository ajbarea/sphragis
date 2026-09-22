"""What must not appear in a published artifact, and how it is taken out.

Sibling of `scrub.py`, and deliberately a separate stage. `scrub` runs at ingestion and
pseudonymises Gerrit account objects and review comment text; it leaves the diff payload
alone on purpose, because rewriting anything in the source that merely looks like an
address would alter the code the study measures (`fetchers.py` states the reason).

That is right for the corpus and insufficient for what gets released. An address written
*inside* a config file, a Debian control file or a DNS record is code, so it survives the
scrub, reaches the reference text and the model's predictions, and lands in the committed
artifacts under `datasets/results/`. Releasing those is not defensible practice: CIDR
(arXiv:2605.12153) does not retain author addresses in its released artifact, and Gold and
Krinke (EMSE 2021) treat repository mining as human-subjects research, where public
availability is not by itself evidence of intent to publish.

So the corpus keeps the code intact and the release is redacted, and the two stages are
separate because they answer different questions.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

#: What replaces an address. Opaque and identical for every address, because these are
#: incidental strings inside code rather than an identity field the study links records
#: by, so nothing needs them to stay distinct. A salted pseudonym would need the corpus
#: salt to reproduce and would still fall to a dictionary attack over a known contributor
#: list, which is what pseudonymising a small closed set buys.
PLACEHOLDER = "<redacted-email>"

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

#: `.invalid` and `.example` are reserved by RFC 2606 and `example.org`/`example.com` are
#: the documentation domains, so an address in one of them is illustrative rather than a
#: person. The rest are this repository's own fixtures and the maintainer's own address,
#: which every job script carries by design.
_NOT_A_PERSON = re.compile(
    r"""
    example\.(org|com|net)
    | \.invalid$ | \.example$
    | @(mock|mark|decorators|pytest)\b
    | your-email
    | @rit\.edu
    | noreply | localhost
    """,
    re.VERBOSE,
)


def third_party_addresses(text: str) -> set[str]:
    """Addresses in `text` belonging to someone other than the maintainer."""
    return {found for found in _EMAIL.findall(text) if not _NOT_A_PERSON.search(found)}


def addresses_in(path: pathlib.Path) -> set[str]:
    """Third-party addresses in an artifact, read from its decoded string values.

    Decoded rather than raw, because an escaped newline abutting a Python decorator reads
    as one address in the file text: `"\\n@app.route"` offers the regex `n@app.route`,
    which has the shape of an address and is a line of code.
    """
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for item in node.values():
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            found.update(third_party_addresses(node))

    try:
        walk(json.loads(path.read_text()))
    except ValueError:
        # Not JSON this can read; the raw scan is the conservative fallback.
        found.update(third_party_addresses(path.read_text()))
    return found


def _redact(value: Any, found: set[str]) -> Any:
    if isinstance(value, dict):
        return {key: _redact(item, found) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, found) for item in value]
    if isinstance(value, str):
        addresses = third_party_addresses(value)
        if not addresses:
            return value
        found |= addresses
        for address in addresses:
            value = value.replace(address, PLACEHOLDER)
        return value
    return value


def _mark_redacted(value: Any) -> Any:
    """Flag the records whose own text the substitution changed.

    The flag goes on the record rather than the file, because the reader who needs it is
    the one comparing one row's text against one row's `edit_similarity`.
    """
    if isinstance(value, dict):
        marked = {key: _mark_redacted(item) for key, item in value.items()}
        carries_pair = "prediction" in marked or "reference" in marked
        if carries_pair and any(
            isinstance(item, str) and PLACEHOLDER in item for item in marked.values()
        ):
            marked["identities_redacted"] = True
        return marked
    if isinstance(value, list):
        return [_mark_redacted(item) for item in value]
    return value


def redact_file(path: pathlib.Path) -> tuple[set[str], str | None]:
    """Addresses found in `path`, and its redacted text when anything changed."""
    if not addresses_in(path):
        return set(), None
    found: set[str] = set()
    data = _mark_redacted(_redact(json.loads(path.read_text()), found))
    # Two spaces and a trailing newline, matching what the scripts write.
    return found, json.dumps(data, indent=2) + "\n"
