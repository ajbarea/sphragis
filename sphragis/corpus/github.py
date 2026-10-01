"""GitHub pull requests shaped as Gerrit changes, so the Gerrit build runs on them unchanged.

Design of record: `docs/superpowers/specs/2026-10-01-github-route-design.md`. A pull request is a
change, its k-th commit is patch set k, a review comment is an inline comment on the patch set of
its `original_commit_id`, and the diff between two patch sets is the compare API's patch for one
file converted into Gerrit's `content` blocks. `build.py`, `examples.py` and `refine.py` are not
touched, so the Gerrit corpora's rule digests stay as they are; this module's own digest
(`rules.GITHUB_RULES`) is recorded on GitHub months.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sphragis.corpus.github_api import Gone
from sphragis.corpus.rules import GITHUB_RULES
from sphragis.corpus.scrub import pseudonym, scrub

# Accounts that are AI coding agents or AI reviewers without GitHub's `Bot` type. Registered and
# versioned with the route: the population changes month to month, and a login on it is treated
# as a service user, so its comments are dropped and the PRs it opens are not collected.
AGENT_LOGINS = frozenset(
    {
        "copilot",
        "copilot-swe-agent",
        "devin-ai-integration",
        "codex",
        "chatgpt-codex-connector",
        "claude",
        "gemini-code-assist",
        "coderabbitai",
        "sourcery-ai",
        "qodo-merge-pro",
        "cursor",
    }
)
SERVICE_USER = "SERVICE_USER"
# A snapshot row's GitHub payload: the repository, the PR's commit shas in order, and its inline
# comments by file, carried in the row as a NoteDb row carries its own.
GITHUB_KEY = "github"

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
# A GitHub login (alphanumerics and single hyphens, at most 39) or a team, `@org/team`.
_MENTION = re.compile(r"(?<![\w`@])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:/[A-Za-z0-9_.-]+)?)")
_CODE = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`", re.S)


def is_service_account(user: Mapping[str, Any] | None) -> bool:
    """Whether an account is a bot or a registered AI agent."""
    if not user:
        return False
    login = str(user.get("login") or "").lower().removesuffix("[bot]")
    return user.get("type") == "Bot" or login in AGENT_LOGINS


def account(user: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """A GitHub user as a Gerrit account: the numeric id, and `SERVICE_USER` for a bot or agent.

    The login and any other identity field are left out, and `scrub` pseudonymises the id, so
    the author filter compares pseudonyms as it does on Gerrit.
    """
    if not user or user.get("id") is None:
        return None
    shaped: dict[str, Any] = {"_account_id": user["id"]}
    if is_service_account(user):
        shaped["tags"] = [SERVICE_USER]
    return shaped


def scrub_mentions(text: str, salt: str) -> str:
    """`@login` and `@org/team` in prose replaced by their pseudonyms; code spans left alone."""

    def prose(segment: str) -> str:
        return _MENTION.sub(lambda m: "@" + pseudonym(m.group(1).lower(), salt), segment)

    out, last = [], 0
    for code in _CODE.finditer(text):
        out += [prose(text[last : code.start()]), code.group(0)]
        last = code.end()
    out.append(prose(text[last:]))
    return "".join(out)


def diff_from_patch(patch: str) -> dict[str, list[dict[str, list[str]]]]:
    """A unified patch for one file as Gerrit `content` blocks.

    Each hunk keeps its own context lines as `ab` blocks beside its changes. The unchanged lines
    between hunks (and before the first) become an `ab` block of the right length so line numbers
    accumulate as on Gerrit; `examples._context` reads only the block next to a change, which is
    always a hunk's real context, so that padding is never read as text.
    """
    content: list[dict[str, list[str]]] = []
    next_old = 1

    def block(kind: str) -> dict[str, list[str]]:
        """The block a line of `kind` joins: the open one, or a new one."""
        last = content[-1] if content else None
        if kind == "ab":
            if last is not None and set(last) == {"ab"}:
                return last
            content.append({"ab": []})
        else:
            if last is not None and "ab" not in last:
                return last
            content.append({"a": [], "b": []})
        return content[-1]

    for raw in patch.splitlines():
        header = _HUNK_HEADER.match(raw)
        if header:
            old_start = int(header.group(1))
            if old_start > next_old:
                content.append({"ab": [""] * (old_start - next_old), "_padding": []})
            next_old = old_start
        elif raw.startswith(" "):
            block("ab")["ab"].append(raw[1:])
            next_old += 1
        elif raw.startswith("-"):
            block("a")["a"].append(raw[1:])
            next_old += 1
        elif raw.startswith("+"):
            block("b")["b"].append(raw[1:])
    for entry in content:
        entry.pop("_padding", None)
    return {"content": content}


def _gerrit_time(iso: str) -> str:
    """GitHub's `2025-01-15T10:20:30Z` as Gerrit's `2025-01-15 10:20:30.000000000`."""
    return iso.replace("T", " ").removesuffix("Z") + ".000000000"


def change_from_pr(
    repo: str,
    pr: Mapping[str, Any],
    commits: Sequence[Mapping[str, Any]],
    comments: Sequence[Mapping[str, Any]],
    *,
    project: str,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], dict[str, int]]:
    """A merged pull request as a Gerrit change, its inline comments by file, and what was dropped.

    Every review comment is kept, replies included, as Gerrit lists them; the build groups them by
    hunk. A comment on the base side of the diff (`side == "LEFT"`) has no line on its patch set.
    A comment whose commit is no longer in the PR's history (a force push) cannot be paired with a
    successor, so it is counted `rewritten_history` and left out.
    """
    patch_set = {commit["sha"]: index for index, commit in enumerate(commits, start=1)}
    change = {
        "_number": int(pr["number"]),
        "change_id": f"{repo}#{pr['number']}",
        "project": project,
        "created": _gerrit_time(str(pr["created_at"])),
        "owner": account(pr.get("user")),
        "revisions": {sha: {"_number": number} for sha, number in patch_set.items()},
    }
    by_file: dict[str, list[dict[str, Any]]] = {}
    dropped = {"rewritten_history": 0}
    for comment in comments:
        number = patch_set.get(str(comment.get("original_commit_id")))
        if number is None:
            dropped["rewritten_history"] += 1
            continue
        line = comment.get("original_line") if comment.get("side", "RIGHT") == "RIGHT" else None
        by_file.setdefault(str(comment["path"]), []).append(
            {
                "author": account(comment.get("user")),
                "message": str(comment.get("body") or ""),
                "patch_set": number,
                "line": line,
            }
        )
    return change, by_file, dropped


def row_from_pr(
    repo: str,
    pr: Mapping[str, Any],
    commits: Sequence[Mapping[str, Any]],
    comments: Sequence[Mapping[str, Any]],
    *,
    project: str,
    salt: str,
) -> dict[str, Any]:
    """One snapshot row, scrubbed before it reaches disk: the change, and its comments carried."""
    change, by_file, dropped = change_from_pr(repo, pr, commits, comments, project=project)
    row = scrub(change, salt)
    carried = scrub(by_file, salt)
    for file_comments in carried.values():
        for comment in file_comments:
            comment["message"] = scrub_mentions(comment["message"], salt)
    row[GITHUB_KEY] = {
        "repo": repo,
        "shas": [str(commit["sha"]) for commit in commits],
        "comments": carried,
        **dropped,
    }
    return row


def github_fetchers(
    row: Mapping[str, Any],
    compare_files: Callable[[str, str, str], Mapping[str, str | None]],
) -> tuple[
    Callable[[int], Mapping[str, Sequence[Mapping[str, Any]]]],
    Callable[[int, int, str, int], Mapping[str, Any]],
]:
    """`build`'s two fetchers for a GitHub row: comments from the row, diffs from the compare API.

    A Gerrit patch set is the author's whole next revision; a GitHub commit is one increment, and
    the fix to a commented file can come commits later. So the diff for a comment on patch set k
    runs from k to the first later patch set whose compare against k changes that file: the
    author's first response to it. When no later one does, the file was not changed, which on
    Gerrit is a diff with no hunk, so an empty diff is returned and the build counts
    `no_anchored_hunk`. A patch GitHub omits (a file too large) raises, counted `diff_error`.
    `compare_files(repo, base_sha, head_sha)` is called once a commit pair.
    """
    carried = row[GITHUB_KEY]
    shas: list[str] = carried["shas"]
    pairs: dict[tuple[str, str], Mapping[str, str | None]] = {}

    def files(base: str, head: str) -> Mapping[str, str | None]:
        if (base, head) not in pairs:
            pairs[base, head] = compare_files(carried["repo"], base, head)
        return pairs[base, head]

    def comments(number: int) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        return carried["comments"]

    def diff(number: int, revision: int, path: str, base: int) -> Mapping[str, Any]:
        for head in shas[revision - 1 :]:
            changed = files(shas[base - 1], head)
            if path in changed:
                patch = changed[path]
                if patch is None:
                    raise LookupError(f"GitHub omits the patch of {path} from {base}")
                return diff_from_patch(patch)
        return {"content": []}

    return comments, diff


@dataclass(frozen=True)
class GitHubOrg:
    """A registered GitHub organization: its owner, which PRs count, and what a project is."""

    owner: str
    # The search qualifier ending in the date field a month applies to.
    qualifier: str = "is:merged merged"
    # Repositories whose projects are their top-level directories (a monorepo), not the repository.
    monorepos: tuple[str, ...] = ()


# Candidates sized on 2026-10-01 (research log, "The confirmation pass"). GitHub's Acceptable Use
# Policies allow research use of public information when the resulting publications are open
# access; every publication using these corpora is posted to arXiv and published open access.
GITHUB_ORGS = {
    "apache": GitHubOrg("apache"),
    "llvm": GitHubOrg("llvm", monorepos=("llvm/llvm-project",)),
    "dotnet": GitHubOrg("dotnet"),
    "grafana": GitHubOrg("grafana"),
    "openjdk": GitHubOrg("openjdk", qualifier="is:closed label:integrated closed"),
    "hashicorp": GitHubOrg("hashicorp"),
}


class PullRequestSource(Protocol):
    def merged_prs(self, owner: str, day: str, *, qualifier: str) -> list[dict[str, Any]]: ...
    def pr_comments(self, repo: str, number: int) -> list[dict[str, Any]]: ...
    def pr_heads(self, repo: str, number: int) -> list[dict[str, Any]]: ...
    def pr_files(self, repo: str, number: int) -> list[dict[str, Any]]: ...


def project_of(org: GitHubOrg, repo: str, files: Sequence[Mapping[str, Any]]) -> str:
    """The repository, or in a monorepo `repo:<top directory with the most changed lines>`."""
    if repo not in org.monorepos:
        return repo
    lines: dict[str, int] = {}
    for f in files:
        top = str(f["filename"]).split("/", 1)[0]
        lines[top] = lines.get(top, 0) + int(f.get("changes", 0))
    return f"{repo}:{max(sorted(lines), key=lines.__getitem__)}" if lines else repo


def collect_month(
    name: str, month: str, api: PullRequestSource, *, salt: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every PR one registered organization merged in `month`, as scrubbed snapshot rows.

    A PR's patch sets are every commit that was ever its head (`pr_heads`), force-pushed ones
    included, so a comment on an amended commit keeps its successor. PRs opened by a bot or AI
    agent are not collected; PRs GitHub answers 404 for are withdrawn since they were listed. Both
    are counted in the month's record, as is every comment whose commit the timeline lacks.
    """
    if name not in GITHUB_ORGS:
        raise SystemExit(f"{name} is not a registered GitHub organization ({sorted(GITHUB_ORGS)})")
    org = GITHUB_ORGS[name]
    year, number = map(int, month.split("-"))
    rows: list[dict[str, Any]] = []
    counts = {"listed": 0, "agent_authored": 0, "withdrawn": 0}
    rewritten = 0
    for day in range(1, calendar.monthrange(year, number)[1] + 1):
        for pr in api.merged_prs(org.owner, f"{month}-{day:02d}", qualifier=org.qualifier):
            counts["listed"] += 1
            if is_service_account(pr.get("user")):
                counts["agent_authored"] += 1
                continue
            repo = str(pr["repo"])
            try:
                comments = api.pr_comments(repo, int(pr["number"]))
                heads = api.pr_heads(repo, int(pr["number"]))
                files = api.pr_files(repo, int(pr["number"])) if repo in org.monorepos else []
            except Gone:
                counts["withdrawn"] += 1
                continue
            row = row_from_pr(
                repo, pr, heads, comments, project=project_of(org, repo, files), salt=salt
            )
            rewritten += row[GITHUB_KEY]["rewritten_history"]
            rows.append(row)
    record = {
        "route": "github",
        "org": name,
        "owner": org.owner,
        "qualifier": org.qualifier,
        "month": month,
        **counts,
        "rewritten_history": rewritten,
        "github_rules": GITHUB_RULES,
    }
    return rows, record
