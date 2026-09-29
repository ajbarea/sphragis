"""How much of H1's reading is the partition: alternative balanced splits against the seed effect.

Each run is one single-seed placebo run on one partition. Its H1 point estimate is the two halves'
own-minus-sibling contrasts, pooled over examples as `seed_effect.py` pools them, weighted
equally. With every partition at the same training seeds (balanced, seeds crossed with
partitions), the partition component is the variance of the partition means less the
within-partition (seed) variance over the number of seeds, the one-way estimator; a crossed
two-way fit differs only by the seed main effect. Seeds of one partition share its evaluation
set, so the component still carries that shared change-sampling noise, and partitions differ in
which changes each half is scored on: it is not the boundary alone. Descriptive; nothing is
tested.

    cd datasets/results && uv run --no-sync python ../../scripts/partition_variance.py \\
        --partition registered=rq1-placebo-openstack-v3.json,rq1-placebo-openstack-v3-s2.json \\
        --partition p2=rq1-placebo-openstack-v3-p2.json,rq1-placebo-openstack-v3-p2-s2.json \\
        --seed-effect seed-effect-placebo-openstack-v3.json \\
        --out partition-variance-openstack-v3.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import fmean, variance

from sphragis.provenance import provenance_header

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seed_effect import per_example_contrast  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--partition", action="append", required=True, metavar="NAME=RUN,RUN", help="one per partition"
)
parser.add_argument("--seed-effect", type=Path, help="the registered partition's seed effect")
parser.add_argument("--out", type=Path, required=True)


def run_h1(path: Path) -> tuple[int, float, dict[str, float]]:
    """A single-seed run's seed, H1 point estimate, and each half's pooled contrast."""
    run = json.loads(path.read_text())
    if len(run["seeds"]) != 1:
        raise SystemExit(f"{path}: expected a single-seed run, got seeds {run['seeds']}")
    seed = run["seeds"][0]
    orgs = sorted(run["corpora"])
    if len(orgs) != 2:
        raise SystemExit(f"{path}: expected two halves, got {orgs}")
    halves = {
        org: fmean(v for _, v in per_example_contrast(run["results"], org, other, seed).values())
        for org, other in (orgs, orgs[::-1])
    }
    return seed, fmean(halves.values()), halves


def components(by_partition: dict[str, dict[int, float]]) -> dict[str, float]:
    """Balanced one-way moment estimates: partition and within-partition components of H1."""
    seed_sets = {tuple(sorted(runs)) for runs in by_partition.values()}
    if len(seed_sets) != 1 or len(next(iter(seed_sets))) < 2:
        raise SystemExit(f"every partition needs the same two or more seeds, got {seed_sets}")
    n_seeds = len(next(iter(seed_sets)))
    means = [fmean(runs.values()) for runs in by_partition.values()]
    within = fmean(variance(list(runs.values())) for runs in by_partition.values())
    between = variance(means)
    partition_var = between - within / n_seeds
    return {
        "partitions": len(by_partition),
        "seeds_per_partition": n_seeds,
        "variance_of_partition_means": between,
        "within_partition_variance": within,
        "sigma_partition_squared": partition_var,
        "sigma_partition": max(partition_var, 0.0) ** 0.5,
        "within_partition_sd": within**0.5,
        "range_of_partition_means": [min(means), max(means)],
    }


def main() -> None:
    args = parser.parse_args()
    by_partition: dict[str, dict[int, float]] = {}
    runs_out: dict[str, list] = {}
    for spec in args.partition:
        name, _, paths = spec.partition("=")
        if not name or not paths or name in by_partition:
            raise SystemExit(f"--partition takes a new NAME=RUN,RUN, got {spec!r}")
        by_partition[name], runs_out[name] = {}, []
        for path in map(Path, paths.split(",")):
            seed, h1, halves = run_h1(path)
            if seed in by_partition[name]:
                raise SystemExit(f"{name}: seed {seed} given twice")
            by_partition[name][seed] = h1
            runs_out[name].append({"run": str(path), "seed": seed, "h1": h1, "halves": halves})
    report = {"runs": runs_out, **components(by_partition)}
    if args.seed_effect:
        effect = json.loads(args.seed_effect.read_text())
        report["seed_effect"] = {"sigma_b": effect["sigma_b"], "source": str(args.seed_effect)}
    for name, runs in by_partition.items():
        print(f"{name}: " + ", ".join(f"s{s} {h:+.4f}" for s, h in sorted(runs.items())))
    print(
        f"partition means range {report['range_of_partition_means'][0]:+.4f} to "
        f"{report['range_of_partition_means'][1]:+.4f}; sigma_partition "
        f"{report['sigma_partition']:.4f} (sigma^2 {report['sigma_partition_squared']:+.6f}), "
        f"within-partition SD {report['within_partition_sd']:.4f}"
    )
    report["provenance"] = provenance_header()
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
