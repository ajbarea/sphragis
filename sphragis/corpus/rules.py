"""Digests of the code that decides what an example is, so a changed rule cannot go unnoticed.

Two stages apply rules, and each gets its own digest, because re-running one does not re-run
the other:

- `BUILD_RULES`, what `build` applies while it turns a change into examples. Every built month
  records the digest it was built under, and a month built under other rules is stale until it is
  rebuilt: refining it again would certify the old build's output as current.
- `RULES_VERSION`, what `refine` applies to built examples (the data audit's label rules). A
  change to these is a re-refine, never a refetch, which is why the build applies none of them.

Hashing the rule code itself means a changed rule is a changed version without anyone
remembering to bump one; a comment edit also changes it, which errs toward redoing the work.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from pathlib import Path

from sphragis.corpus.automated import registry_digest

_CORPUS = Path(__file__).resolve().parent

# What the build applies needs the network or the comment's author: fetching and scrubbing,
# hunks, the author, service-account and acknowledgement filters, well-posedness.
BUILD_SOURCES = ("build.py", "examples.py", "wellposed.py", "scrub.py", "fetchers.py")
# What refine applies to built examples: the bot templates, the acknowledgement list, successor
# kinds, the residue sweep.
REFINE_SOURCES = ("refine.py", "automated/__init__.py", "build.py", "examples.py")


def rules_version(sources: Mapping[str, str], registry: str) -> str:
    """A digest of rule code by file name, and of the bot registries."""
    digest = hashlib.sha256()
    for name in sorted(sources):
        digest.update(name.encode() + b"\0" + sources[name].encode() + b"\0")
    digest.update(registry.encode())
    return digest.hexdigest()[:16]


def _digest(names: Iterable[str], registry: str) -> str:
    return rules_version({name: (_CORPUS / name).read_text() for name in names}, registry)


BUILD_RULES = _digest(BUILD_SOURCES, "")
RULES_VERSION = _digest(REFINE_SOURCES, registry_digest())
