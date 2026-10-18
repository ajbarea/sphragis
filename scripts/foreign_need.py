"""What a foreign pool must hold to cover H2's matched arm over an organization's admissible list.

Each admissible partition's halves are read as `partition_run.sbatch` trains them: the placebo
corpus under `--partitions/p<seed>` (`placebo_corpus.py --dedup-org --partition-seed <seed>`),
each half read by `window_split` and cut to the list's N by `equalize_training` at the seed
`rq1_pilot.py --equalize-train` uses. A half's draw is the sibling draw of the other half's
foreign arm, so a pool covers every arm when it holds at least the largest C++ count
(`decomposition.is_cpp`) and the largest other count over all draws (`holdout.matched_foreign_draw`,
research log 2026-10-14, item 3 of the H2 plan). Reads training windows only.

    uv run --no-sync --no-active python scripts/foreign_need.py \\
        --admissible datasets/results/admissible-partitions-qt.json \\
        --partitions ~/ajsoftworks/sphragis-data-local/qt/partitions \\
        --out datasets/results/foreign-need-qt.json
"""

from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

from sphragis.experiment.decomposition import H2_POPULATION, is_cpp
from sphragis.experiment.holdout import equalize_training, window_split
from sphragis.provenance import provenance_header

#: `rq1_pilot.py`'s `--split-seed` default, which `partition_run.sbatch` leaves unset; the cut to N
#: is seeded by it.
SPLIT_SEED = 0
#: A partition run's corpus root, as `partition_run.sbatch` names it.
PARTITION = re.compile(r"/corpus-partition-[^/]+-p(\d+)(?:-s\d+)?-n\d+[^/]*/")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--admissible", type=Path, required=True, help="admissible_partitions.py's list"
)
parser.add_argument("--partitions", type=Path, required=True, help="holds p<seed>/ for each seed")
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--workers", type=int, default=1, help="partitions read in parallel")
parser.add_argument(
    "--runs",
    type=Path,
    nargs="*",
    default=[],
    help="stored partition runs (or their likelihood rescorings): each half's training and "
    "held-out counts must match the partition rebuilt here",
)


def partition_draws(seed: int, *, partitions: Path, org: str, source_root: str, size: int) -> dict:
    """Each half's training draw at N, counted C++ and other, from partition `seed`'s corpus."""
    root = partitions / f"p{seed}"
    manifest = json.loads((root / "placebo.json").read_text())
    expected = {
        "source_org": org,
        "partition_seed": seed,
        "plant": None,
        "names": [f"{org}-a", f"{org}-b"],
        "train_window": "train",
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"{root}: {key} is {manifest.get(key)!r}, not {value!r}")
    # The admissible list records its root resolved, the placebo corpus as it was given.
    built_from = Path(str(manifest.get("source_root"))).resolve()
    if built_from != Path(source_root).resolve():
        raise ValueError(f"{root}: source_root is {built_from}, not {source_root}")
    if manifest.get("dedup_org") is None:
        raise ValueError(f"{root}: not built with --dedup-org")
    split = {name: window_split(root, name) for name in manifest["names"]}
    drawn = equalize_training(
        {n: train for n, (train, _, _) in split.items()}, seed=SPLIT_SEED, size=size
    )
    out = {}
    for name, rows in drawn.items():
        cpp = sum(1 for row in rows if is_cpp(row))
        train, held_out, _ = split[name]
        out[name] = {
            "train_examples": len(train),
            "held_out_examples": len(held_out),
            "drawn": len(rows),
            "in_language": cpp,
            "other": len(rows) - cpp,
        }
    return out


def against_runs(draws: dict[str, dict], runs: list[Path], *, org: str) -> list[str]:
    """The stored runs each rebuilt partition is checked against: each half's training and
    held-out counts as the run recorded them (`rq1_pilot.py`'s `corpora`). Refused on any
    difference, or a run of no partition read here."""
    checked = []
    for path in runs:
        run = json.loads(path.read_text())
        sources = {h: c["source"] for h, c in run["corpora"].items()}
        seeds = {m.group(1) for s in sources.values() if (m := PARTITION.search(s))}
        if len(seeds) != 1 or any("-plant" in s for s in sources.values()):
            raise ValueError(f"{path} is not one unplanted partition run of {org}")
        (seed,) = seeds
        if seed not in draws:
            raise ValueError(f"{path} is a run of partition {seed}, not one read here")
        for half, recorded in run["corpora"].items():
            mine = draws[seed][half]
            for key in ("train_examples", "held_out_examples"):
                if recorded[key] != mine[key]:
                    raise ValueError(
                        f"{path}: {half} {key} {recorded[key]} recorded, {mine[key]} rebuilt"
                    )
        checked.append(str(path))
    return checked


def main() -> None:
    args = parser.parse_args()
    admissible = json.loads(args.admissible.read_text())
    org, size = admissible["org"], admissible["size_floor"]
    read = partial(
        partition_draws,
        partitions=args.partitions,
        org=org,
        source_root=admissible["root"],
        size=size,
    )
    seeds = admissible["admissible"]
    if args.workers == 1:
        counted = list(map(read, seeds))
    else:
        with ProcessPoolExecutor(args.workers) as pool:
            counted = list(pool.map(read, seeds))
    draws = {str(seed): halves for seed, halves in zip(seeds, counted, strict=True)}
    try:
        checked = against_runs(draws, args.runs, org=org)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    need = {}
    for stratum in ("in_language", "other"):
        seed, half = max(
            ((s, h) for s, halves in draws.items() for h in halves),
            key=lambda key: draws[key[0]][key[1]][stratum],
        )
        need[stratum] = {"examples": draws[seed][half][stratum], "seed": int(seed), "half": half}
    report = {
        "org": org,
        "population": H2_POPULATION,
        "size": size,
        "split_seed": SPLIT_SEED,
        "admissible": str(args.admissible),
        "partitions": len(draws),
        "checked_against_runs": checked,
        "need": need,
        "draws": draws,
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    shares = [h["in_language"] / h["drawn"] for halves in draws.values() for h in halves.values()]
    print(
        f"{org}: {len(draws)} partitions, C++ share of a draw {min(shares):.3f} to "
        f"{max(shares):.3f}; "
        f"a pool needs {need['in_language']['examples']} C++ and {need['other']['examples']} other "
        f"examples; wrote {args.out}"
    )


if __name__ == "__main__":
    main()
