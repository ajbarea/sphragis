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
        --source 'tigris:$SPHRAGIS_DATA/corpus-v3' \\
        --out datasets/results/dataset-funnel.json --docs docs/dataset.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sphragis.corpus.storage import read_snapshot_record, refused_snapshot, snapshot_path
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

#: Each step, the stats section its counters are kept in, and its rows: (label, unit, counters).
#: In the order the code applies them: `build_from_change`, then `refine`, then dedup.
STEPS: tuple[tuple[str, str, tuple[tuple[str, str, tuple[str, ...]], ...]], ...] = (
    (
        "Changes the build did not read",
        "built_drops",
        (
            ("their listing records no comment", "changes", ("uncommented_by_count",)),
            ("past a grant's monthly read cap", "changes", ("over_read_cap",)),
        ),
    ),
    (
        "Changes lost before any comment was read",
        "built_drops",
        (("their comments could not be fetched", "changes", ("comment_error",)),),
    ),
    (
        "Comments dropped",
        "built_drops",
        (
            ("on the commit message or the patch set as a whole", "comments", ("metadata_file",)),
            ("written by the change's author", "comments", ("author_comment",)),
            ("from an account the host tags as a bot", "comments", ("service_user",)),
            (
                "an acknowledgement (Done, LGTM, thanks, ok and the like)",
                "comments",
                ("acknowledgement",),
            ),
            ("anchored to no line", "comments", ("no_line_anchor",)),
            ("on the last patch set, with no revision after it", "comments", ("no_successor",)),
            ("its diff could not be fetched", "comments", ("diff_error",)),
            ("outside any hunk the next patch set changed", "comments", ("no_anchored_hunk",)),
        ),
    ),
    (
        "Hunks dropped as ill-posed",
        "built_drops",
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
        "Examples dropped at refinement",
        "refine_counts",
        (
            ("next patch set only a rebase, not a rework", "examples", ("not_rework_successor",)),
            (
                "target written by the reviewer (suggested edit, applied fix)",
                "examples",
                ("suggested_edit", "applied_fix"),
            ),
        ),
    ),
    (
        "Comments removed at refinement",
        "refine_counts",
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
        "Examples left with no comment",
        "refine_counts",
        (
            ("only acknowledgements were left", "examples", ("acknowledgement_only",)),
            ("only a bot's templates were left", "examples", ("automated_only",)),
        ),
    ),
    (
        "Examples removed as duplicates",
        "deduped",
        (
            ("exact copies", "examples", ("exact",)),
            ("near duplicates (MinHash)", "examples", ("near_duplicate",)),
            ("shared boilerplate", "examples", ("boilerplate",)),
            ("one change in two places", "examples", ("shared_change_id",)),
        ),
    ),
)

#: Kept, not dropped: examples whose next patch set's kind was never recorded, so the rebase
#: check could not run on them.
KEPT_UNCHECKED = (
    "Examples kept without the rebase check",
    "refine_counts",
    "successor_kind_unknown",
)


def merged_changes(root: Path, org: str) -> tuple[int, list[str]]:
    """Merged changes in the months the build read (those with a drops record), and the months.

    Each count is the row count the snapshot's record holds, read once `refused_snapshot` has
    passed the snapshot as a finished write.
    """
    months = sorted(
        p.name.removesuffix(".drops.json") for p in (root / org / "examples").glob("*.drops.json")
    )
    if not months:
        raise ValueError(f"{org} has no built month under {root}")
    total = 0
    for month in months:
        path = snapshot_path(root, org, month)
        refused = refused_snapshot(path)
        record = read_snapshot_record(path)
        if refused is not None or record is None or "rows" not in record:
            raise ValueError(f"{path}: {refused or 'its record holds no row count'}")
        total += int(record["rows"])
    return total, months


#: Counters only a build mode writes (`build --fewer-requests`, `--read-cap`): absent, the mode
#: was off and skipped nothing, so they read as zero rather than as not counted.
MODE_COUNTERS = ("uncommented_by_count", "over_read_cap")


def unplaced_drops(stats: dict) -> list[str]:
    """Build counters no row reads: a new one would otherwise vanish from the funnel unseen."""
    placed = {
        k
        for _, section, rows in STEPS
        if section == "built_drops"
        for *_, keys in rows
        for k in keys
    }
    return sorted(set(stats.get("built_drops", {})) - placed)


def _count(stats: dict, section: str, keys: tuple[str, ...]) -> int | None:
    """The summed counters; None when the build recorded none of them, refused when only some."""
    if section not in stats:
        raise ValueError(f"the manifest's stats hold no {section}")
    if all(k in MODE_COUNTERS for k in keys) and not any(k in stats[section] for k in keys):
        return 0
    found = [k for k in keys if k in stats[section]]
    if found and len(found) != len(keys):
        raise ValueError(f"{section} records {found} of {list(keys)}: a partial sum is no count")
    return sum(stats[section][k] for k in keys) if found else None


def funnel(root: Path, org: str) -> dict:
    path = root / org / "manifest.json"
    manifest = json.loads(path.read_text())
    stats = manifest["stats"]
    if manifest["counts"].get("test"):
        raise ValueError(f"{org}'s manifest counts test-window examples; the seal is broken")
    if unplaced := unplaced_drops(stats):
        raise ValueError(f"{org}'s build counts {unplaced}, which no funnel row reads")
    changes, months = merged_changes(root, org)
    label, section, key = KEPT_UNCHECKED
    return {
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "rules": stats["rules"],
        "months": months,
        "merged_changes": changes,
        "steps": [
            {
                "step": step,
                "rows": [
                    {"reason": reason, "unit": unit, "count": _count(stats, section, keys)}
                    for reason, unit, keys in rows
                ],
            }
            for step, section, rows in STEPS
        ],
        "kept_unchecked": {"label": label, "count": _count(stats, section, (key,))},
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
        f"| {by[orgs[0]]['kept_unchecked']['label']} | examples | "
        + " | ".join(cell(by[o]["kept_unchecked"]["count"]) for o in orgs)
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
    if page.count(BEGIN) != 1 or page.count(END) != 1 or page.index(BEGIN) > page.index(END):
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
