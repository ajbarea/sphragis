"""A library refusal as a script's exit.

The library raises a plain `ValueError` when its input breaks one of the study's rules; a script
exits with its message. A subclass (a file that does not decode, say), or one raised by code
outside the study's (torch or transformers during generation), is not a refusal and keeps its
traceback.
"""

from __future__ import annotations

import sys
import sysconfig
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# The study's code: any file of the checkout but an interpreter's (a virtual environment sits in
# the checkout on the clusters, `.venv-<machine>`).
_ROOT = Path(__file__).resolve().parents[1]
_INTERPRETER = tuple(
    path
    for path in {
        Path(p).resolve()
        for p in (
            sys.prefix,
            sys.base_prefix,
            *(sysconfig.get_paths()[k] for k in ("stdlib", "purelib", "platlib")),
        )
    }
    # An interpreter path holding the whole checkout would exclude the study itself.
    if not _ROOT.is_relative_to(path)
)


def _studys(filename: str) -> bool:
    """Whether a frame's file is the study's: a real file of the checkout, not an interpreter's
    (its venv, its standard library) nor a frozen module's or exec'd code's pseudo-name
    (`<frozen posixpath>`)."""
    if filename.startswith("<"):
        return False
    path = Path(filename).resolve()
    return path.is_relative_to(_ROOT) and not any(path.is_relative_to(p) for p in _INTERPRETER)


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
