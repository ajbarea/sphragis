"""Nothing this repository publishes discloses a third party's email address.

The corpus scrub pseudonymises account objects and comment text and deliberately leaves
the diff payload alone, because rewriting anything in the source that looks like an
address would corrupt the code the study measures. The consequence only shows up on the
far side of the pipeline: an address written inside a config file, a Debian control file
or a DNS record rides through the reference text and the model's predictions into
`datasets/results/`, and one rode further, into an example's id, because an OpenStack
candidacy file is named after the candidate's address.

Twenty-four reached the published artifacts that way. The scrub cannot be the thing that
catches them without changing what the corpus measures, so the check belongs here, on what
gets committed, and it covers the whole tree rather than one directory: the first version
of this guard watched `datasets/results/` alone, and three contributors' names, addresses,
usernames and account ids sat untouched in an orphaned test fixture the whole time.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from sphragis.corpus.redact import PLACEHOLDER, addresses_in, third_party_addresses

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "datasets" / "results"

#: Binary and generated files a text scan cannot read usefully.
_SKIP_SUFFIXES = {".png", ".jpg", ".pdf", ".whl", ".gz", ".lock"}

#: Python sources are exempt, and only Python sources. Third-party identities enter this
#: repository through mined data, never through hand-written code, while the detector and
#: its tests have to contain specimen addresses to be testable at all. Every other tracked
#: file is in scope, which is what the mined data is: `datasets/`, `tests/fixtures/`,
#: `docs/`, and the prose at the root.
_SPECIMEN_SUFFIX = ".py"


def _tracked_files() -> list[Path]:
    """Every committed file, because publication is a property of the commit.

    Read from git rather than walked, so the scan covers exactly what a reader of the
    public repository can fetch, and nothing from an untracked working tree.
    """
    listing = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        capture_output=True,
        check=True,
    ).stdout.decode()
    return [
        ROOT / name
        for name in listing.split("\0")
        if name
        and Path(name).suffix not in _SKIP_SUFFIXES
        and Path(name).suffix != _SPECIMEN_SUFFIX
        and (ROOT / name).is_file()
    ]


def test_no_committed_file_discloses_a_third_party_address() -> None:
    tracked = _tracked_files()
    # A guard that scanned nothing passes for the wrong reason. This is what separates
    # "clean" from "the path moved and the glob quietly returned empty".
    assert len(tracked) > 100, (
        f"only {len(tracked)} files scanned; the scan is not finding the tree"
    )
    # The data paths are the ones that carry mined identities; naming them keeps a
    # reorganisation from quietly moving them out from under the scan.
    scanned = {path.relative_to(ROOT).parts[0] for path in tracked}
    assert {"datasets", "tests", "docs"} <= scanned, f"data paths missing from the scan: {scanned}"

    disclosed = {
        path.relative_to(ROOT).as_posix(): sorted(found)
        for path in tracked
        if (found := addresses_in(path))
    }
    assert not disclosed, (
        "third-party addresses in committed files; for artifacts run "
        f"`python scripts/redact_identities.py --write`:\n{json.dumps(disclosed, indent=2)}"
    )


def test_the_results_directory_is_scanned_whole() -> None:
    """Every file type under `datasets/results/`, not only `*.json`.

    The directory holds `.npz` and `.txt` beside the JSON, and the first version of this
    guard globbed `*.json`, so a planted address in `power-rq1-windows.txt` passed.

    Committed files only, like the scan above. Walking the directory instead made this
    test fail on any working tree holding a fresh job result or a gitignored corpus slice,
    which is the normal state of this repository: green in CI, red for whoever just ran a
    job. A new result is caught when it is staged, because `git ls-files` reads the index.
    """
    scanned = [path for path in _tracked_files() if RESULTS in path.parents]
    suffixes = {path.suffix for path in scanned}
    assert len(scanned) > 100, f"only {len(scanned)} committed artifacts under {RESULTS}"
    assert {".json", ".npz", ".txt"} <= suffixes, f"expected more than JSON here, saw {suffixes}"
    assert not {path.name for path in scanned if addresses_in(path)}


@pytest.mark.parametrize(
    "address",
    [
        "someone@windriver.com",
        "a.person@zte.com.cn",
        "libosvar@redhat.com",
        # Substring exclusions used to drop every one of these. Each is a person.
        "bob@notexample.org",
        "eve@counterexample.com",
        "spy@rit.edu.cn",
        "noreply.jane@redhat.com",
        "alice@localhostings.com",
        "u@mark.com",
        "tel@example.org.attacker.com",
        # GitHub's privacy address still names the account that owns it.
        "lazekteam@users.noreply.github.com",
    ],
)
def test_the_detector_fires_on_a_person(address: str) -> None:
    """A test asserting an absence has to show it can detect a presence."""
    assert third_party_addresses(f"contact: {address}") == {address}


@pytest.mark.parametrize(
    "benign",
    [
        "alice@example.org",  # RFC 2606 documentation domains
        "bob@example.com",
        "my-project-mailing-list@lists.example.org",  # and their subdomains
        "your-email@example.com",
        "ajb6289@rit.edu",  # the maintainer, already on every job script
        "noreply@github.com",  # a role, not a person
        "n@app.route",  # a decorator, caught by the address shape
        "n@mock.patch",
    ],
)
def test_the_detector_leaves_the_harmless_alone(benign: str) -> None:
    """A detector that flags the fixtures gets switched off, which is the real failure."""
    assert third_party_addresses(benign) == set()


def test_the_placeholder_is_not_itself_an_address() -> None:
    """Redacting twice must not find something to redact the second time."""
    assert third_party_addresses(f"email: {PLACEHOLDER}") == set()
    assert "@" not in PLACEHOLDER


def test_redacted_records_say_so() -> None:
    """Where the text was redacted, `edit_similarity` was measured on the original.

    It is left at the measured value rather than recomputed, because the repository's
    stated invariant is that a re-run reproduces the artifact. The flag is what keeps that
    from reading as drift, so a redacted mapping without one is a defect.
    """

    def unmarked_in(node: object, name: str) -> list[str]:
        if isinstance(node, dict):
            found = [item for value in node.values() for item in unmarked_in(value, name)]
            redacted = any(isinstance(v, str) and PLACEHOLDER in v for v in node.values())
            if redacted and not node.get("identities_redacted"):
                found.append(f"{name}:{node.get('id', '?')}")
            return found
        if isinstance(node, list):
            return [item for value in node for item in unmarked_in(value, name)]
        return []

    unmarked: list[str] = []
    carrying = 0
    for path in sorted(
        path for path in _tracked_files() if path.suffix == ".json" and RESULTS in path.parents
    ):
        text = path.read_text()
        if PLACEHOLDER not in text:
            continue
        carrying += 1
        unmarked += unmarked_in(json.loads(text), path.name)
    assert carrying, "no artifact carries a redaction, so this test asserts nothing"
    assert not unmarked, f"redacted records missing `identities_redacted`: {unmarked}"
