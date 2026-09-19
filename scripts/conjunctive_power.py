"""The power the RQ1 gate actually has, which is not the power that was computed.

`power_rq1.py` reports each organization's minimum detectable effect separately, at 80%
power. The gate is conjunctive: it passes only when BOTH organizations' intervals sit above
zero. Conjunctive power is the probability of detecting the effect everywhere at once, and
for independent organizations it is the product, so two arms at 80% give a gate at 64%. That
is the number section 5 of the Stage 1 report has to state, because it is the probability the
study answers its question.

This simulates both arms at the sizes the test window is projected to reach, under lifts
taken from the windowed run, and reports marginal power per organization beside the
conjunctive power of the gate.

Run: uv run --no-active python scripts/conjunctive_power.py
"""

from __future__ import annotations

import argparse
import json
import random
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from sphragis.experiment.power import (
    _null,
    _shift,
    lift_for_effect,
    realised_difference,
    seed_trial,
)
from sphragis.experiment.runner import to_clusters
from sphragis.measure.stats import cluster_bootstrap, supports_direction
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser()
parser.add_argument(
    "results", type=Path, nargs="?", default=Path("datasets/results/rq1-windows.json")
)
parser.add_argument("--size", action="append", default=[], metavar="ORG=N")
parser.add_argument("--trials", type=int, default=400)
parser.add_argument("--resamples", type=int, default=500)
parser.add_argument("--seed", type=int, default=23)
parser.add_argument("--out", type=Path, default=Path("datasets/results/conjunctive-power.json"))
parser.add_argument(
    "--seeds", type=int, default=1, help="above 1, simulate multi-seed studies read both ways"
)
parser.add_argument("--sigma-b", type=float, default=0.0, help="the seed main effect's SD")
parser.add_argument(
    "--redraw",
    type=float,
    default=0.055,
    help="share of each arm's outcomes a seed redraws; 0.055 reproduces the measured churn",
)
parser.add_argument("--workers", type=int, default=6)


def arm_draws(
    clusters, *, lift: float, n: int, trials: int, resamples: int, seed: int
) -> list[bool]:
    """Whether each simulated study of this organization supports the direction."""
    rng = random.Random(seed)
    out = []
    for trial in range(trials):
        sample = [
            _shift(_null(clusters[rng.randrange(len(clusters))], rng), lift, rng) for _ in range(n)
        ]
        out.append(
            supports_direction(cluster_bootstrap(sample, seed=seed + trial, resamples=resamples))
        )
    return out


def _seed_trial(job):
    clusters, lift, n, seeds, sigma_b, redraw, resamples, seed = job
    return seed_trial(
        clusters,
        lift=lift,
        n_changes=n,
        seeds=seeds,
        sigma_b=sigma_b,
        redraw=redraw,
        resamples=resamples,
        seed=seed,
    )


def main() -> None:
    args = parser.parse_args()
    if args.seeds == 1 and args.sigma_b > 0:
        raise SystemExit("a seed main effect needs --seeds above 1; one seed cannot carry it")
    payload = json.loads(args.results.read_text())
    results = payload["results"]
    sizes = dict(s.split("=") for s in args.size) if args.size else {}
    orgs = sorted({k.split("|")[1] for k in results if k.startswith("base|")})

    report: dict[str, object] = {
        "trials": args.trials,
        "resamples": args.resamples,
        "seeds": args.seeds,
        "sigma_b": args.sigma_b,
        "redraw": args.redraw if args.seeds > 1 else None,
        "rule": "crossed" if args.seeds > 1 else "single seed",
    }
    per_org: dict[str, list[bool]] = {}
    median_rule: dict[str, list[bool]] = {}
    for org in orgs:
        other = next(o for o in orgs if o != org)
        clusters = to_clusters(
            results[f"adapter:{org}|{org}|s1"], results[f"adapter:{other}|{org}|s1"]
        )
        n = int(sizes.get(org, len(clusters)))
        # Find the lift reproducing the effect this organization actually showed, so the
        # simulation asks "if the truth is what we saw, how often does the gate pass?"
        # By bisection, not by stepping: a 0.02 step overshot Qt's +0.0110 to +0.0138, a
        # quarter high and across its detection threshold, which reported the gate's power
        # as 0.91 when the effect it simulated was not the effect observed.
        observed = cluster_bootstrap(clusters, seed=args.seed)["estimate"]
        lift = lift_for_effect(clusters, observed, seed=args.seed)
        realised = realised_difference(clusters, lift=lift, seed=args.seed, draws=400)
        print(f"{org}: lift {lift:.4f} realises {realised:+.4f} against observed {observed:+.4f}")
        if args.seeds > 1:
            jobs = [
                (
                    clusters,
                    lift,
                    n,
                    args.seeds,
                    args.sigma_b,
                    args.redraw,
                    args.resamples,
                    args.seed + trial,
                )
                for trial in range(args.trials)
            ]
            with ProcessPoolExecutor(args.workers) as pool:
                trials = list(pool.map(_seed_trial, jobs, chunksize=4))
            per_org[org] = [t.crossed for t in trials]
            median_rule[org] = [t.median_seed for t in trials]
            report[f"{org}_median_seed_power"] = sum(median_rule[org]) / args.trials
            report[f"{org}_between_seed_variance"] = (
                sum(t.between_seed_variance for t in trials) / args.trials
            )
        else:
            per_org[org] = arm_draws(
                clusters,
                lift=lift,
                n=n,
                trials=args.trials,
                resamples=args.resamples,
                seed=args.seed,
            )
        marginal = sum(per_org[org]) / args.trials
        report[org] = {
            "observed_effect": observed,
            "lift": lift,
            "realised_effect": realised,
            "n_changes": n,
            "marginal_power": marginal,
        }
        print(f"{org}: observed {observed:+.4f}, {n} changes -> marginal power {marginal:.3f}")

    first, second = orgs
    both = sum(a and b for a, b in zip(per_org[first], per_org[second], strict=True)) / args.trials
    product = (sum(per_org[first]) / args.trials) * (sum(per_org[second]) / args.trials)
    report["conjunctive_power"] = both
    report["product_of_marginals"] = product
    if median_rule:
        report["median_seed_conjunctive_power"] = (
            sum(a and b for a, b in zip(median_rule[first], median_rule[second], strict=True))
            / args.trials
        )
        median_power = report["median_seed_conjunctive_power"]
        print(f"median-seed rule, both organizations:                {median_power:.3f}")
    print(f"\nconjunctive power of the gate (both organizations): {both:.3f}")
    print(f"product of the marginals, if independent:            {product:.3f}")
    print(
        "\nThe gate passes only when both arms do, so this is the probability the study\n"
        "answers its question at the sizes and effects simulated."
    )

    report["provenance"] = provenance_header()
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
