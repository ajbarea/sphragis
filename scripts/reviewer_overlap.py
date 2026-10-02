"""How far an evaluated half's dev reviewers also reviewed its sibling half's training data.

H1 credits an organization with what a sibling half's adapter gains over a foreign one. If the
two halves share reviewers, part of that gain could be particular reviewers' habits rather than
the organization's, so the overlap is measured before any analysis of it is registered.

A change's reviewers are the accounts in its attention-set history other than its owner (salted
pseudonyms; the raw-number keys are never read). That is change-level, not the author of the
example's own comment, which the built examples do not keep. Halves are the registered
largest-first assignment, windows the corpus's own dedup and split. For each dev example:
whether any of its reviewers reviewed a sibling-half training change, and its exposure, the
share of the sibling half's training examples that come from changes one of its reviewers
reviewed. The test window is sealed and never read.

    uv run --no-sync --no-active python scripts/reviewer_overlap.py \\
        --root openstack=../wm-bots/datasets/gerrit \\
        --root wikimedia=../fa-auto/datasets/gerrit \\
        --out datasets/results/reviewer-overlap.json
"""

from __future__ import annotations

import argparse
import gzip
import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from sphragis.corpus.cli import WINDOWS
from sphragis.corpus.halves import assign, project_counts
from sphragis.corpus.load import refined_examples
from sphragis.corpus.pipeline import run_dedup, run_split
from sphragis.provenance import provenance_header

ATTENTION = ("attention_set", "removed_from_attention_set")
QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", action="append", required=True, help="org=root of a built corpus")
parser.add_argument("--out", type=Path, required=True)


def change_reviewers(change: Mapping) -> set[str]:
    """Pseudonymous accounts in a change's attention-set history, its owner excluded."""
    owner = (change.get("owner") or {}).get("_account_id")
    people = {
        (entry.get("account") or {}).get("_account_id")
        for part in ATTENTION
        for entry in (change.get(part) or {}).values()
    }
    return {p for p in people if p and p != owner}


def quantiles(values: list[float]) -> dict[str, float]:
    """Nearest-rank quantiles, the order statistic at floor(q * n), no interpolation."""
    ordered = sorted(values)
    return (
        {str(q): ordered[min(int(q * len(ordered)), len(ordered) - 1)] for q in QUANTILES}
        if ordered
        else {}
    )


def overlap(
    train: Iterable[Mapping],
    dev: Iterable[Mapping],
    side: Mapping[str, int],
    reviewers: Mapping[tuple[str, str], set[str]],
) -> dict:
    """Per dev example, a shared reviewer with the sibling half's training data, and exposure."""
    by_half: dict[int, list[set[str]]] = {0: [], 1: []}
    for row in train:
        if row["project"] in side:
            by_half[side[row["project"]]].append(
                reviewers.get((row["project"], row["change_id"]), set())
            )
    sibling_people = {h: set().union(*seen) if seen else set() for h, seen in by_half.items()}
    counted = skipped = known = shared = shared_two = 0
    exposures: list[float] = []
    for row in dev:
        if row["project"] not in side:
            skipped += 1
            continue
        counted += 1
        people = reviewers.get((row["project"], row["change_id"]), set())
        if not people:
            continue
        known += 1
        sibling = by_half[1 - side[row["project"]]]
        hit = sum(bool(people & seen) for seen in sibling)
        shared += hit > 0
        # Two distinct accounts: an account on most changes (CI, a bot without a service tag,
        # which the raw snapshots never carry) cannot make an example count by itself.
        shared_two += len(people & sibling_people[1 - side[row["project"]]]) >= 2
        exposures.append(hit / len(sibling) if sibling else 0.0)
    return {
        "dev_examples_in_halves": counted,
        "dev_examples_without_a_half": skipped,
        "dev_examples_with_reviewers": known,
        "dev_examples_sharing_a_sibling_reviewer": shared,
        "share_sharing": shared / known if known else None,
        "dev_examples_sharing_two_sibling_reviewers": shared_two,
        "share_sharing_two": shared_two / known if known else None,
        "exposure_quantiles": quantiles(exposures),
        "reviewers_per_half": [len(sibling_people[0]), len(sibling_people[1])],
        "reviewers_in_both_halves": len(sibling_people[0] & sibling_people[1]),
    }


def main() -> None:
    args = parser.parse_args()
    report: dict = {"orgs": {}}
    for spec in args.root:
        org, root = spec.split("=", 1)
        reviewers: dict[tuple[str, str], set[str]] = {}
        for path in sorted((Path(root) / org / "raw").glob("*.ndjson.gz")):
            with gzip.open(path, "rt") as lines:
                for line in lines:
                    change = json.loads(line)
                    key = (change["project"], change["change_id"])
                    reviewers[key] = change_reviewers(change)
        rows = refined_examples(Path(root), org)
        side = assign(dict(project_counts(rows, WINDOWS["train"])))
        kept, _ = run_dedup(rows)
        windows, _, _ = run_split(kept, WINDOWS)
        if windows.get("test"):
            raise SystemExit(f"{org}: the corpus holds test-window examples; it is sealed")
        report["orgs"][org] = overlap(windows["train"], windows["dev"], side, reviewers)
        print(org, report["orgs"][org])
    report["provenance"] = provenance_header()
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
