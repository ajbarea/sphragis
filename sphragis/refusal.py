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

# The study's code: the package and the scripts beside it.
_STUDY = Path(__file__).resolve().parents[1]


@contextmanager
def refusals(prefix: str = "", *, where: bool = False) -> Iterator[None]:
    """Exit on a refusal raised inside, `prefix` before its message; `where` adds the file and
    line it was raised at, for a catch around a whole job."""
    try:
        yield
    except ValueError as error:
        frame = traceback.extract_tb(error.__traceback__)[-1]
        raised = Path(frame.filename).resolve()
        if (
            type(error) is not ValueError
            or not raised.is_relative_to(_STUDY)
            or ".venv" in raised.parts
        ):
            raise
        at = f" (at {raised.name}:{frame.lineno})" if where else ""
        raise SystemExit(f"{prefix}{error}{at}") from error
