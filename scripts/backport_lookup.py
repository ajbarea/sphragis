"""Each target's added lines looked up in its project's branches as they stood at the checkpoint.

The registered lookup (research log, 2026-10-20, "The backport lookup registered";
`sphragis.corpus.lookup`): every example of the given windows, deduplicated and split as the
runner reads them, against every branch of its project at its last commit before the evaluated
weights' upload. Each project is cloned once, bare, into `--clones`, under its host's claim and
2 seconds apart; a project with no repository at any registered source has no reference. The
output holds example ids, counts and η, never a line of code or review text. The test window
only once its seal is open.

    uv run --no-sync --no-active python scripts/backport_lookup.py \\
        --root ~/ajsoftworks/sphragis-data-local/reference --org openstack \\
        --clones ~/ajsoftworks/sphragis-data-local/lookup-clones \\
        --out datasets/results/backport-lookup-openstack.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from sphragis.corpus import lookup
from sphragis.corpus.cli import USER_AGENT
from sphragis.corpus.halves import TYPED_TARGET_MIN_CHARS
from sphragis.corpus.hosts import claim_host
from sphragis.corpus.load import refined_examples
from sphragis.corpus.pipeline import run_dedup, run_split
from sphragis.corpus.windows import WINDOWS
from sphragis.experiment.holdout import SEALED_WINDOWS
from sphragis.provenance import provenance_header

CLASSES = ("clean", "suspicious", "found", "no_eligible_line", "no_reference")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True, help="the corpus the partitions came from")
parser.add_argument("--org", required=True, choices=sorted(lookup.SOURCES))
parser.add_argument(
    "--window",
    action="append",
    choices=sorted(WINDOWS),
    help="a window to read, repeatable (default pilot, train and dev); never a sealed one",
)
parser.add_argument("--clones", type=Path, required=True, help="where bare clones are kept")
parser.add_argument("--out", type=Path, required=True)


def summarize(readings: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(r["class"] for r in readings)
    return {"examples": len(readings), **{name: counts.get(name, 0) for name in CLASSES}}


def read_windows(root: Path, org: str, windows: list[str]) -> dict[str, list[dict[str, Any]]]:
    """The examples of `windows`, deduplicated as one corpus and split as `holdout.window_split`
    reads them for the runner, with its refusals: a change straddling a boundary or outside every
    window, and any test-window row while the test window is sealed."""
    examples = refined_examples(root, org)
    if not examples:
        raise SystemExit(f"no refined examples under {root / org}")
    kept, _ = run_dedup(examples)
    split, straddling, unassigned = run_split(kept, WINDOWS)
    if straddling or unassigned:
        raise SystemExit(f"{org}: {len(straddling)} straddling and {len(unassigned)} unassigned")
    if split.get("test"):
        raise SystemExit(f"{org}: the corpus holds test-window examples; it is sealed")
    return {window: [dict(row) for row in split.get(window, [])] for window in windows}


def main() -> None:
    args = parser.parse_args()
    windows = args.window or ["pilot", "train", "dev"]
    sealed = sorted(set(windows) & set(SEALED_WINDOWS))
    if sealed:
        raise SystemExit(f"{sealed} sealed: read only once acceptance unseals it, as the runner")
    rows = read_windows(args.root, args.org, windows)
    projects = sorted({row["project"] for held in rows.values() for row in held})
    cloner = lookup.Cloner(args.clones, user_agent=USER_AGENT, claim=claim_host)
    by_example: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, Any]] = {}
    for number, project in enumerate(projects, 1):
        lookup.safe_project(project)
        clone = url = None
        for candidate in lookup.SOURCES[args.org](project):
            clone = cloner.clone(candidate)
            if clone is not None:
                url = candidate
                break
        reference = None
        if clone is None:
            sources[project] = {"source": None}
        else:
            tips = lookup.branch_tips(clone)
            reference, files = lookup.reference_lines(clone, tips.values())
            sources[project] = {
                "source": url,
                "branches": len(tips),
                "tips": sorted(set(tips.values())),
                "files": files,
                "lines": len(reference),
            }
        for held in rows.values():
            for row in held:
                if row["project"] == project:
                    by_example[row["id"]] = lookup.reading(row, reference)
        print(
            f"{args.org} {number}/{len(projects)} {project}: {sources[project].get('source')}",
            flush=True,
        )
    report: dict[str, Any] = {
        "org": args.org,
        "rule": {
            "upload": lookup.UPLOAD,
            "min_chars": TYPED_TARGET_MIN_CHARS,
            "suspicious_from": lookup.SUSPICIOUS,
            "found_from": lookup.FOUND,
            "binary_probe_bytes": lookup.BINARY_PROBE,
        },
        "windows": {},
        "projects": sources,
    }
    for window, held in rows.items():
        readings = [by_example[row["id"]] for row in held]
        report["windows"][window] = {
            **summarize(readings),
            "ids": {
                name: sorted(r["id"] for r in readings if r["class"] == name)
                for name in ("found", "suspicious")
            },
        }
        print(window, summarize(readings))
    report["provenance"] = provenance_header()
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
