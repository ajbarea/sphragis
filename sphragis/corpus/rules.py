"""Digests of the code that decides what an example is, so a changed rule cannot go unnoticed.

Two stages apply rules, and each gets its own digest, because re-running one does not re-run
the other:

- `BUILD_RULES`, what `build` applies while it turns a change into examples (the author and
  acknowledgement filters, the successor check, the well-posedness filter, the scrub, the bot
  registry). Every built month records the digest it was built under, and a month built under
  other rules is stale until it is rebuilt: refining it again would stamp the old build's output
  as current.
- `RULES_VERSION`, what `refine` applies to built examples (the data audit's label rules).

Hashing the rule code itself means a changed rule is a changed version without anyone
remembering to bump one; a comment edit also changes it, which errs toward redoing the work.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from pathlib import Path

from sphragis.corpus.automated import registry_digest

_CORPUS = Path(__file__).resolve().parent

BUILD_SOURCES = (
    "build.py",
    "examples.py",
    "wellposed.py",
    "scrub.py",
    "fetchers.py",
    "automated/__init__.py",
)
REFINE_SOURCES = ("refine.py", "build.py", "examples.py", "scrub.py", "automated/__init__.py")


def rules_version(sources: Mapping[str, str], registry: str) -> str:
    """A digest of rule code by file name, and of the bot registries."""
    digest = hashlib.sha256()
    for name in sorted(sources):
        digest.update(name.encode() + b"\0" + sources[name].encode() + b"\0")
    digest.update(registry.encode())
    return digest.hexdigest()[:16]


def _digest(names: Iterable[str]) -> str:
    return rules_version({name: (_CORPUS / name).read_text() for name in names}, registry_digest())


BUILD_RULES = _digest(BUILD_SOURCES)
RULES_VERSION = _digest(REFINE_SOURCES)
