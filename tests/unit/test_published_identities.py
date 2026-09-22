"""No committed artifact discloses a third party's email address.

The corpus scrub pseudonymises account objects and comment text and deliberately leaves
the diff payload alone, because rewriting anything in the source that looks like an
address would corrupt the code the study measures. The consequence only shows up on the
far side of the pipeline: an address written inside a config file, a Debian control file
or a DNS record rides through the reference text and the model's predictions into
`datasets/results/`, which is committed and, since the repository went public, released.

Twenty addresses reached the published artifact that way before anything caught it. The
scrub cannot be the thing that catches it without changing what the corpus measures, so
the check belongs here, on what gets committed. A new measurement that carries one fails
this test, and `scripts/redact_identities.py --write` is the fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sphragis.corpus.redact import PLACEHOLDER, addresses_in, third_party_addresses

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "datasets" / "results"


def test_no_committed_result_discloses_a_third_party_address() -> None:
    disclosed: dict[str, set[str]] = {}
    for path in sorted(RESULTS.rglob("*.json")):
        found = addresses_in(path)
        if found:
            disclosed[path.name] = found
    assert not disclosed, (
        "third-party addresses in committed artifacts; run "
        f"`python scripts/redact_identities.py --write`:\n{disclosed}"
    )


def test_the_detector_would_actually_fire() -> None:
    """A test asserting an absence has to show it can detect a presence."""
    assert third_party_addresses("contact: someone@windriver.com") == {"someone@windriver.com"}
    assert third_party_addresses("    email: a.person@zte.com.cn") == {"a.person@zte.com.cn"}


@pytest.mark.parametrize(
    "benign",
    [
        "alice@example.org",  # RFC 2606 documentation domain, used by the fixtures
        "bob@example.com",
        "your-email@example.com",
        "ajb6289@rit.edu",  # the maintainer, already on every job script
        f"email: {PLACEHOLDER}",  # an address this repository has already redacted
    ],
)
def test_the_detector_leaves_the_harmless_alone(benign: str) -> None:
    """A detector that flags the fixtures gets switched off, which is the real failure."""
    assert third_party_addresses(benign) == set()


def test_redacted_records_say_so() -> None:
    """Where the text was redacted, `edit_similarity` was measured on the original.

    It is left at the measured value rather than recomputed, because the repository's
    stated invariant is that a re-run reproduces the artifact. The flag is what keeps that
    from reading as drift, so a redacted placeholder without one is a defect.
    """
    import json

    def unmarked_in(node: object, name: str) -> list[str]:
        if isinstance(node, dict):
            found = [item for value in node.values() for item in unmarked_in(value, name)]
            carries_pair = "prediction" in node or "reference" in node
            redacted = any(isinstance(v, str) and PLACEHOLDER in v for v in node.values())
            if carries_pair and redacted and not node.get("identities_redacted"):
                found.append(f"{name}:{node.get('id', '?')}")
            return found
        if isinstance(node, list):
            return [item for value in node for item in unmarked_in(value, name)]
        return []

    unmarked: list[str] = []
    for path in sorted(RESULTS.rglob("*.json")):
        text = path.read_text()
        if PLACEHOLDER in text:
            unmarked += unmarked_in(json.loads(text), path.name)
    assert not unmarked, f"redacted records missing `identities_redacted`: {unmarked}"
