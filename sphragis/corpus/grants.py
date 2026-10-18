"""The written grants this machine holds, read from a machine-local file.

A host that opens only by written grant (Qt's and Chromium's review hosts over REST, AOSP's git
host for its release tag) granted one collector, at its stated address and with its User-Agent,
not every clone of this repository. The REST route and the git route
(`notedb.refused_by_permission`) ask `holds_grant` before contacting such a host.
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
    if os.environ.get("SPHRAGIS_GRANTS") == "":
        raise SystemExit("refusing every written grant: SPHRAGIS_GRANTS is set but empty")
    path = grants_file()
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return False
    except (OSError, UnicodeDecodeError) as error:
        raise SystemExit(f"refusing every written grant: {path} cannot be read ({error})") from None
    return host.lower() in {line.split("#", 1)[0].strip().lower() for line in lines}
