"""GitHub REST and GraphQL access for the GitHub route: paced, paged, rate-limit aware.

Authenticated, serial requests only (GitHub's guidance against concurrent requests),
a floor between requests, the primary limit waited out until `x-ratelimit-reset`, a secondary limit
until its `retry-after`, server errors retried with backoff, and 404 raised as `Gone` without a
retry: on a pull request, a 404 means it was deleted or made private after it was listed.
The transport is injected, so the suite runs offline.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

API = "https://api.github.com"
# GitHub's search returns at most 1,000 results for one query; a window holding more is split.
SEARCH_CAP = 1000
_MAX_ATTEMPTS = 8
_STAMP = "%Y-%m-%dT%H:%M:%S"

Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict[str, str], str]]

_SEARCH = """query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest {
      number createdAt baseRefName repository { nameWithOwner }
      mergeCommit { parents(first: 1) { nodes { oid } } }
      author { login __typename ... on User { databaseId } ... on Bot { databaseId } }
    } }
  }
}"""


_TIMELINE = """query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) {
    timelineItems(itemTypes: [PULL_REQUEST_COMMIT, HEAD_REF_FORCE_PUSHED_EVENT],
                  first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { __typename
        ... on PullRequestCommit { commit { ...C } }
        ... on HeadRefForcePushedEvent { beforeCommit { ...C } afterCommit { ...C } }
      }
    }
  } }
}
fragment C on Commit { oid committedDate parents(first: 2) { nodes { oid } } }"""


class Gone(LookupError):
    """GitHub answered 404."""


def urllib_transport(timeout: float = 30.0) -> Transport:
    def send(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, dict[str, str], str]:
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, dict(response.headers), response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers or {}), error.read().decode(errors="replace")

    return send


class GitHubAPI:
    def __init__(
        self,
        token: str,
        *,
        transport: Transport | None = None,
        min_interval: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "sphragis-research (github.com/ajbarea/sphragis)",
        }
        self._send = transport or urllib_transport()
        self._min_interval, self._sleep, self._clock = min_interval, sleep, clock
        self._last = -float("inf")

    def _request(
        self, method: str, url: str, body: Mapping[str, Any] | None = None
    ) -> tuple[dict[str, str], Any]:
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(_MAX_ATTEMPTS):
            wait = self._last + self._min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()
            status, raw_headers, text = self._send(method, url, dict(self._headers), data)
            headers = {k.lower(): v for k, v in raw_headers.items()}
            if status == 200:
                return headers, json.loads(text)
            if status == 404:
                raise Gone(f"GitHub answered 404 for {url}")
            if status in (403, 429) and headers.get("x-ratelimit-remaining") == "0":
                self._sleep(max(float(headers.get("x-ratelimit-reset", 0)) - self._clock(), 0) + 1)
            elif status in (403, 429) and "retry-after" in headers:
                self._sleep(float(headers["retry-after"]))
            elif status >= 500:
                self._sleep(2.0**attempt)
            else:
                raise RuntimeError(f"GitHub answered {status} for {url}: {text[:200]}")
        raise RuntimeError(f"GitHub kept refusing {url} after {_MAX_ATTEMPTS} attempts")

    def graphql(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        """A GraphQL query's `data`, rate limits waited out, NOT_FOUND raised as `Gone`.

        GitHub reports an exhausted GraphQL budget with HTTP 200 and an `errors` entry of type
        RATE_LIMIT or RATE_LIMITED, so the status alone cannot be trusted.
        """
        for _ in range(_MAX_ATTEMPTS):
            headers, payload = self._request(
                "POST", f"{API}/graphql", {"query": query, "variables": dict(variables)}
            )
            errors = payload.get("errors") or []
            kinds = {str(e.get("type")) for e in errors}
            if kinds & {"RATE_LIMIT", "RATE_LIMITED"}:
                reset = float(headers.get("x-ratelimit-reset", self._clock() + 60))
                self._sleep(max(reset - self._clock(), 0) + 1)
                continue
            if "NOT_FOUND" in kinds:
                raise Gone(f"GraphQL NOT_FOUND: {errors[0].get('message', '')}")
            if errors:
                raise RuntimeError(f"GraphQL errors: {errors[:2]}")
            return payload["data"]
        raise RuntimeError(f"GraphQL rate limited after {_MAX_ATTEMPTS} attempts")

    def _pages(self, path: str, **params: Any) -> Iterator[Any]:
        url: str | None = f"{API}{path}?{urlencode({'per_page': 100, **params})}"
        while url:
            headers, payload = self._request("GET", url)
            yield from payload
            url = _next_link(headers.get("link", ""))

    def pr_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        """Every inline review comment on a PR, replies included."""
        return list(self._pages(f"/repos/{repo}/pulls/{number}/comments"))

    def pr_heads(self, repo: str, number: int) -> list[dict[str, Any]]:
        """Every commit ever on the PR, as `{"sha", "committed_at", "parents"}`, oldest first.

        The commit list holds only the final history; a force push (amend, rebase, squash) leaves
        the commits reviewers commented on out of it. The timeline keeps each force push's before
        and after commits, so they are recovered the way Gerrit keeps patch sets. They are ordered
        by commit time (`order_commits`), not timeline position: an amended commit can sit in the
        timeline before the force push that replaced the commit it amends.
        """
        owner, name = repo.split("/", 1)
        seen: dict[str, dict[str, Any]] = {}
        after: str | None = None
        while True:
            data = self.graphql(
                _TIMELINE, {"owner": owner, "name": name, "number": number, "after": after}
            )
            pull = (data.get("repository") or {}).get("pullRequest")
            if pull is None:
                raise Gone(f"no pull request {repo}#{number}")
            items = pull["timelineItems"]
            for node in items["nodes"]:
                keys = (
                    ("commit",)
                    if node.get("__typename") == "PullRequestCommit"
                    else ("beforeCommit", "afterCommit")
                )
                for key in keys:
                    commit = node.get(key) or {}
                    if commit.get("oid"):
                        seen.setdefault(commit["oid"], _commit_entry(commit))
            if not items["pageInfo"]["hasNextPage"]:
                return order_commits(list(seen.values()))
            after = items["pageInfo"]["endCursor"]

    def commit_times(self, repo: str, shas: list[str]) -> list[dict[str, Any]]:
        """Each commit GitHub still holds, as `pr_heads` gives them; one it lacks is left out."""
        owner, name = repo.split("/", 1)
        out: list[dict[str, Any]] = []
        for start in range(0, len(shas), 50):
            chunk = shas[start : start + 50]
            fields = " ".join(
                f"c{i}: object(oid: {json.dumps(sha)}) {{ ...on Commit {{ ...C }} }}"
                for i, sha in enumerate(chunk)
            )
            query = (
                "query($o: String!, $n: String!) { repository(owner: $o, name: $n) { %s } }"
                " fragment C on Commit { oid committedDate parents(first: 2) { nodes { oid } } }"
            )
            found = self.graphql(query % fields, {"o": owner, "n": name}).get("repository") or {}
            out += [_commit_entry(found[f"c{i}"]) for i in range(len(chunk)) if found.get(f"c{i}")]
        return out

    def file_oids(self, repo: str, path: str, shas: list[str]) -> dict[str, str | None]:
        """The blob id of `path` at each commit, or None where the file does not exist there."""
        owner, name = repo.split("/", 1)
        out: dict[str, str | None] = {}
        for start in range(0, len(shas), 50):
            chunk = shas[start : start + 50]
            blob = "{ ...on Blob { oid } }"
            fields = " ".join(
                f"c{i}: object(expression: {json.dumps(f'{sha}:{path}')}) {blob}"
                for i, sha in enumerate(chunk)
            )
            query = "query($o: String!, $n: String!) { repository(owner: $o, name: $n) { %s } }"
            data = self.graphql(query % fields, {"o": owner, "n": name})
            found = data.get("repository") or {}
            for i, sha in enumerate(chunk):
                out[sha] = (found.get(f"c{i}") or {}).get("oid")
        return out

    def blob_text(self, repo: str, oid: str) -> str | None:
        """A blob's text, or None when GitHub reports it binary or truncated."""
        owner, name = repo.split("/", 1)
        query = """query($o: String!, $n: String!, $id: GitObjectID!) {
          repository(owner: $o, name: $n) { object(oid: $id) {
            ... on Blob { text isBinary isTruncated } } } }"""
        blob = (
            self.graphql(query, {"o": owner, "n": name, "id": oid}).get("repository") or {}
        ).get("object") or {}
        if blob.get("isBinary") or blob.get("isTruncated") or blob.get("text") is None:
            return None
        return str(blob["text"])

    def fork_point(self, repo: str, base_tip: str, sha: str) -> str:
        """The merge base of `sha` with `base_tip`: the base branch as it was before the PR merged.

        `base_tip` is the merge (or squash) commit's first parent where there is one: after a
        merge commit every PR commit is reachable from the branch itself, so a merge base taken
        against the branch's current head would be the commit itself.
        """
        _, payload = self._request(
            "GET", f"{API}/repos/{repo}/compare/{base_tip}...{sha}?per_page=1"
        )
        return str(payload["merge_base_commit"]["sha"])

    def pr_files(self, repo: str, number: int) -> list[dict[str, Any]]:
        """A PR's changed files with their changed-line counts (GitHub lists at most 3,000)."""
        return list(self._pages(f"/repos/{repo}/pulls/{number}/files"))

    def _search(self, query: str) -> tuple[int, list[dict[str, Any]]]:
        nodes: list[dict[str, Any]] = []
        after: str | None = None
        while True:
            page = self.graphql(_SEARCH, {"q": query, "after": after})["search"]
            nodes += [n for n in page["nodes"] if n]
            if page["issueCount"] > SEARCH_CAP or not page["pageInfo"]["hasNextPage"]:
                return page["issueCount"], nodes
            after = page["pageInfo"]["endCursor"]

    def merged_prs(self, owner: str, day: str, *, qualifier: str) -> list[dict[str, Any]]:
        """Every PR `qualifier` selects on one UTC day, the window halved while over the cap.

        `qualifier` ends in the date field the window applies to: `is:merged merged`, or for
        OpenJDK, whose bot closes PRs it integrates, `is:closed label:integrated closed`.
        """
        start = datetime.strptime(f"{day}T00:00:00", _STAMP)
        return self._window(owner, qualifier, start, start + timedelta(seconds=86399))

    def _window(
        self, owner: str, qualifier: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        span = f"{start.strftime(_STAMP)}..{end.strftime(_STAMP)}"
        count, nodes = self._search(f"org:{owner} is:pr {qualifier}:{span}")
        if count > SEARCH_CAP and end > start:
            middle = start + (end - start) / 2
            middle = middle.replace(microsecond=0)
            return self._window(owner, qualifier, start, middle) + self._window(
                owner, qualifier, middle + timedelta(seconds=1), end
            )
        return [
            {
                "repo": n["repository"]["nameWithOwner"],
                "number": n["number"],
                "created_at": n["createdAt"],
                "base_ref": n.get("baseRefName"),
                "base_tip": next(
                    (
                        p["oid"]
                        for p in ((n.get("mergeCommit") or {}).get("parents") or {}).get(
                            "nodes", []
                        )
                    ),
                    None,
                ),
                "user": {
                    "id": (n.get("author") or {}).get("databaseId"),
                    "login": (n.get("author") or {}).get("login"),
                    "type": "Bot" if (n.get("author") or {}).get("__typename") == "Bot" else "User",
                },
            }
            for n in nodes
        ]


def _commit_entry(commit: Mapping[str, Any]) -> dict[str, Any]:
    parents = ((commit.get("parents") or {}).get("nodes")) or []
    return {
        "sha": str(commit["oid"]),
        "committed_at": str(commit.get("committedDate") or ""),
        "parents": [str(p["oid"]) for p in parents if p and p.get("oid")],
    }


def order_commits(commits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Commits by commit time, each parent before its children when they share a second.

    A rebase rewrites a series in one second, so time alone would leave their order to the sha.
    Within a second the commits are sorted topologically, ties by sha.
    """
    by_time: dict[str, list[dict[str, Any]]] = {}
    for commit in commits:
        by_time.setdefault(commit["committed_at"], []).append(commit)
    ordered: list[dict[str, Any]] = []
    for second in sorted(by_time):
        group = {c["sha"]: c for c in by_time[second]}
        waiting = {sha: {p for p in c.get("parents", []) if p in group} for sha, c in group.items()}
        while waiting:
            ready = sorted(sha for sha, parents in waiting.items() if not parents)
            if not ready:  # a cycle cannot occur in git; fall back to sha order if data is odd
                ready = sorted(waiting)
            for sha in ready:
                ordered.append(group[sha])
                del waiting[sha]
            for parents in waiting.values():
                parents.difference_update(ready)
    return ordered


def _next_link(link: str) -> str | None:
    for part in link.split(","):
        url, _, rel = part.partition(";")
        if 'rel="next"' in rel:
            return url.strip().strip("<>")
    return None
