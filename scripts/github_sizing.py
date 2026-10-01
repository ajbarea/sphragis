"""Confirmation sizing of GitHub organizations: a two-stage sample of PRs, one row per draw.

Per organization and month of the training window (2024-11 to 2025-08), K draws: a day uniformly at
random, then one merged (OpenJDK: integrated) PR uniformly at random among that day's. The
Hansen-Hurwitz total for a month is mean(D * N_day * y), unbiased for the month's total of y.
y per PR: reviewer-started inline threads (not the PR author, not a bot or AI reviewer, anchored to
a line, before the PR's last commit), split by whether the opening comment carries a ```suggestion
block (reviewer-written code). PRs authored by a bot or AI agent count zero and are tallied apart.
Every sampled PR is written as one JSON line (audit trail); `github_sizing_report.py` reads them.

    python3 scripts/github_sizing.py openjdk datasets/results/github-sizing-draws-openjdk.jsonl 20
"""

import calendar
import json
import random
import subprocess
import sys
import time

ORG, OUT, K = sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 20
MONTHS = [(2024, 11), (2024, 12)] + [(2025, m) for m in range(1, 9)]
QUAL = "is:pr is:closed label:integrated closed" if ORG == "openjdk" else "is:pr is:merged merged"
BOT_HINTS = (
    "[bot]",
    "-bot",
    "bot-",
    "robot",
    "bors",
    "highfive",
    "copilot",
    "coderabbit",
    "devin",
    "codex",
    "sweep",
    "gemini-code-assist",
    "sourcery",
    "qodo",
)
rng = random.Random(f"sphragis-gh-confirm-{ORG}")


def gh(path, *params):
    for _ in range(8):
        r = subprocess.run(
            ["gh", "api", "-X", "GET", path, *params], capture_output=True, text=True
        )
        if r.returncode == 0:
            return json.loads(r.stdout)
        if "rate limit" in r.stderr.lower() or "secondary" in r.stderr.lower() or "502" in r.stderr:
            time.sleep(65)
            continue
        raise RuntimeError(f"{path}: {r.stderr.strip()[:200]}")
    raise RuntimeError(f"{path}: rate limited")


DAY_QUERY = """query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest { number repository { nameWithOwner } author { login __typename } } }
  }
}"""


def day_prs(q):
    """Every PR the search returns for one day (GraphQL search caps a query at 1,000)."""
    prs, after, count = [], None, 0
    while True:
        params = ["-f", f"query={DAY_QUERY}", "-f", f"q={q}"] + (
            ["-f", f"after={after}"] if after else []
        )
        for _ in range(8):
            r = subprocess.run(["gh", "api", "graphql", *params], capture_output=True, text=True)
            if r.returncode == 0:
                break
            time.sleep(65)
        else:
            raise RuntimeError(f"graphql search failed: {r.stderr[:200]}")
        page = json.loads(r.stdout)["data"]["search"]
        count = page["issueCount"]
        prs += [n for n in page["nodes"] if n]
        if not page["pageInfo"]["hasNextPage"]:
            return count, prs
        after = page["pageInfo"]["endCursor"]


def is_bot(user):
    if not user:
        return True
    login = (user.get("login") or "").lower()
    return user.get("type") == "Bot" or any(h in login for h in BOT_HINTS)


with open(OUT, "a", buffering=1) as out:
    for year, month in MONTHS:
        days = calendar.monthrange(year, month)[1]
        for draw in range(K):
            day = rng.randint(1, days)
            date = f"{year}-{month:02d}-{day:02d}"
            q = f"org:{ORG} {QUAL}:{date}"
            n_day, prs = day_prs(q)
            row = {
                "org": ORG,
                "month": f"{year}-{month:02d}",
                "draw": draw,
                "day": date,
                "n_day": n_day,
                "days": days,
            }
            if n_day:
                pr = rng.choice(prs)
                repo = pr["repository"]["nameWithOwner"]
                author = {
                    "login": (pr.get("author") or {}).get("login"),
                    "type": "Bot"
                    if (pr.get("author") or {}).get("__typename") == "Bot"
                    else "User",
                }
                row.update(
                    repo=repo,
                    number=pr["number"],
                    frame_capped=n_day > len(prs),
                    ai_author=is_bot(author),
                )
                if not row["ai_author"]:
                    comments = gh(
                        f"repos/{repo}/pulls/{pr['number']}/comments", "-f", "per_page=100"
                    )
                    commits = gh(f"repos/{repo}/pulls/{pr['number']}/commits", "-f", "per_page=100")
                    last = max((c["commit"]["committer"]["date"] for c in commits), default="")
                    human, suggest, bot_threads, dirs = 0, 0, 0, []
                    for c in comments:
                        if c.get("in_reply_to_id") is not None:
                            continue
                        if c.get("line") is None and c.get("original_line") is None:
                            continue
                        if is_bot(c.get("user")):
                            bot_threads += 1
                            continue
                        if (c.get("user") or {}).get("login") == author.get("login"):
                            continue
                        if not c["created_at"] < last:
                            continue
                        if "```suggestion" in (c.get("body") or ""):
                            suggest += 1
                        else:
                            human += 1
                            dirs.append(c["path"].split("/")[0])
                    row.update(
                        threads=human,
                        suggestion_threads=suggest,
                        bot_threads=bot_threads,
                        top_dirs=dirs,
                    )
            out.write(json.dumps(row) + "\n")
    out.write(json.dumps({"org": ORG, "done": True}) + "\n")
