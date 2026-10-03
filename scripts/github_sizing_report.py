"""GitHub candidate organizations sized from `github_sizing.py` draws: weighted totals and yield.

Each month of the training window is a stratum. A draw picked a day uniformly, then one PR uniformly
among that day's, so D * N_day * y is unbiased for the month's total of y (Hansen-Hurwitz); a
month's estimate is the mean over its draws, the window's the sum over months. The interval is a
stratified bootstrap over draws within each month. Review activity is heavy-tailed, and a
nonparametric bootstrap understates the width of such intervals at small samples, so it is read
as a lower bound on the uncertainty.

Projected training examples apply the thread-to-example conversion measured on the Gerrit
corpora the study already built (refined training-window examples over reviewer line comments
that reached a later patch set), read from those corpora here rather than typed.

    uv run --no-sync --no-active python scripts/github_sizing_report.py \\
        --draws datasets/results/github-sizing-draws-*.jsonl \\
        --gerrit openstack=../wm-bots/datasets/gerrit \\
        --gerrit wikimedia=../fa-auto/datasets/gerrit \\
        --out datasets/results/github-sizing-report.json
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from sphragis.provenance import provenance_header

# The training window, as the Gerrit corpora define it.
TRAIN_MONTHS = [f"2024-{m:02d}" for m in (11, 12)] + [f"2025-{m:02d}" for m in range(1, 9)]
# Two halves at the design's training size, and one adapter at it (admissible-partitions
# size floor; registered decisions, "Training size").
DESIGN_N = 1850
RQ1_NEED, RQ2_NEED = 2 * DESIGN_N, DESIGN_N
# Bootstrap replicates; the interval is a percentile interval at 95%.
RESAMPLES = 10_000
SEED = 7

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--draws", type=Path, nargs="+", required=True)
parser.add_argument("--gerrit", action="append", required=True, help="org=root of a built corpus")
parser.add_argument("--out", type=Path, required=True)


def conversion(org: str, root: Path) -> dict:
    """Refined training-window examples over reviewer line comments that reached a successor."""
    built = refined = lines = 0
    for month in TRAIN_MONTHS:
        drops = json.loads((root / org / "examples" / f"{month}.drops.json").read_text())
        n_built = sum(1 for _ in (root / org / "examples" / f"{month}.jsonl").open())
        built += n_built
        lines += n_built + drops.get("acknowledgement", 0) + drops.get("no_anchored_hunk", 0)
        lines += sum(v for k, v in drops.items() if k.startswith("ill_posed"))
        refined += sum(1 for _ in (root / org / "refined" / f"{month}.jsonl").open())
    return {
        "built": built,
        "refined": refined,
        "reviewer_line_comments": lines,
        "rate": refined / lines,
    }


def estimate(draws: list[dict], key: str) -> dict[str, list[float]]:
    """Per-month lists of D * N_day * y for one count."""
    by_month: dict[str, list[float]] = defaultdict(list)
    for d in draws:
        y = d.get(key, 0) if d.get("n_day") and not d.get("ai_author") else 0
        by_month[d["month"]].append(d["days"] * d.get("n_day", 0) * y)
    return by_month


def total(by_month: dict[str, list[float]]) -> float:
    return sum(sum(v) / len(v) for v in by_month.values())


def interval(by_month: dict[str, list[float]], rng: random.Random) -> tuple[float, float]:
    reps = sorted(
        sum(sum(rng.choices(v, k=len(v))) / len(v) for v in by_month.values())
        for _ in range(RESAMPLES)
    )
    return reps[int(0.025 * RESAMPLES)], reps[int(0.975 * RESAMPLES) - 1]


def main() -> None:
    args = parser.parse_args()
    rates = {}
    for spec in args.gerrit:
        org, root = spec.split("=", 1)
        rates[org] = conversion(org, Path(root))
    low_rate, high_rate = (
        min(r["rate"] for r in rates.values()),
        max(r["rate"] for r in rates.values()),
    )
    rng = random.Random(SEED)
    report: dict = {"conversion": rates, "design_n": DESIGN_N, "orgs": {}}
    for path in args.draws:
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        org = rows[0]["org"]
        draws = [r for r in rows if "draw" in r]
        if not any(r.get("done") for r in rows):
            raise SystemExit(f"{path}: the sampler did not finish")
        threads = estimate(draws, "threads")
        t, (lo, hi) = total(threads), interval(threads, rng)
        prs = {m: [d["days"] * d["n_day"] for d in draws if d["month"] == m] for m in TRAIN_MONTHS}
        ai = {
            m: [d["days"] * d["n_day"] * bool(d.get("ai_author")) for d in draws if d["month"] == m]
            for m in TRAIN_MONTHS
        }
        dirs = Counter(x for d in draws if not d.get("ai_author") for x in d.get("top_dirs", []))
        examples = (lo * low_rate, hi * high_rate)
        report["orgs"][org] = {
            "draws": len(draws),
            "prs": round(total(prs)),
            "bot_or_ai_authored_share": total(ai) / total(prs) if total(prs) else 0.0,
            "threads": round(t),
            "threads_95": [round(lo), round(hi)],
            "suggestion_threads": round(total(estimate(draws, "suggestion_threads"))),
            "bot_review_threads": round(total(estimate(draws, "bot_threads"))),
            "examples_range": [round(examples[0]), round(examples[1])],
            "rq1": "yes"
            if examples[0] >= RQ1_NEED
            else "borderline"
            if examples[1] >= RQ1_NEED
            else "no",
            "rq2": "yes"
            if examples[0] >= RQ2_NEED
            else "borderline"
            if examples[1] >= RQ2_NEED
            else "no",
            "repos_sampled": len({d["repo"] for d in draws if d.get("repo")}),
            "frame_capped_draws": sum(bool(d.get("frame_capped")) for d in draws),
            "top_dirs": dirs.most_common(15),
        }
        o = report["orgs"][org]
        print(
            f"{org}: threads {o['threads']} {o['threads_95']}, examples {o['examples_range']}, "
            f"RQ1 {o['rq1']}, RQ2 {o['rq2']}, bot/AI-authored {o['bot_or_ai_authored_share']:.1%}"
        )
    report["provenance"] = provenance_header()
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
