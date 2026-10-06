"""A library refusal as a script's exit.

The library raises a plain `ValueError` when its input breaks one of the study's rules; a script
exits with its message. A subclass (a file that does not decode, say), or one raised by code
outside the study's (torch or transformers during generation), is not a refusal and keeps its
traceback.
"""

from __future__ import annotations

import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# The study's code: the package, its scripts and its tests; a virtual environment beside them in
# the checkout (`.venv-<machine>` on the clusters) is not.
_ROOT = Path(__file__).resolve().parents[1]
_STUDY = tuple(_ROOT / part for part in ("sphragis", "scripts", "tests"))


def _studys(filename: str) -> bool:
    """Whether a frame's file is the study's: a real file under its code, not a frozen module's
    or exec'd code's pseudo-name (`<frozen posixpath>`)."""
    if filename.startswith("<"):
        return False
    path = Path(filename).resolve()
    return any(path.is_relative_to(root) for root in _STUDY)


@contextmanager
def refusals(prefix: str = "", *, where: bool = False) -> Iterator[None]:
    """Exit on a refusal raised inside, `prefix` before its message; `where` adds the file and
    line it was raised at, for a catch around a whole job."""
    try:
        yield
    except ValueError as error:
        frame = traceback.extract_tb(error.__traceback__)[-1]
        if type(error) is not ValueError or not _studys(frame.filename):
            raise
        at = f" (at {Path(frame.filename).name}:{frame.lineno})" if where else ""
        raise SystemExit(f"{prefix}{error}{at}") from error
