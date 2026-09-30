"""The ordered list of admissible partitions H1's runs are drawn from, fixed before any run.

Partition seeds are tried in order from `--start`. A seed is admissible when its halves, built as
`placebo_corpus.py --dedup-org --partition-seed` builds them (the organization deduplicated
once, projects assigned in the seeded order on training-window counts, each half then deduplicated
and split on its own), meet `split_criteria` with the fixed training size N, read from
`--training-size` (`training_size.py`), and the reference's language-mix ceiling. The first
`--count` admissible seeds are written in order; run k of the study uses the k-th. Reads the
corpus's rows and no outcome: the organization's dedup sees every window, the assignment and the
criteria the training window alone.

    uv run --no-sync --no-active python scripts/admissible_partitions.py \\
        --root datasets/gerrit --org openstack \\
        --training-size datasets/results/training-size-openstack.json \\
        --reference datasets/results/split-criteria-openstack.json \\
        --out datasets/results/admissible-partitions-openstack.json
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from sphragis.corpus.cli import WINDOWS
from sphragis.corpus.halves import excluded_projects, halves, runner_train, split_criteria
from sphragis.corpus.load import refined_examples
from sphragis.corpus.pipeline import run_dedup
from sphragis.experiment.partitions import K_MAX
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--org", required=True)
parser.add_argument("--reference", type=Path, required=True, help="a split-criteria artifact")
parser.add_argument(
    "--training-size", type=Path, required=True, help="training_size.py's artifact: N"
)
parser.add_argument("--start", type=int, default=1)
parser.add_argument("--count", type=int, default=K_MAX)
parser.add_argument("--batch", type=int, default=24, help="seeds checked per parallel round")
parser.add_argument("--max-seed", type=int, default=2_000, help="stop searching past this seed")
parser.add_argument("--workers", type=int, default=6)
parser.add_argument("--out", type=Path, required=True)

_ROWS: list[dict] = []
_REF: list[dict] = []
_FLOOR = 0


def _init(rows: list[dict], ref: list[dict], floor: int) -> None:
    global _ROWS, _REF, _FLOOR
    _ROWS, _REF, _FLOOR = rows, ref, floor


def check(seed: int) -> dict:
    train = runner_train(_ROWS, WINDOWS, seed)
    own = halves(_ROWS, train, WINDOWS["train"], seed)
    result = split_criteria(own, _REF, size_floor=_FLOOR)
    return {
        "seed": seed,
        "qualifies": result["qualifies"],
        "checks": result["checks"],
        "train_examples": [h["train_examples"] for h in own],
        "projects": [h["projects"] for h in own],
        "suffix_total_variation": result["suffix_total_variation"],
    }


def main() -> None:
    args = parser.parse_args()
    reference = json.loads(args.reference.read_text())
    size_floor = json.loads(args.training_size.read_text())["training_size"]
    rows, removed = run_dedup(refined_examples(args.root, args.org))
    checked: list[dict] = []
    seed = args.start
    with ProcessPoolExecutor(
        args.workers, initializer=_init, initargs=(rows, reference["halves"], size_floor)
    ) as pool:
        while sum(c["qualifies"] for c in checked) < args.count:
            if seed > args.max_seed:
                raise SystemExit(
                    f"fewer than {args.count} admissible partitions by seed {seed - 1}"
                )
            batch = list(range(seed, seed + args.batch))
            checked += list(pool.map(check, batch))
            seed += args.batch
            print(
                f"checked through seed {seed - 1}: "
                f"{sum(c['qualifies'] for c in checked)} admissible",
                flush=True,
            )
    admissible = [c["seed"] for c in checked if c["qualifies"]][: args.count]
    last = admissible[-1]
    report = {
        "org": args.org,
        "size_floor": size_floor,
        "size_floor_source": f"{args.training_size}: training_size",
        "language_mix_ceiling": reference["suffix_total_variation"],
        "reference": str(args.reference),
        "start": args.start,
        "admissible": admissible,
        # Seeds past the last admissible one were checked only because a batch ran in parallel;
        # they are dropped here, as never part of the list.
        "checked": [c for c in checked if c["seed"] <= last],
        "dedup_org": removed,
        "excluded_projects": excluded_projects(rows, WINDOWS["train"]),
    }
    report["provenance"] = provenance_header()
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{len(admissible)} admissible of {len(report['checked'])} checked; wrote {args.out}")


if __name__ == "__main__":
    main()
