"""Two projects inside ONE organization, as the contrast's two sides.

The probe found that two projects in one organization are as separable as two organizations,
which says the learnable unit may be the codebase rather than the organization. The
generative contrast can test that directly and needs no new experiment code: build a corpus
per project and hand them to `rq1_pilot.py` where it expects organizations.

If project-level adapters separate where organization-level ones did not, the finding is
that the house style exists at a finer grain than the study assumed, and a privacy perimeter
drawn around an organization is not drawn where the signal lives. If they do not separate
either, the premise is wrong at every altitude, which is worth knowing before a Stage 1
report is written around it.

Run: uv run --no-active python scripts/project_corpora.py --org qt --out-dir <dir>
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from sphragis.corpus.cli import WINDOWS
from sphragis.corpus.load import refined_month_files, write_derived_file
from sphragis.corpus.pipeline import run_dedup

parser = argparse.ArgumentParser()
parser.add_argument("--org", default="qt")
parser.add_argument("--out-dir", type=Path, required=True)
parser.add_argument(
    "--window",
    action="append",
    choices=("pilot", "train", "dev", "all"),
    help="repeatable; default train. RQ2's clients need no time split, so they may pool train "
    "and dev, or take `all` months present, which an organization outside the study's windows "
    "needs. The test window is never offered, and `all` cannot reach it: it is never fetched.",
)
parser.add_argument(
    "--projects", action="append", default=[], help="repeatable; default the top two"
)


def load(org: str, window: str) -> list[dict[str, Any]]:
    # `all` takes every month built for this organization. The test window is sealed and never
    # fetched, so there is nothing of it on disk for `all` to reach.
    first, last = ("0000-00-00", "9999-99-99") if window == "all" else WINDOWS[window]
    rows: list[dict[str, Any]] = []
    for path in refined_month_files(Path("datasets/gerrit"), org):
        if not (first[:7] <= path.stem < last[:7]):
            continue
        rows.extend(json.loads(line) for line in path.open() if line.strip())
    kept, _ = run_dedup(rows)
    return kept


def main() -> None:
    args = parser.parse_args()
    windows = args.window or ["train"]
    rows = [row for window in windows for row in load(args.org, window)]
    rows, _ = run_dedup(rows)
    by_project: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_project.setdefault(str(row["project"]), []).append(row)

    counts = Counter({p: len(v) for p, v in by_project.items()})
    print(f"{args.org} {'+'.join(windows)}: {len(rows)} deduplicated examples")
    for project, n in counts.most_common(6):
        changes = len({r["change_id"] for r in by_project[project]})
        print(f"  {project:34s} {n:5d} examples over {changes:4d} changes")

    # Two for the pairwise contrast; RQ2's clients take as many projects as are named.
    chosen = args.projects or [p for p, _ in counts.most_common(2)]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"org": args.org, "windows": windows, "projects": {}}
    for project in chosen:
        if project not in by_project:
            raise SystemExit(f"{project} is not in {args.org}'s {'+'.join(windows)} windows")
        slug = project.replace("/", "_")
        path = args.out_dir / f"{slug}.jsonl"
        examples = by_project[project]
        write_derived_file(path, examples, sources=[(Path("datasets/gerrit"), args.org)])
        manifest["projects"][project] = {
            "path": str(path),
            "examples": len(examples),
            "changes": len({r["change_id"] for r in examples}),
        }
        print(f"wrote {path}: {len(examples)} examples")

    out = args.out_dir / f"manifest-{args.org}-projects.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {out}")
    print(
        "\nBoth sides come from one organization, so anything the contrast finds is a\n"
        "codebase effect and cannot be an organizational one."
    )


if __name__ == "__main__":
    main()
