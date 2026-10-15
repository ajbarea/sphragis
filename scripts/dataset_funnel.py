"""How each organization's corpus is filtered, step by step, from its build manifest.

Reads each organization's frozen corpus (`<root>/<org>/manifest.json` and its raw snapshots) and
writes what every rule removed, in the order the pipeline applies them: merged changes fetched in
the months the build read; comments dropped one by one (on Gerrit's commit-message and
patch-set pseudo-files, the change author's own, a bot account's, an acknowledgement, with no line
anchor, with no later patch set, outside any hunk the next patch set changed, or lost to a fetch
error); hunks dropped as ill-posed pairs; comments removed at refinement (a bot's template, an
acknowledgement) and examples dropped there (the reviewer's own suggested edit or applied fix, or
nothing left but acknowledgements or templates); duplicates removed; and the examples left in
each window. A counter the build did not record reads as not counted, never as zero. The test
window is sealed and is not counted.

`--docs` rewrites the table between the markers in the dataset page from the artifact, and the
test suite fails if the page and the artifact differ.

    uv run --no-sync --no-active python scripts/dataset_funnel.py \\
        --root R/corpus-v3 --org openstack --org wikimedia \\
        --out datasets/results/dataset-funnel.json --docs docs/dataset.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sphragis.corpus.storage import iter_snapshot
from sphragis.corpus.windows import WINDOWS
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True, help="the frozen corpus root")
parser.add_argument("--org", action="append", required=True)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--docs", type=Path, help="the page whose funnel table is regenerated")
parser.add_argument(
    "--source",
    required=True,
    help="where the frozen corpora live, recorded in place of a local copy's path, e.g. "
    "tigris:$SPHRAGIS_DATA/corpus-v3",
)

#: The windows counted; the test window is sealed.
COUNTED = ("pilot", "train", "dev")

#: Each step's rows: (label, unit, the counters it sums), each counter read from `SOURCE[step]`.
STEPS: tuple[tuple[str, tuple[tuple[str, str, tuple[str, ...]], ...]], ...] = (
    (
        "Changes lost before any comment was read",
        (("their comments could not be fetched", "changes", ("comment_error",)),),
    ),
    (
        "Comments dropped",
        (
            ("on the commit message or the patch set as a whole", "comments", ("metadata_file",)),
            ("written by the change's author", "comments", ("author_comment",)),
            ("from an account the host tags as a bot", "comments", ("service_user",)),
            ("an acknowledgement (Done, LGTM, Acknowledged)", "comments", ("acknowledgement",)),
            ("anchored to no line", "comments", ("no_line_anchor",)),
            ("on the last patch set, with no revision after it", "comments", ("no_successor",)),
            ("outside any hunk the next patch set changed", "comments", ("no_anchored_hunk",)),
            ("its diff could not be fetched", "comments", ("diff_error",)),
        ),
    ),
    (
        "Hunks dropped as ill-posed",
        (
            (
                "nothing before or after the edit",
                "hunks",
                ("ill_posed_empty_before", "ill_posed_empty_after"),
            ),
            (
                "grown or shrunk past the size rules",
                "hunks",
                ("ill_posed_expansion", "ill_posed_contraction"),
            ),
        ),
    ),
    (
        "Comments removed at refinement",
        (
            (
                "a bot's template, matched to the bot's own source",
                "comments",
                ("automated_comment",),
            ),
            ("an acknowledgement", "comments", ("acknowledgement_comment",)),
        ),
    ),
    (
        "Examples dropped at refinement",
        (
            (
                "target written by the reviewer (suggested edit, applied fix)",
                "examples",
                ("suggested_edit", "applied_fix"),
            ),
            ("nothing left but acknowledgements", "examples", ("acknowledgement_only",)),
            ("nothing left but a bot's templates", "examples", ("automated_only",)),
            ("revision not a rework of the change", "examples", ("not_rework_successor",)),
        ),
    ),
    (
        "Examples removed as duplicates",
        (
            ("exact copies", "examples", ("exact",)),
            ("near duplicates (MinHash)", "examples", ("near_duplicate",)),
            ("shared boilerplate", "examples", ("boilerplate",)),
            ("one change in two places", "examples", ("shared_change_id",)),
        ),
    ),
)

#: Where in the manifest's stats each step's counters are kept.
SOURCE = {
    "Changes lost before any comment was read": "built_drops",
    "Comments dropped": "built_drops",
    "Hunks dropped as ill-posed": "built_drops",
    "Comments removed at refinement": "refine_counts",
    "Examples dropped at refinement": "refine_counts",
    "Examples removed as duplicates": "deduped",
}


def merged_changes(root: Path, org: str) -> tuple[int, list[str]]:
    """Merged changes in the months the build read (those with a drops record), and the months.

    Read through `iter_snapshot`, which refuses a snapshot whose write did not finish.
    """
    months = sorted(
        p.name.removesuffix(".drops.json") for p in (root / org / "examples").glob("*.drops.json")
    )
    if not months:
        raise ValueError(f"{org} has no built month under {root}")
    total = 0
    for month in months:
        total += sum(1 for _ in iter_snapshot(root / org / "raw" / f"{month}.ndjson.gz"))
    return total, months


def _count(stats: dict, step: str, keys: tuple[str, ...]) -> int | None:
    """The summed counters, or None when the build recorded none of them."""
    found = [stats[SOURCE[step]][k] for k in keys if k in stats[SOURCE[step]]]
    return sum(found) if found else None


def funnel(root: Path, org: str) -> dict:
    path = root / org / "manifest.json"
    manifest = json.loads(path.read_text())
    stats = manifest["stats"]
    if manifest["counts"].get("test"):
        raise ValueError(f"{org}'s manifest counts test-window examples; the seal is broken")
    changes, months = merged_changes(root, org)
    return {
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "build_rules": stats["build_rules"],
        "rules": stats["rules"],
        "months": months,
        "merged_changes": changes,
        "steps": [
            {
                "step": step,
                "rows": [
                    {"reason": label, "unit": unit, "count": _count(stats, step, keys)}
                    for label, unit, keys in rows
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
    """The funnel as one Markdown table, a column per organization, refused if their rows differ."""
    by = artifact["organizations"]
    orgs = list(by)
    layout = {
        o: [(st["step"], [r["reason"] for r in st["rows"]]) for st in by[o]["steps"]] for o in orgs
    }
    if any(layout[o] != layout[orgs[0]] for o in orgs):
        raise ValueError("the organizations' funnels do not share one layout of steps and rows")
    cell = lambda n: "not counted" if n is None else f"{n:,}"  # noqa: E731
    lines = [
        "| | Unit | " + " | ".join(DISPLAY.get(org, org) for org in orgs) + " |",
        "|---|---|" + "---:|" * len(orgs),
        "| **Merged changes fetched** | changes | "
        + " | ".join(cell(by[o]["merged_changes"]) for o in orgs)
        + " |",
    ]
    for index, step in enumerate(by[orgs[0]]["steps"]):
        lines.append(f"| **{step['step']}** | |" + " |" * len(orgs))
        for row_index, row in enumerate(step["rows"]):
            counts = [by[o]["steps"][index]["rows"][row_index]["count"] for o in orgs]
            lines.append(
                f"| {row['reason']} | {row['unit']} | " + " | ".join(cell(n) for n in counts) + " |"
            )
    for window in COUNTED:
        lines.append(
            f"| **Examples kept, {window} window** | examples | "
            + " | ".join(cell(by[o]["windows"][window]) for o in orgs)
            + " |"
        )
    lines.append(
        "| **Examples kept in all** | examples | "
        + " | ".join(cell(by[o]["examples"]) for o in orgs)
        + " |"
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
        artifact = {
            "provenance": provenance_header(),
            "source": args.source,
            "windows": {window: list(WINDOWS[window]) for window in COUNTED},
            "organizations": organizations,
        }
        page = with_table(args.docs.read_text(), artifact) if args.docs else None
    except ValueError as error:
        raise SystemExit(str(error)) from error
    args.out.write_text(json.dumps(artifact, indent=2) + "\n")
    print(f"wrote {args.out}")
    if args.docs and page is not None:
        args.docs.write_text(page)
        print(f"rewrote the funnel table in {args.docs}")


if __name__ == "__main__":
    main()
