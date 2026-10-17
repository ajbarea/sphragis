"""The written grants this machine holds, read from a machine-local file.

A host that opens only by written grant (Qt's and Chromium's review hosts over REST) granted one
collector, at its stated address and with its User-Agent, not every clone of this repository.
Every route such a grant opens asks `holds_grant` before contacting the host.
"""

from __future__ import annotations

import os
from pathlib import Path


def grants_file() -> Path:
    """The machine-local list of written grants: one host a line, `#` starts a comment.
    `SPHRAGIS_GRANTS`, when set, names another path; set but empty, it names no readable file."""
    if "SPHRAGIS_GRANTS" in os.environ:
        return Path(os.environ["SPHRAGIS_GRANTS"])
    return (
        Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "sphragis" / "grants"
    )


def holds_grant(host: str) -> bool:
    """Whether `grants_file()` lists `host`, for any route a written grant opens. A file that
    exists but cannot be read stops the process rather than reading as no grants: `build` counts
    an exception per change, so it would record every refusal as an empty month."""
    path = grants_file()
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return False
    except (OSError, UnicodeDecodeError) as error:
        raise SystemExit(f"refusing every written grant: {path} cannot be read ({error})") from None
    return host.lower() in {line.split("#", 1)[0].strip().lower() for line in lines}
