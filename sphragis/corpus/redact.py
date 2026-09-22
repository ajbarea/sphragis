"""What must not appear in a published artifact, and how it is taken out.

Sibling of `scrub.py`, and deliberately a separate stage. `scrub` runs at ingestion and
pseudonymises Gerrit account objects and review comment text; it leaves the diff payload
alone on purpose, because rewriting anything in the source that merely looks like an
address would alter the code the study measures (`fetchers.py` states the reason).

That is right for the corpus and insufficient for what gets released. An address written
*inside* a config file, a Debian control file or a DNS record is code, so it survives the
scrub, reaches the reference text and the model's predictions, and lands in the committed
artifacts. One reaches further still: an OpenStack candidacy file is named after the
candidate's address, so the path becomes part of an example's id. Releasing those is not
defensible practice: CIDR (arXiv:2605.12153) does not retain author addresses in its
released artifact, and Gold and Krinke (EMSE 2021) treat repository mining as
human-subjects research, where public availability is not by itself evidence of intent to
publish.

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
#: by. A salted pseudonym would need the corpus salt to reproduce and would still fall to
#: a dictionary attack over a known contributor list, which is what pseudonymising a small
#: closed set buys.
PLACEHOLDER = "<redacted-email>"

#: Domains that name a document rather than a person. RFC 2606 reserves `example.*`,
#: `.invalid` and `.test` for exactly this, and `localhost` resolves nowhere.
_DOCUMENTATION_DOMAINS = frozenset({"example.org", "example.com", "example.net", "localhost"})
#: Subdomains count: `lists.example.org` is a documentation mailing list, while
#: `bob@notexample.org` and `tel@example.org.attacker.com` are not documentation at all,
#: which is why these are suffixes with a leading dot rather than substrings.
_DOCUMENTATION_SUFFIXES = (
    ".example",
    ".invalid",
    ".test",
    ".localhost",
    ".example.org",
    ".example.com",
    ".example.net",
)

#: The maintainer's own domain, which every job script carries by design.
_MAINTAINER_DOMAIN = "rit.edu"

#: Local parts that are a role rather than a person.
_ROLE_LOCAL_PARTS = frozenset({"noreply", "no-reply", "your-email", "root", "admin"})

#: Decorator text caught by the address shape. A raw `"\\n@mock.patch"` offers the regex
#: `n@mock.patch`, and the escaped newline is what supplies the local part, so these are
#: recognised by a local part of exactly `n` beside one of these first labels. Matching the
#: label alone would drop `u@mark.com`, which is a person. Reading decoded values removes
#: most of these already; this is the belt to that braces.
_DECORATOR_LABELS = frozenset({"mock", "mark", "decorators", "pytest", "app", "patch", "route"})


def _looks_like_a_person(address: str) -> bool:
    """Whether `address` plausibly belongs to someone, matched on its parsed parts.

    Matched structurally rather than by substring, because substring exclusions silently
    drop real addresses: a bare `example.com` rule also blocks `eve@counterexample.com`,
    a bare `rit.edu` rule blocks `spy@rit.edu.cn`, and a bare `noreply` rule blocks
    `noreply.jane@redhat.com`. Every one of those is a person, and a detector that drops
    a person is worse than no detector, because it reads as a clean result.
    """
    local, _, domain = address.rpartition("@")
    local, domain = local.lower(), domain.lower()
    if not local or not domain:
        return False
    if domain in _DOCUMENTATION_DOMAINS or domain.endswith(_DOCUMENTATION_SUFFIXES):
        return False
    if domain == _MAINTAINER_DOMAIN:
        return False
    if local in _ROLE_LOCAL_PARTS:
        return False
    return not (local == "n" and domain.partition(".")[0] in _DECORATOR_LABELS)


#: Deliberately permissive; `_looks_like_a_person` does the deciding.
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def third_party_addresses(text: str) -> set[str]:
    """Addresses in `text` belonging to someone other than the maintainer."""
    return {found for found in _EMAIL.findall(text) if _looks_like_a_person(found)}


def redact_text(text: str) -> str:
    """`text` with every third-party address replaced.

    Public because a consumer that joins a published record against the corpus has to put
    the corpus side through the same substitution, or the join silently loses the rows
    whose id carried an address.
    """
    for address in third_party_addresses(text):
        text = text.replace(address, PLACEHOLDER)
    return text


def addresses_in(path: pathlib.Path) -> set[str]:
    """Third-party addresses in a file, whatever its type.

    JSON is read from its decoded string values, because an escaped newline abutting a
    Python decorator reads as one address in the raw file text: `"\\n@app.route"` offers
    the regex `n@app.route`, which has the shape of an address and is a line of code.

    Anything else is scanned as text, and anything undecodable as text is scanned as
    latin-1 so a binary container still gets looked at rather than skipped. A file class
    nobody scans is how a guard passes while the disclosure stands.
    """
    try:
        text = path.read_text()
    except (UnicodeDecodeError, ValueError):
        return third_party_addresses(path.read_bytes().decode("latin-1", errors="replace"))

    if path.suffix != ".json":
        return third_party_addresses(text)

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
        walk(json.loads(text))
    except ValueError:
        # A truncated or malformed artifact is an expected state here: the battery writes
        # a `.partial.json` by design. Fall back to the raw scan rather than skipping it.
        return third_party_addresses(text)
    return found


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _mark_redacted(value: Any) -> Any:
    """Flag every mapping whose own strings the substitution changed.

    Any mapping, not only one carrying `prediction` or `reference`: a contamination score
    row has neither and still had its `id` redacted, so keying the flag on those two left
    five redacted records silently unflagged and the test that checked for the flag shared
    the blind spot exactly.
    """
    if isinstance(value, dict):
        marked = {key: _mark_redacted(item) for key, item in value.items()}
        if any(isinstance(item, str) and PLACEHOLDER in item for item in marked.values()):
            marked["identities_redacted"] = True
        return marked
    if isinstance(value, list):
        return [_mark_redacted(item) for item in value]
    return value


def redact_file(path: pathlib.Path) -> tuple[set[str], str | None]:
    """Addresses found in `path`, and its redacted text when anything changed.

    Non-JSON and unparseable files are redacted as plain text, so a malformed artifact is
    reported and fixed rather than raising out of the walk.

    The original's trailing newline is reproduced rather than imposed. The scripts write
    `json.dumps(..., indent=2)` with no newline, one writes it with, and appending one
    everywhere would make a re-run produce a byte-different file, against the invariant
    this repository states and against the deploy guard that reads results-file bytes.
    """
    found = addresses_in(path)
    if not found:
        return set(), None

    original = path.read_text(errors="replace")
    trailing = "\n" if original.endswith("\n") else ""
    if path.suffix != ".json":
        return found, redact_text(original)
    try:
        data = json.loads(original)
    except ValueError:
        return found, redact_text(original)
    return found, json.dumps(_mark_redacted(_redact(data)), indent=2) + trailing
