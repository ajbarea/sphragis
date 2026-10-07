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
        --org openjdk --month 2024-11 \\
        --root ~/ajsoftworks/sphragis-data-local/github-pilot/gerrit \\
        --draws datasets/results/github-sizing-draws-openjdk.jsonl \\
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
from sphragis.corpus.github_api import GitHubAPI, Gone
from sphragis.corpus.storage import read_snapshot_record, refused_snapshot, snapshot_path
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
parser.add_argument("--draws", type=Path, required=True, help="the organization's sizing draws")
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--request-interval", type=float, default=0.5)


def commit_dates(api: GitHubAPI, repo: str, number: int) -> list[str]:
    """Committer dates of a PR's first page of commits, as the sizing read them."""
    owner, name = repo.split("/", 1)
    data = api.graphql(_COMMITS, {"owner": owner, "name": name, "number": number})
    pr = (data.get("repository") or {}).get("pullRequest")
    if pr is None:
        raise Gone(f"{repo}#{number}: no pull request")
    return [n["commit"]["committedDate"] for n in pr["commits"]["nodes"]]


def census(api: GitHubAPI, name: str, month: str) -> list[dict]:
    """Every PR the route lists for the month, with the sizing's per-PR counts.

    A PR GitHub no longer answers for is kept with `gone` and no counts.
    """
    org = GITHUB_ORGS[name]
    year, number = map(int, month.split("-"))
    per_pr: list[dict] = []
    for day in range(1, calendar.monthrange(year, number)[1] + 1):
        for pr in api.merged_prs(org.owner, f"{month}-{day:02d}", qualifier=org.qualifier):
            author = pr.get("user") or {}
            row = {"pr": f"{pr['repo']}#{pr['number']}", "bot_or_ai_author": is_bot(author)}
            if row["bot_or_ai_author"]:
                row.update(threads=0, suggestion_threads=0, comments_over_page=False)
            else:
                try:
                    comments = api.pr_comments(pr["repo"], int(pr["number"]))
                    dates = commit_dates(api, pr["repo"], int(pr["number"]))
                except Gone:
                    per_pr.append({**row, "gone": True})
                    continue
                counted = pr_threads(comments, dates, author)
                row.update(
                    threads=counted["threads"],
                    suggestion_threads=counted["suggestion_threads"],
                    comments_over_page=len(comments) > PAGE,
                )
            per_pr.append(row)
    return per_pr


def ratio_interval(prs: list[tuple[int, int]], rng: random.Random) -> tuple[float, float]:
    """95% percentile bootstrap over PRs of sum(examples) / sum(threads).

    A resample with no threads has no ratio and is drawn again.
    """
    reps: list[float] = []
    while len(reps) < RESAMPLES:
        sample = rng.choices(prs, k=len(prs))
        threads = sum(t for t, _ in sample)
        if threads:
            reps.append(sum(e for _, e in sample) / threads)
    reps.sort()
    return reps[int(0.025 * RESAMPLES)], reps[int(0.975 * RESAMPLES) - 1]


def summarize(
    per_pr: list[dict],
    record: dict,
    built: Counter,
    refined: Counter,
    draws: list[dict],
) -> dict:
    """The month's conversion over the PRs that count, or a refusal naming why it cannot be read.

    A PR the route failed to collect, or that is gone at the census, counts on neither side: its
    examples are unknown. A PR withdrawn at collection keeps its threads, as the sizing counted
    them and the route can build nothing from it.
    """
    listed = {r["pr"] for r in per_pr}
    if len(per_pr) != record["listed"]:
        raise SystemExit(
            f"listed {len(per_pr)} PRs, the snapshot listed {record['listed']}: not the same month"
        )
    if unlisted := (set(refined) | set(built)) - listed:
        raise SystemExit(f"examples from PRs the listing lacks: {sorted(unlisted)[:5]}")
    failures = record.get("failures", [])
    if record.get("failed", 0) > len(failures):
        raise SystemExit(f"{record['failed']} failed PRs, {len(failures)} named: rerun the fetch")
    failed = {f.split(":", 1)[0] for f in failures}
    counted = [r for r in per_pr if r["pr"] not in failed and not r.get("gone")]
    for r in counted:
        r.update(built=built.get(r["pr"], 0), refined=refined.get(r["pr"], 0))
    threads = sum(r["threads"] for r in counted)
    if not threads:
        raise SystemExit("no reviewer threads in the month: no conversion to read")
    examples = sum(r["refined"] for r in counted)
    low, high = ratio_interval([(r["threads"], r["refined"]) for r in counted], random.Random(SEED))
    by_pr = {r["pr"]: r for r in counted}
    recounts = [
        (d, by_pr[f"{d['repo']}#{d['number']}"])
        for d in draws
        if "threads" in d and f"{d['repo']}#{d['number']}" in by_pr
    ]
    return {
        "prs": len(per_pr),
        "prs_counted": len(counted),
        "prs_failed": len(failed & listed),
        "prs_gone": sum(bool(r.get("gone")) for r in per_pr),
        "bot_or_ai_authored_prs": sum(r["bot_or_ai_author"] for r in per_pr),
        "prs_with_comments_over_page": sum(r["comments_over_page"] for r in counted),
        "reviewer_threads": threads,
        "suggestion_threads": sum(r["suggestion_threads"] for r in counted),
        "built": sum(r["built"] for r in counted),
        "refined": examples,
        "refined_from_zero_thread_prs": sum(r["refined"] for r in counted if not r["threads"]),
        "rate": examples / threads,
        "rate_95": [low, high],
        # The sizing's draws of this month, counted again here: the rule and the data agree.
        "draws_recounted": len(recounts),
        "draws_agreeing": sum(
            (d["threads"], d["suggestion_threads"]) == (r["threads"], r["suggestion_threads"])
            for d, r in recounts
        ),
        "snapshot_record": {k: record[k] for k in ("listed", "rows", "failed", "github_rules")},
        "per_pr": per_pr,
    }


def main() -> None:
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("set GITHUB_TOKEN (e.g. GITHUB_TOKEN=$(gh auth token))")
    snapshot = snapshot_path(args.root, args.org, args.month)
    refused = refused_snapshot(snapshot)
    record = read_snapshot_record(snapshot)
    if refused or record is None:
        raise SystemExit(f"{snapshot}: {refused or 'no readable record'}")
    built, refined = (
        Counter(json.loads(line)["change_id"] for line in (args.root / args.org / d / f).open())
        for d, f in (("examples", f"{args.month}.jsonl"), ("refined", f"{args.month}.jsonl"))
    )
    draws = [
        row
        for row in map(json.loads, args.draws.read_text().splitlines())
        if row.get("month") == args.month
    ]
    api = GitHubAPI(token, min_interval=args.request_interval)
    per_pr = census(api, args.org, args.month)
    report = {
        "org": args.org,
        "month": args.month,
        **summarize(per_pr, record, built, refined, draws),
        "provenance": provenance_header(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    low, high = report["rate_95"]
    print(
        f"{args.org} {args.month}: {report['refined']} refined examples / "
        f"{report['reviewer_threads']} reviewer threads = {report['rate']:.3f} "
        f"[{low:.3f}, {high:.3f}] over {report['prs_counted']} PRs; draws recounted "
        f"{report['draws_agreeing']} of {report['draws_recounted']} agree"
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
