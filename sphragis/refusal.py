"""A library refusal as a script's exit.

The library raises a plain `ValueError` when its input breaks one of the study's rules; a script
exits with its message. A subclass (a file that does not decode, say) is not a refusal and keeps
its traceback.
"""

from __future__ import annotations

import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def refusals(prefix: str = "", *, where: bool = False) -> Iterator[None]:
    """Exit on a refusal raised inside, `prefix` before its message; `where` adds the file and
    line it was raised at, for a catch around a whole job."""
    try:
        yield
    except ValueError as error:
        if type(error) is not ValueError:
            raise
        at = ""
        if where:
            frame = traceback.extract_tb(error.__traceback__)[-1]
            at = f" (at {Path(frame.filename).name}:{frame.lineno})"
        raise SystemExit(f"{prefix}{error}{at}") from error
