"""How each organization's corpus is filtered, step by step, from its build manifest.

Reads each organization's frozen corpus (`<root>/<org>/manifest.json` and its raw snapshots) and
writes what every rule removed, in the order the pipeline applies them: merged changes fetched;
inline comments dropped one by one (on a non-code file, the change author's own, a bot's, an
acknowledgement, with no line anchor, with no later patch set, outside any changed hunk, or lost
to a fetch error); hunks dropped as ill-posed pairs; examples dropped at refinement (the
reviewer's own suggested edit or applied fix, an acknowledgement, a bot's template); duplicates
removed; and the examples left in each window. The test window is sealed and is not counted.

`--docs` rewrites the table between the markers in the dataset page from the artifact, and the
test suite fails if the page and the artifact differ.

    uv run --no-sync --no-active python scripts/dataset_funnel.py \\
        --root R/corpus-v3 --org openstack --org wikimedia \\
        --out datasets/results/dataset-funnel.json --docs docs/dataset.md
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from sphragis.corpus.windows import WINDOWS
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True, help="the frozen corpus root")
parser.add_argument("--org", action="append", required=True)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--docs", type=Path, help="the page whose funnel table is regenerated")

#: The windows counted; the test window is sealed.
COUNTED = ("pilot", "train", "dev")

#: Each step's rows: (label, the counters it sums, where they are kept in the manifest).
STEPS: tuple[tuple[str, tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    (
        "Inline comments dropped",
        (
            ("on a file that is not code", ("metadata_file",)),
            ("written by the change's author", ("author_comment",)),
            ("from an account the host tags as a bot", ("service_user",)),
            ("an acknowledgement (Done, LGTM, Acknowledged)", ("acknowledgement",)),
            ("anchored to no line", ("no_line_anchor",)),
            ("on the last patch set, with no revision after it", ("no_successor",)),
            ("outside any hunk the next patch set changed", ("no_anchored_hunk",)),
            ("lost to a fetch error", ("comment_error", "diff_error")),
        ),
    ),
    (
        "Hunks dropped as ill-posed",
        (
            (
                "nothing before or after the edit",
                ("ill_posed_empty_before", "ill_posed_empty_after"),
            ),
            (
                "grown or shrunk past the size rules",
                ("ill_posed_expansion", "ill_posed_contraction"),
            ),
        ),
    ),
    (
        "Comments removed at refinement",
        (
            ("a bot's template, matched to the bot's own source", ("automated_comment",)),
            ("an acknowledgement inside a kept example", ("acknowledgement_comment",)),
        ),
    ),
    (
        "Examples dropped at refinement",
        (
            (
                "target written by the reviewer (suggested edit, applied fix)",
                ("suggested_edit", "applied_fix"),
            ),
            ("only acknowledgements left", ("acknowledgement_only",)),
            ("only a bot's template left", ("automated_only",)),
            ("revision not a rework of the change", ("not_rework_successor",)),
            ("revision of unknown kind", ("successor_kind_unknown",)),
            ("an address left in the text", ("address_residue",)),
        ),
    ),
    (
        "Examples removed as duplicates",
        (
            ("exact copies", ("exact",)),
            ("near duplicates (MinHash)", ("near_duplicate",)),
            ("shared boilerplate", ("boilerplate",)),
            ("one change in two places", ("shared_change_id",)),
        ),
    ),
)


def merged_changes(root: Path, org: str) -> int:
    """Merged changes fetched in the counted windows' months, after the creation cutoff."""
    first, last = WINDOWS[COUNTED[0]][0][:7], WINDOWS[COUNTED[-1]][1][:7]
    total = 0
    for snapshot in sorted((root / org / "raw").glob("*.ndjson.gz")):
        month = snapshot.name.removesuffix(".ndjson.gz")
        if first <= month < last:
            with gzip.open(snapshot, "rt") as lines:
                total += sum(1 for line in lines if line.strip())
    return total


def funnel(root: Path, org: str) -> dict:
    manifest = json.loads((root / org / "manifest.json").read_text())
    stats = manifest["stats"]
    counters = {**stats["built_drops"], **stats["refine_counts"], **stats["deduped"]}
    if manifest["counts"].get("test"):
        raise ValueError(f"{org}'s manifest counts test-window examples; the seal is broken")
    return {
        "rules": stats["rules"],
        "merged_changes": merged_changes(root, org),
        "steps": [
            {
                "step": step,
                "rows": [
                    {"reason": label, "count": sum(counters.get(k, 0) for k in keys)}
                    for label, keys in rows
                ],
            }
            for step, rows in STEPS
        ],
        "windows": {window: manifest["counts"][window] for window in COUNTED},
        "examples": sum(manifest["counts"][window] for window in COUNTED),
    }


#: Each organization's name as its project writes it.
DISPLAY = {"openstack": "OpenStack", "wikimedia": "Wikimedia", "qt": "Qt", "chromium": "Chromium"}

BEGIN, END = "<!-- funnel:begin (scripts/dataset_funnel.py) -->", "<!-- funnel:end -->"


def table(artifact: dict) -> str:
    """The funnel as one Markdown table, a column per organization."""
    orgs = list(artifact["organizations"])
    by = artifact["organizations"]
    cell = lambda n: f"{n:,}"  # noqa: E731
    lines = [
        "| | " + " | ".join(DISPLAY.get(org, org) for org in orgs) + " |",
        "|---|" + "---:|" * len(orgs),
        "| **Merged changes fetched** | "
        + " | ".join(cell(by[o]["merged_changes"]) for o in orgs)
        + " |",
    ]
    for index, step in enumerate(artifact["organizations"][orgs[0]]["steps"]):
        lines.append(f"| **{step['step']}** |" + " |" * len(orgs))
        for row_index, row in enumerate(step["rows"]):
            counts = [by[o]["steps"][index]["rows"][row_index]["count"] for o in orgs]
            lines.append(f"| {row['reason']} | " + " | ".join(cell(n) for n in counts) + " |")
    for window in COUNTED:
        lines.append(
            f"| **Examples kept, {window} window** | "
            + " | ".join(cell(by[o]["windows"][window]) for o in orgs)
            + " |"
        )
    lines.append(
        "| **Examples kept in all** | " + " | ".join(cell(by[o]["examples"]) for o in orgs) + " |"
    )
    return "\n".join(lines)


def with_table(page: str, artifact: dict) -> str:
    """The page with the text between the funnel markers replaced by the artifact's table."""
    if page.count(BEGIN) != 1 or page.count(END) != 1:
        raise ValueError("the page needs exactly one pair of funnel markers")
    head, rest = page.split(BEGIN)
    _, tail = rest.split(END)
    return f"{head}{BEGIN}\n{table(artifact)}\n{END}{tail}"


def main() -> None:
    args = parser.parse_args()
    try:
        organizations = {org: funnel(args.root, org) for org in args.org}
    except ValueError as error:
        raise SystemExit(str(error)) from error
    artifact = {
        "provenance": provenance_header(),
        "root": str(args.root),
        "windows": {window: list(WINDOWS[window]) for window in COUNTED},
        "organizations": organizations,
    }
    args.out.write_text(json.dumps(artifact, indent=2) + "\n")
    print(f"wrote {args.out}")
    if args.docs:
        args.docs.write_text(with_table(args.docs.read_text(), artifact))
        print(f"rewrote the funnel table in {args.docs}")


if __name__ == "__main__":
    main()
