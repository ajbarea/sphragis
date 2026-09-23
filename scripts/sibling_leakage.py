"""How much of an evaluated half's dev window a sibling half's training data already holds.

A contrast that credits an organization with whatever a sibling half's adapter gains over a
foreign one reads shared text as style. Boilerplate shared across an organization's projects
(build and CI configuration, licence headers, dependency bumps) would raise the sibling's score
for a reason that is not style, so near-duplicate leakage from the sibling's train window into
the evaluated half's dev window is measured beside the own half's and a foreign organization's,
at the thresholds `window_report.py` reports.

Halves are assigned exactly as `placebo_corpus.py` assigns them; windows are deduplicated and
split as `window_report.py` does. The test window is sealed and never read.

    uv run --no-sync --no-active python scripts/sibling_leakage.py \\
        --root datasets/gerrit --out datasets/results/sibling-leakage.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from placebo_corpus import assign, project_counts  # noqa: E402

from sphragis.corpus.cli import WINDOWS  # noqa: E402
from sphragis.corpus.load import refined_examples  # noqa: E402
from sphragis.corpus.pipeline import run_dedup, run_split  # noqa: E402
from sphragis.experiment.neutral import closest_training_match  # noqa: E402
from sphragis.provenance import provenance_header  # noqa: E402

THRESHOLDS = (0.8, 0.7, 0.6, 0.5)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, default=Path("datasets/gerrit"))
parser.add_argument("--org", action="append", default=[], help="repeatable; default both")
parser.add_argument("--out", type=Path, default=None)


def windows_by_half(root: Path, org: str) -> dict[str, dict[str, list[dict]]]:
    """Train and dev windows for each placebo half of one organization."""
    rows = refined_examples(root, org)
    if not rows:
        raise SystemExit(f"{org}: no refined examples under {root / org}")
    # The assignment is the placebo's own: counted on the built rows before dedup, exactly as
    # `placebo_corpus.main` counts them, so these are the halves the gate trains on.
    side_of = assign(dict(project_counts(rows, WINDOWS["train"])))
    kept, _ = run_dedup(rows)
    windows, _, _ = run_split(kept, WINDOWS)
    halves: dict[str, dict[str, list[dict]]] = {
        f"{org}-a": {"train": [], "dev": []},
        f"{org}-b": {"train": [], "dev": []},
    }
    for window in ("train", "dev"):
        for row in windows[window]:
            side = side_of.get(row["project"])
            if side is not None:
                halves[f"{org}-{'ab'[side]}"][window].append(row)
    return halves


def rates(train: list[dict], dev: list[dict]) -> dict[str, float]:
    """The share of dev examples whose closest train example reaches each threshold."""
    similarities = [similarity for _, similarity in closest_training_match(train, dev)]
    return {
        f"{t:g}": sum(1 for s in similarities if s >= t) / len(similarities)
        if similarities
        else 0.0
        for t in THRESHOLDS
    } | {"dev_examples": float(len(similarities))}


def main() -> None:
    args = parser.parse_args()
    orgs = args.org or ["openstack", "qt"]
    halves = {org: windows_by_half(args.root, org) for org in orgs}
    report: dict[str, dict] = {}
    for org in orgs:
        foreign = [o for o in orgs if o != org]
        for name, own in halves[org].items():
            sibling = next(h for n, h in halves[org].items() if n != name)
            cell = {
                "own": rates(own["train"], own["dev"]),
                "sibling": rates(sibling["train"], own["dev"]),
            }
            for other in foreign:
                other_train = [r for h in halves[other].values() for r in h["train"]]
                cell[f"foreign:{other}"] = rates(other_train, own["dev"])
            report[name] = cell
            print(name, json.dumps(cell), flush=True)
    if args.out:
        args.out.write_text(
            json.dumps(
                {
                    "thresholds": list(THRESHOLDS),
                    "halves": report,
                    "provenance": provenance_header(),
                },
                indent=2,
            )
        )
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
