"""GitHub pull requests shaped as Gerrit changes, so the Gerrit build runs on them unchanged.

Design of record: `docs/superpowers/specs/2026-10-01-github-route-design.md`. A pull request is a
change; every commit ever on it (force-pushed ones recovered from its timeline), in commit-time
order, is a patch set; a review comment is an inline comment on the patch set of its
`original_commit_id`. Each diff the build will ask for is computed once, at collection, between
the exact file versions (the file at the commented commit, and at the first commit made after the
comment that changes it), with git's own diff, and carried in the row, as a NoteDb row carries its
own. The build then runs offline. `build.py`, `examples.py` and `refine.py` are not touched, so the
Gerrit corpora's digests stay as they are; this route's own digest (`rules.GITHUB_RULES`) covers
this module and the client it collects with.
"""

from __future__ import annotations

import calendar
import json
import re
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sphragis.corpus.github_api import Gone, order_commits
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
# A snapshot row's GitHub payload: the repository, its inline comments by file, and the diff
# for each commented (patch set, file), keyed as `diff_key` does.
GITHUB_KEY = "github"

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
# A GitHub login (alphanumerics and single hyphens, at most 39) or a team, `@org/team`.
_MENTION = re.compile(r"(?<![\w`@])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:/[A-Za-z0-9_.-]+)?)")
_LOGIN = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
# Links that name an account: a profile or repository, raw files, and GitHub Pages.
_PROFILE = re.compile(r"((?:raw\.githubusercontent|github)\.com/)(" + _LOGIN + ")", re.I)
_PAGES = re.compile(r"(?<![\w.-])(" + _LOGIN + r")(\.github\.io)", re.I)
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


def scrub_mentions(
    text: str, salt: str, *, logins: Iterable[str] = (), keep_owners: Iterable[str] = ()
) -> str:
    """Account references in comment text replaced by their pseudonyms.

    Replaced wherever they appear, code spans and suggestion blocks included: a known
    participant's `@login` (the PR author's, each commenter's), and links that name an account
    (`github.com/<owner>`, `raw.githubusercontent.com/<owner>`, `<owner>.github.io`) unless the
    owner is the organization itself (`keep_owners`). In prose, any other `@login` or `@org/team`
    is replaced too; in code it is left, since `@property` there is a decorator. A login written
    as a bare word is left, as names in prose are on the Gerrit routes, since a login can be an
    ordinary word ("fix") and replacing it would corrupt the comment.
    """
    keep = {owner.lower() for owner in keep_owners}

    def alias(name: str) -> str:
        return pseudonym(name.lower(), salt)

    known = sorted({login for login in logins if login}, key=len, reverse=True)
    if known:
        names = "|".join(re.escape(login) for login in known)
        participant = re.compile(r"(?<![\w@-])@(" + names + r")(?![\w-])", re.I)
        text = participant.sub(lambda m: "@" + alias(m.group(1)), text)
    text = _PROFILE.sub(
        lambda m: m.group(0) if m.group(2).lower() in keep else m.group(1) + alias(m.group(2)),
        text,
    )
    text = _PAGES.sub(
        lambda m: m.group(0) if m.group(1).lower() in keep else alias(m.group(1)) + m.group(2),
        text,
    )

    def prose(segment: str) -> str:
        return _MENTION.sub(lambda m: "@" + alias(m.group(1)), segment)

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
    always a hunk's real context, so that padding is never read as text. Lines split on `\\n` only:
    a form feed or line separator inside a source line is part of that line, as git counts it.
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

    for raw in patch.split("\n"):
        line = raw.removesuffix("\r")
        header = _HUNK_HEADER.match(line)
        if header:
            old_start = int(header.group(1))
            if old_start > next_old:
                content.append({"ab": [""] * (old_start - next_old), "_padding": []})
            next_old = old_start
        elif line.startswith(" "):
            block("ab")["ab"].append(line[1:])
            next_old += 1
        elif line.startswith("-"):
            block("a")["a"].append(line[1:])
            next_old += 1
        elif line.startswith("+"):
            block("b")["b"].append(line[1:])
    for entry in content:
        entry.pop("_padding", None)
    return {"content": content}


def unified_patch(before: str, after: str) -> str:
    """git's own diff of two file versions, three lines of context, from its first hunk on."""
    with tempfile.TemporaryDirectory() as scratch:
        old, new = Path(scratch) / "a", Path(scratch) / "b"
        old.write_text(before, encoding="utf-8", newline="")
        new.write_text(after, encoding="utf-8", newline="")
        result = subprocess.run(
            ["git", "diff", "--no-index", "--no-color", "--no-ext-diff", "-U3", str(old), str(new)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"git diff failed: {result.stderr[:200]}")
    start = result.stdout.find("\n@@")
    return "" if start < 0 else result.stdout[start + 1 :]


def diff_key(base: int, path: str) -> str:
    """Where a row keeps the diff of `path` from patch set `base` to its successor."""
    return f"{base}:{path}"


def _gerrit_time(iso: str) -> str:
    """GitHub's `2025-01-15T10:20:30Z` as Gerrit's `2025-01-15 10:20:30.000000000`."""
    return iso.replace("T", " ").removesuffix("Z") + ".000000000"


def change_from_pr(
    repo: str,
    pr: Mapping[str, Any],
    heads: Sequence[Mapping[str, Any]],
    comments: Sequence[Mapping[str, Any]],
    *,
    project: str,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], dict[str, int]]:
    """A merged pull request as a Gerrit change, its inline comments by file, and what was dropped.

    `heads` is every commit ever on the PR in commit-time order (`GitHubAPI.pr_heads`); patch set
    k is the k-th. Every review comment is kept, replies included, as Gerrit lists them; the build
    groups them by hunk. A comment on the base side of the diff (`side == "LEFT"`) has no line on
    its patch set. A comment on a commit the timeline lacks cannot be placed, so it is counted
    `rewritten_history` and left out.
    """
    patch_set = {str(head["sha"]): index for index, head in enumerate(heads, start=1)}
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
                "created": str(comment.get("created_at") or ""),
            }
        )
    return change, by_file, dropped


class FileSource(Protocol):
    def file_oids(self, repo: str, path: str, shas: list[str]) -> dict[str, str | None]: ...
    def blob_text(self, repo: str, oid: str) -> str | None: ...
    def fork_point(self, repo: str, base_tip: str, sha: str) -> str: ...


# What can stop a commented file having a diff the build can read, counted per comment.
DIFF_DROPS = (
    "file_absent",
    "outdated_view",
    "file_gone",
    "upstream_change",
    "text_unavailable",
    "object_gone",
)


def successor_diffs(
    repo: str,
    base_tip: str,
    heads: Sequence[Mapping[str, Any]],
    by_file: dict[str, list[dict[str, Any]]],
    api: FileSource,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Each commented (patch set, file)'s diff to the author's first later change of that file.

    A Gerrit patch set is the author's whole next revision; a GitHub commit is one increment, so
    the successor of patch set k for a file is the first commit made after the earliest comment on
    it whose version of the file differs from k's. Both versions are fetched by commit and path and
    diffed with git, so the diff is between exactly those two files whatever the commits' ancestry.
    When no later commit changes the file, the diff is empty, as Gerrit's is for an untouched file
    (`no_anchored_hunk` in the build).

    Comments that cannot have a clean diff are removed from `by_file` and counted:
    `file_absent` (no file at k); `outdated_view` (the file changed after k but before the
    comment, so the reviewer read a superseded version and the diff would carry edits made before
    the comment); `file_gone` (absent at the successor, as by a rename); `upstream_change` (a merge
    commit between k and the successor, or the base branch's own version of the file differs
    between the two commits' fork points, so the successor carries upstream edits: the rebase
    guard the Gerrit routes apply through a revision's kind); `text_unavailable` (binary or too
    large); `object_gone` (GitHub no longer holds a commit or blob it needs).
    """
    shas = [str(head["sha"]) for head in heads]
    times = [str(head.get("committed_at") or "") for head in heads]
    merges = [len(head.get("parents") or []) > 1 for head in heads]
    diffs: dict[str, Any] = {}
    dropped = dict.fromkeys(DIFF_DROPS, 0)
    forks: dict[str, str] = {}

    def fork(sha: str) -> str:
        if sha not in forks:
            forks[sha] = api.fork_point(repo, base_tip, sha)
        return forks[sha]

    for path, file_comments in list(by_file.items()):
        groups: dict[int, list[dict[str, Any]]] = {}
        for comment in file_comments:
            groups.setdefault(int(comment["patch_set"]), []).append(comment)
        kept: list[dict[str, Any]] = []
        oids: dict[str, str | None] | None = None
        for k, group in sorted(groups.items()):
            if all(comment["line"] is None for comment in group) or k >= len(shas):
                kept += group  # no anchor, or no later commit: the build counts these
                continue
            try:
                if oids is None:
                    oids = api.file_oids(repo, path, shas)
                since = min(str(comment.get("created") or "") for comment in group)
                reason, diff = _successor_diff(
                    repo, k, shas, times, merges, since, oids, api, fork, path
                )
            except Gone:
                reason, diff = "object_gone", None
            if reason:
                dropped[reason] += len(group)
                continue
            diffs[diff_key(k, path)] = diff
            kept += group
        if kept:
            by_file[path] = kept
        else:
            del by_file[path]
    return diffs, dropped


def _successor_diff(
    repo: str,
    k: int,
    shas: list[str],
    times: list[str],
    merges: list[bool],
    since: str,
    oids: Mapping[str, str | None],
    api: FileSource,
    fork: Callable[[str], str],
    path: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """A drop reason, or the diff of `path` from patch set k to its first later change."""
    commented = shas[k - 1]
    before_oid = oids.get(commented)
    if before_oid is None:
        return "file_absent", None
    later = range(k, len(shas))
    if any(times[i] <= since and oids.get(shas[i]) != before_oid for i in later):
        return "outdated_view", None
    j = next((i for i in later if times[i] > since and oids.get(shas[i]) != before_oid), None)
    if j is None:
        return None, {"content": []}
    after_oid = oids.get(shas[j])
    if after_oid is None:
        return "file_gone", None
    if any(merges[i] for i in range(k, j + 1)):
        return "upstream_change", None
    older, newer = fork(commented), fork(shas[j])
    if older != newer:
        upstream = api.file_oids(repo, path, [older, newer])
        if upstream.get(older) != upstream.get(newer):
            return "upstream_change", None
    before, after = api.blob_text(repo, before_oid), api.blob_text(repo, after_oid)
    if before is None or after is None:
        return "text_unavailable", None
    return None, diff_from_patch(unified_patch(before, after))


def row_from_pr(
    repo: str,
    pr: Mapping[str, Any],
    heads: Sequence[Mapping[str, Any]],
    comments: Sequence[Mapping[str, Any]],
    *,
    project: str,
    salt: str,
    api: FileSource,
    keep_owners: Iterable[str] = (),
) -> dict[str, Any]:
    """One snapshot row, scrubbed before it reaches disk: the change, its comments and diffs."""
    change, by_file, dropped = change_from_pr(repo, pr, heads, comments, project=project)
    base_tip = str(pr.get("base_tip") or pr.get("base_ref") or "")
    diffs, diff_dropped = successor_diffs(repo, base_tip, heads, by_file, api)
    logins = [str((pr.get("user") or {}).get("login") or "")] + [
        str((c.get("user") or {}).get("login") or "") for c in comments
    ]
    row = scrub(change, salt)
    carried = scrub(by_file, salt)
    for file_comments in carried.values():
        for comment in file_comments:
            comment["message"] = scrub_mentions(
                comment["message"], salt, logins=logins, keep_owners=keep_owners
            )
    row[GITHUB_KEY] = {"repo": repo, "comments": carried, "diffs": diffs, **dropped, **diff_dropped}
    return row


def github_fetchers(
    row: Mapping[str, Any],
) -> tuple[
    Callable[[int], Mapping[str, Sequence[Mapping[str, Any]]]],
    Callable[[int, int, str, int], Mapping[str, Any]],
]:
    """`build`'s two fetchers, answered offline from what a GitHub row carries.

    A diff the row lacks raises, which `build_from_change` counts as `diff_error`.
    """
    carried = row[GITHUB_KEY]

    def comments(number: int) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        return carried["comments"]

    def diff(number: int, revision: int, path: str, base: int) -> Mapping[str, Any]:
        entry = carried["diffs"].get(diff_key(base, path)) if revision == base + 1 else None
        if entry is None:
            raise KeyError(f"no diff of {path} from patch set {base}")
        return entry

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


class PullRequestSource(FileSource, Protocol):
    def merged_prs(self, owner: str, day: str, *, qualifier: str) -> list[dict[str, Any]]: ...
    def commit_times(self, repo: str, shas: list[str]) -> list[dict[str, Any]]: ...
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


def with_commented_commits(
    repo: str,
    heads: Sequence[Mapping[str, Any]],
    comments: Sequence[Mapping[str, Any]],
    api: PullRequestSource,
) -> list[dict[str, Any]]:
    """The PR's commits with every commit a comment names, in commit-time order.

    The timeline lists the commits that were ever the PR's head and each force push's before and
    after commits, but not the other commits of an earlier push. A review comment names its own
    commit (`original_commit_id`), which GitHub still holds after a force push, so those commits
    are added by their commit time. A commit GitHub no longer holds stays out, and its comments
    are counted `rewritten_history`.
    """
    known = {str(head["sha"]) for head in heads}
    missing = sorted({str(c.get("original_commit_id")) for c in comments} - known - {"None"})
    found = api.commit_times(repo, missing) if missing else []
    return order_commits([dict(head) for head in heads] + found)


def has_inline_review(pr: Mapping[str, Any], comments: Sequence[Mapping[str, Any]]) -> bool:
    """Whether anyone but the PR's author and service accounts left a comment on a code line."""
    author = (pr.get("user") or {}).get("login")
    return any(
        (c.get("original_line") is not None or c.get("line") is not None)
        and not is_service_account(c.get("user"))
        and (c.get("user") or {}).get("login") != author
        for c in comments
    )


def collect_month(
    name: str,
    month: str,
    api: PullRequestSource,
    *,
    salt: str,
    checkpoint: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every PR one registered organization merged in `month`, as scrubbed snapshot rows.

    PRs opened by a bot or AI agent are not collected; a PR with no reviewer's inline comment
    costs one request and is counted (`no_inline_review`), since it can yield no example; PRs
    GitHub answers 404 for are withdrawn since they were listed; a PR whose requests still fail
    after the client's retries is `failed`, with its reason. All are counted in the month's
    record, as is every comment left out by `change_from_pr` or `successor_diffs`, by reason.

    With a `checkpoint`, each PR's outcome (and its row, already scrubbed) is appended as it
    finishes, and a rerun replays it rather than asking GitHub again, so a long month that fails
    partway keeps its work. Rows come back in change-id order, so a resumed month matches an
    uninterrupted one.
    """
    if name not in GITHUB_ORGS:
        raise SystemExit(f"{name} is not a registered GitHub organization ({sorted(GITHUB_ORGS)})")
    org = GITHUB_ORGS[name]
    year, number = map(int, month.split("-"))
    done: dict[str, dict[str, Any]] = {}
    if checkpoint is not None and checkpoint.is_file():
        for line in checkpoint.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                done[entry["key"]] = entry
    log = checkpoint.open("a", encoding="utf-8") if checkpoint is not None else None
    outcomes: list[dict[str, Any]] = []
    listed = 0
    try:
        for day in range(1, calendar.monthrange(year, number)[1] + 1):
            for pr in api.merged_prs(org.owner, f"{month}-{day:02d}", qualifier=org.qualifier):
                listed += 1
                key = f"{pr['repo']}#{pr['number']}"
                entry = done.get(key) or _collect_pr(org, pr, api, salt)
                if key not in done and log is not None:
                    log.write(json.dumps({"key": key, **entry}) + "\n")
                    log.flush()
                outcomes.append(entry)
    finally:
        if log is not None:
            log.close()
    rows = sorted(
        (e["row"] for e in outcomes if e["outcome"] == "row"), key=lambda r: r["change_id"]
    )
    counts = {
        outcome: sum(e["outcome"] == outcome for e in outcomes)
        for outcome in ("agent_authored", "no_inline_review", "withdrawn", "failed")
    }
    comment_drops = {
        reason: sum(row[GITHUB_KEY][reason] for row in rows)
        for reason in ("rewritten_history", *DIFF_DROPS)
    }
    record = {
        "route": "github",
        "org": name,
        "owner": org.owner,
        "qualifier": org.qualifier,
        "month": month,
        "listed": listed,
        **counts,
        **comment_drops,
        "failures": [e["failure"] for e in outcomes if e["outcome"] == "failed"][:50],
        "github_rules": GITHUB_RULES,
    }
    return rows, record


def _collect_pr(
    org: GitHubOrg, pr: Mapping[str, Any], api: PullRequestSource, salt: str
) -> dict[str, Any]:
    """One listed PR's outcome: its scrubbed row, or why it has none."""
    if is_service_account(pr.get("user")):
        return {"outcome": "agent_authored"}
    repo = str(pr["repo"])
    try:
        comments = api.pr_comments(repo, int(pr["number"]))
        if not has_inline_review(pr, comments):
            return {"outcome": "no_inline_review"}
        heads = with_commented_commits(repo, api.pr_heads(repo, int(pr["number"])), comments, api)
        files = api.pr_files(repo, int(pr["number"])) if repo in org.monorepos else []
        row = row_from_pr(
            repo,
            pr,
            heads,
            comments,
            project=project_of(org, repo, files),
            salt=salt,
            api=api,
            keep_owners=(org.owner,),
        )
    except Gone:
        return {"outcome": "withdrawn"}
    except RuntimeError as error:
        # The client has already retried; one PR's persistent error costs that PR, not the
        # month. Counted, with its reason, so a failed month is visible in its record.
        return {"outcome": "failed", "failure": f"{repo}#{pr['number']}: {str(error)[:160]}"}
    return {"outcome": "row", "row": row}
