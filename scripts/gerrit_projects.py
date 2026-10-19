"""The code projects a Gerrit host lists, the projects a grant scope over all of them names.

One request to the host's project list (`/projects/?type=CODE`: active and read-only code
projects, hidden ones aside), through the host's own REST permission and pace, before any change
is listed. Reads no review data. Its artifact is what `cli.fuchsia_projects` reads into Fuchsia's
grant scope (research log, 2026-10-19).

    uv run --no-sync --no-active python scripts/gerrit_projects.py --org fuchsia \\
        --out datasets/results/fuchsia-projects.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from sphragis.corpus.cli import GERRIT, host_of, http_transport
from sphragis.corpus.gerrit import _get, parse_response
from sphragis.provenance import provenance_header

QUERY = "/projects/?type=CODE"

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--org", required=True, choices=sorted(GERRIT))
parser.add_argument("--out", type=Path, required=True)


def code_projects(listed: dict[str, dict]) -> list[str]:
    """The listed projects' names, sorted, the host's own meta projects aside."""
    return sorted(
        name
        for name, info in listed.items()
        if not name.startswith(("All-", "refs/")) and info.get("state", "ACTIVE") != "HIDDEN"
    )


def main() -> None:
    args = parser.parse_args()
    url = GERRIT[args.org].rstrip("/") + QUERY
    listed = parse_response(_get(url, http_transport(), time.sleep, [0]))
    projects = code_projects(listed)
    report = {
        "org": args.org,
        "host": host_of(args.org),
        "query": QUERY,
        "listed": len(listed),
        "states": {
            state: sum(1 for info in listed.values() if info.get("state", "ACTIVE") == state)
            for state in sorted({info.get("state", "ACTIVE") for info in listed.values()})
        },
        "projects": projects,
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{args.org}: {len(projects)} code projects of {len(listed)} listed; wrote {args.out}")


if __name__ == "__main__":
    main()
