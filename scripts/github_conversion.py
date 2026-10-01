"""GitHub's thread-to-example conversion, read from a built pilot month of a GitHub organization.

The sizing (`github_sizing.py`) estimates an organization's reviewer-started threads, so the
conversion that turns them into training examples is refined examples over those threads, both
counted on the same PRs: every PR the collection route lists for the month (`merged_prs`), each
counted with the sizing's own rule (`pr_threads`), against the refined examples the route built
from it. A PR the sizing scores zero (bot or AI author) still contributes its examples, so the
rate times the sizing's thread total projects the examples the route would build. The interval is
a percentile bootstrap over PRs of the ratio.

    GITHUB_TOKEN=$(gh auth token) \\
        uv run --no-sync --no-active python scripts/github_conversion.py \\
        --org openjdk --month 2024-11 --root ../../sphragis-data-local/github-pilot/gerrit \\
        --out datasets/results/github-conversion-openjdk.json
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path

from sphragis.corpus.github import GITHUB_ORGS
from sphragis.corpus.github_api import GitHubAPI
from sphragis.corpus.storage import snapshot_path, snapshot_record_path
from sphragis.provenance import provenance_header

sys.path.insert(0, str(Path(__file__).resolve().parent))
from github_sizing import PAGE, is_bot, pr_threads  # noqa: E402

# Bootstrap replicates; the interval is a percentile interval at 95%.
RESAMPLES = 10_000
SEED = 7

_COMMITS = """query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) {
    commits(first: 100) { nodes { commit { committedDate } } }
  } }
}"""

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--org", required=True, choices=sorted(GITHUB_ORGS))
parser.add_argument("--month", required=True, help="YYYY-MM, a month the route built")
parser.add_argument("--root", type=Path, required=True, help="root of the built corpus")
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--request-interval", type=float, default=0.5)


def commit_dates(api: GitHubAPI, repo: str, number: int) -> list[str]:
    """Committer dates of a PR's first page of commits, as the sizing read them."""
    owner, name = repo.split("/", 1)
    data = api.graphql(_COMMITS, {"owner": owner, "name": name, "number": number})
    nodes = data["repository"]["pullRequest"]["commits"]["nodes"]
    return [n["commit"]["committedDate"] for n in nodes]


def ratio_interval(prs: list[tuple[int, int]], rng: random.Random) -> tuple[float, float]:
    """95% percentile bootstrap over PRs of sum(examples) / sum(threads)."""
    reps = []
    for _ in range(RESAMPLES):
        sample = rng.choices(prs, k=len(prs))
        threads = sum(t for t, _ in sample)
        reps.append(sum(e for _, e in sample) / threads if threads else 0.0)
    reps.sort()
    return reps[int(0.025 * RESAMPLES)], reps[int(0.975 * RESAMPLES) - 1]


def main() -> None:
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("set GITHUB_TOKEN (e.g. GITHUB_TOKEN=$(gh auth token))")
    org = GITHUB_ORGS[args.org]
    record = json.loads(
        snapshot_record_path(snapshot_path(args.root, args.org, args.month)).read_text()
    )
    refined_path = args.root / args.org / "refined" / f"{args.month}.jsonl"
    built_path = args.root / args.org / "examples" / f"{args.month}.jsonl"
    refined = Counter(json.loads(line)["change_id"] for line in refined_path.open())
    built = Counter(json.loads(line)["change_id"] for line in built_path.open())

    api = GitHubAPI(token, min_interval=args.request_interval)
    year, number = map(int, args.month.split("-"))
    per_pr: list[dict] = []
    for day in range(1, calendar.monthrange(year, number)[1] + 1):
        for pr in api.merged_prs(org.owner, f"{args.month}-{day:02d}", qualifier=org.qualifier):
            key = f"{pr['repo']}#{pr['number']}"
            author = pr.get("user") or {}
            row = {"pr": key, "bot_or_ai_author": is_bot(author)}
            if row["bot_or_ai_author"]:
                row.update(threads=0, suggestion_threads=0, comments_over_page=False)
            else:
                comments = api.pr_comments(pr["repo"], int(pr["number"]))
                dates = commit_dates(api, pr["repo"], int(pr["number"]))
                counted = pr_threads(comments, dates, author)
                row.update(
                    threads=counted["threads"],
                    suggestion_threads=counted["suggestion_threads"],
                    comments_over_page=len(comments) > PAGE,
                )
            row.update(built=built.get(key, 0), refined=refined.get(key, 0))
            per_pr.append(row)

    listed = {r["pr"] for r in per_pr}
    if len(per_pr) != record["listed"]:
        raise SystemExit(
            f"listed {len(per_pr)} PRs, the snapshot listed {record['listed']}: not the same month"
        )
    if unlisted := (set(refined) | set(built)) - listed:
        raise SystemExit(f"examples from PRs the listing lacks: {sorted(unlisted)[:5]}")
    threads = sum(r["threads"] for r in per_pr)
    examples = sum(r["refined"] for r in per_pr)
    low, high = ratio_interval([(r["threads"], r["refined"]) for r in per_pr], random.Random(SEED))
    report = {
        "org": args.org,
        "month": args.month,
        "prs": len(per_pr),
        "bot_or_ai_authored_prs": sum(r["bot_or_ai_author"] for r in per_pr),
        "prs_with_comments_over_page": sum(r["comments_over_page"] for r in per_pr),
        "reviewer_threads": threads,
        "suggestion_threads": sum(r["suggestion_threads"] for r in per_pr),
        "built": sum(r["built"] for r in per_pr),
        "refined": examples,
        "refined_from_zero_thread_prs": sum(r["refined"] for r in per_pr if not r["threads"]),
        "rate": examples / threads if threads else 0.0,
        "rate_95": [low, high],
        "snapshot_record": {k: record[k] for k in ("listed", "rows", "github_rules")},
        "per_pr": per_pr,
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"{args.org} {args.month}: {examples} refined examples / {threads} reviewer threads = "
        f"{report['rate']:.3f} [{low:.3f}, {high:.3f}] over {len(per_pr)} PRs"
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
