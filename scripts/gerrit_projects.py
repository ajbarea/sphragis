"""The code projects a Gerrit host lists, for a grant whose scope is every one of them.

One request to the host's project list (`/projects/?type=CODE`: active and read-only code
projects), through the host's own REST permission and pace, before any change is listed. Reads no
review data. Only a host whose grant scope is defined by this list may be asked
(`GrantScope.lists_projects`), and the list is read once: an existing artifact is refused unless
`--overwrite`. Its artifact is what `cli.fuchsia_projects` reads into Fuchsia's grant scope
(research log, 2026-10-19).

    uv run --no-sync --no-active python scripts/gerrit_projects.py --org fuchsia \\
        --out datasets/results/fuchsia-projects.json
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sphragis.corpus.cli import GERRIT, PROJECT_LISTS, host_of, http_transport, rest_project_name
from sphragis.corpus.gerrit import _get, parse_response
from sphragis.durable import write_json_atomic
from sphragis.provenance import provenance_header

QUERY = "/projects/?type=CODE"
#: The host's own projects, which hold its access rules and accounts, not code.
META_PROJECTS = frozenset({"All-Projects", "All-Users"})

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--org", required=True, choices=sorted(PROJECT_LISTS))
parser.add_argument("--out", type=Path, help="the artifact; the scope's own by default")
parser.add_argument("--overwrite", action="store_true", help="replace an existing artifact")


def code_projects(listed: Any) -> list[str]:
    """The listed projects' names, sorted, the host's meta projects aside. Refused when the list
    is empty, truncated or not a mapping, or names a project a `project:` query could misread."""
    if not isinstance(listed, Mapping) or not listed:
        raise SystemExit(f"the project list is empty or not a mapping: {type(listed).__name__}")
    if not all(isinstance(info, Mapping) for info in listed.values()):
        raise SystemExit("the project list holds an entry that is not a project's record")
    if any(isinstance(info, Mapping) and info.get("_more_projects") for info in listed.values()):
        raise SystemExit("the project list is truncated (_more_projects); refusing a partial scope")
    unsafe = sorted(name for name in listed if not rest_project_name(name))
    if unsafe:
        raise SystemExit(f"project names a query could misread: {unsafe[:5]}")
    projects = sorted(name for name in listed if name not in META_PROJECTS)
    if not projects:
        raise SystemExit("the project list names only the host's own projects")
    return projects


def main() -> None:
    args = parser.parse_args()
    out = args.out or PROJECT_LISTS[args.org]
    if out.exists() and not args.overwrite:
        raise SystemExit(f"{out} exists: the list is read once; pass --overwrite to replace it")
    url = GERRIT[args.org].rstrip("/") + QUERY
    listed = parse_response(_get(url, http_transport(), time.sleep, [0]))
    projects = code_projects(listed)
    if out.exists() and not args.overwrite:
        raise SystemExit(f"{out} appeared while the list was read; refusing to replace it")
    states: dict[str, int] = {}
    for info in listed.values():
        state = str(info.get("state", "ACTIVE")) if isinstance(info, Mapping) else "ACTIVE"
        states[state] = states.get(state, 0) + 1
    write_json_atomic(
        out,
        {
            "org": args.org,
            "host": host_of(args.org),
            "query": QUERY,
            "listed": len(listed),
            "states": dict(sorted(states.items())),
            "projects": projects,
            "provenance": provenance_header(),
        },
    )
    print(f"{args.org}: {len(projects)} code projects of {len(listed)} listed; wrote {out}")


if __name__ == "__main__":
    main()
