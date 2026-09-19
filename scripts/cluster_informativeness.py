"""Is a change's size informative about its outcome?

The registration binds the gate to the pooled estimand, and one of the two reasons it gives
is Kahan et al.'s: pooling over examples is the wrong choice when cluster size carries
information about the outcome, because then a large change's extra weight is not neutral.
The report quotes that correlation, so it has to be re-derivable rather than asserted.

Two readings per organization, both over the changes a run actually scored:

  size against contrast    the correlation between a change's example count and its own
                           matched-minus-mismatched difference. Near zero means the weight
                           pooling gives a large change does not systematically favour
                           either arm, which is the condition the choice rests on.
  the estimands' distance  pooled minus change-averaged on the same clusters. This is what
                           the informativeness would cost if it were there, in the units the
                           gate reads.

Run: uv run --no-active python scripts/cluster_informativeness.py \
       datasets/results/rq1-windows-qtfull-fp32.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import fmean, median

from sphragis.experiment.runner import to_clusters
from sphragis.measure.stats import change_averaged_difference, paired_difference
from sphragis.provenance import provenance_header

RESULTS = Path("datasets/results/cluster-informativeness.json")

parser = argparse.ArgumentParser()
parser.add_argument("runs", type=Path, nargs="+")
parser.add_argument("--metric", default="exact_match")
parser.add_argument("--out", type=Path, default=RESULTS)


def correlation(xs: list[float], ys: list[float]) -> float:
    """Pearson's r, or 0.0 where a side does not vary and no correlation is defined."""
    mx, my = fmean(xs), fmean(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    denominator = (sum(a * a for a in dx) ** 0.5) * (sum(b * b for b in dy) ** 0.5)
    return sum(a * b for a, b in zip(dx, dy, strict=True)) / denominator if denominator else 0.0


def read(path: Path, metric: str) -> dict[str, dict[str, float]]:
    payload = json.loads(path.read_text())
    results = payload["results"]
    seeds = [str(s) for s in payload["seeds"]]
    orgs = sorted(key.split("|")[1] for key in results if key.startswith("base|"))

    # With three organizations "the mismatched arm" is a choice, and picking the first one
    # silently would answer a different question than the gate's two-organization contrast.
    if len(orgs) != 2:
        raise SystemExit(
            f"{path.name} scores {len(orgs)} organizations: {orgs}; the contrast is a pair"
        )

    report: dict[str, dict[str, float]] = {}
    for org in orgs:
        for seed in seeds:
            matched = results.get(f"adapter:{org}|{org}|s{seed}")
            other = [o for o in orgs if o != org]
            if not matched or not other:
                continue
            mismatched = results.get(f"adapter:{other[0]}|{org}|s{seed}")
            if not mismatched:
                continue
            clusters = to_clusters(matched, mismatched, metric=metric)
            sizes = [float(len(c.treatment)) for c in clusters]
            contrasts = [fmean(c.treatment) - fmean(c.control) for c in clusters]
            report[f"{org}|s{seed}"] = {
                "changes": float(len(clusters)),
                "examples": sum(sizes),
                "largest_change": max(sizes),
                "size_contrast_correlation": correlation(sizes, contrasts),
                "pooled": paired_difference(clusters),
                "change_averaged": change_averaged_difference(clusters),
                "estimand_distance": paired_difference(clusters)
                - change_averaged_difference(clusters),
            }
    return report


def summarize(runs: dict[str, dict[str, dict[str, float]]]) -> dict[str, dict[str, float]]:
    """Per organization, across every seed read: the median and the span.

    A single seed's correlation is one draw, and the registration rests on the claim that
    size carries no information, not on one run of it.
    """
    by_org: dict[str, list[dict[str, float]]] = {}
    for rows in runs.values():
        for name, row in rows.items():
            by_org.setdefault(name.split("|")[0], []).append(row)
    summary: dict[str, dict[str, float]] = {}
    for org, rows in by_org.items():
        correlations = [row["size_contrast_correlation"] for row in rows]
        distances = [row["estimand_distance"] for row in rows]
        summary[org] = {
            "seeds": float(len(rows)),
            "correlation_median": median(correlations),
            "correlation_min": min(correlations),
            "correlation_max": max(correlations),
            "estimand_distance_median": median(distances),
            "estimand_distance_min": min(distances),
            "estimand_distance_max": max(distances),
        }
    return summary


def main() -> None:
    args = parser.parse_args()
    report = {"metric": args.metric, "provenance": provenance_header(), "runs": {}}
    for path in args.runs:
        rows = read(path, args.metric)
        report["runs"][path.name] = rows
        for name, row in rows.items():
            print(
                f"{path.name} {name:<16} {int(row['changes']):4d} changes, largest "
                f"{int(row['largest_change']):3d}: r = {row['size_contrast_correlation']:+.3f}, "
                f"pooled {row['pooled']:+.4f} against change-averaged "
                f"{row['change_averaged']:+.4f}"
            )
    report["summary"] = summarize(report["runs"])
    for org, row in sorted(report["summary"].items()):
        print(
            f"{org:<10} over {int(row['seeds'])} seeds: r median "
            f"{row['correlation_median']:+.3f} ({row['correlation_min']:+.3f} to "
            f"{row['correlation_max']:+.3f}), estimands differ by "
            f"{row['estimand_distance_median']:+.4f} "
            f"({row['estimand_distance_min']:+.4f} to {row['estimand_distance_max']:+.4f})"
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
