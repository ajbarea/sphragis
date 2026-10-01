"""GitHub pull requests shaped as Gerrit changes, so the Gerrit build runs on them unchanged.

Design of record: `docs/superpowers/specs/2026-10-01-github-route-design.md`. A pull request is a
change, its k-th commit is patch set k, a review comment is an inline comment on the patch set of
its `original_commit_id`, and the diff between two patch sets is the compare API's patch for one
file converted into Gerrit's `content` blocks. `build.py`, `examples.py` and `refine.py` are not
touched, so the Gerrit corpora's rule digests stay as they are; this module's own digest
(`rules.GITHUB_RULES`) is recorded on GitHub months.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sphragis.corpus.scrub import pseudonym

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


def diff_fetcher(
    commits: Sequence[Mapping[str, Any]],
    compare: Callable[[str, str, str], str | None],
) -> Callable[[int, int, str, int], Mapping[str, Any]]:
    """`build`'s diff fetcher for one PR: patch set `base` to `revision` of one file.

    `compare(base_sha, head_sha, path)` returns that file's unified patch, or None when GitHub
    omits it (a file too large to diff); None raises, which the build counts as `diff_error`.
    """
    shas = [str(commit["sha"]) for commit in commits]

    def fetch(number: int, revision: int, path: str, base: int) -> Mapping[str, Any]:
        patch = compare(shas[base - 1], shas[revision - 1], path)
        if patch is None:
            raise LookupError(f"no patch of {path} from patch set {base} to {revision}")
        return diff_from_patch(patch)

    return fetch
