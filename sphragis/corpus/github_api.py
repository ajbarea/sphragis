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
      number createdAt repository { nameWithOwner }
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
        ... on PullRequestCommit { commit { oid } }
        ... on HeadRefForcePushedEvent { beforeCommit { oid } afterCommit { oid } }
      }
    }
  } }
}"""


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

    def _pages(self, path: str, **params: Any) -> Iterator[Any]:
        url: str | None = f"{API}{path}?{urlencode({'per_page': 100, **params})}"
        while url:
            headers, payload = self._request("GET", url)
            yield from payload
            url = _next_link(headers.get("link", ""))

    def pr_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        """Every inline review comment on a PR, replies included."""
        return list(self._pages(f"/repos/{repo}/pulls/{number}/comments"))

    def pr_commits(self, repo: str, number: int) -> list[dict[str, Any]]:
        """A PR's commits, oldest first (GitHub lists at most 250)."""
        return list(self._pages(f"/repos/{repo}/pulls/{number}/commits"))

    def pr_heads(self, repo: str, number: int) -> list[dict[str, Any]]:
        """Every commit that was ever the PR's head or on it, in timeline order, as `{"sha": ...}`.

        The commit list holds only the final history; a force push (amend, rebase, squash) leaves
        the commits reviewers commented on out of it. The timeline keeps each force push's before
        and after commits, so the PR's revisions are recovered the way Gerrit keeps patch sets.
        """
        owner, name = repo.split("/", 1)
        shas: list[str] = []
        after: str | None = None
        while True:
            variables = {"owner": owner, "name": name, "number": number, "after": after}
            _, payload = self._request(
                "POST", f"{API}/graphql", {"query": _TIMELINE, "variables": variables}
            )
            pull = (payload.get("data") or {}).get("repository", {}).get("pullRequest")
            if pull is None:
                raise Gone(f"no pull request {repo}#{number}")
            items = pull["timelineItems"]
            for node in items["nodes"]:
                if node.get("__typename") == "PullRequestCommit":
                    oids = [(node.get("commit") or {}).get("oid")]
                else:
                    oids = [(node.get(k) or {}).get("oid") for k in ("beforeCommit", "afterCommit")]
                for oid in oids:
                    if oid and oid not in shas:
                        shas.append(oid)
            if not items["pageInfo"]["hasNextPage"]:
                return [{"sha": sha} for sha in shas]
            after = items["pageInfo"]["endCursor"]

    def pr_files(self, repo: str, number: int) -> list[dict[str, Any]]:
        """A PR's changed files with their changed-line counts (GitHub lists at most 3,000)."""
        return list(self._pages(f"/repos/{repo}/pulls/{number}/files"))

    def compare_files(self, repo: str, base: str, head: str) -> dict[str, str | None]:
        """Each changed file's patch between two commits; None where GitHub omits it."""
        _, payload = self._request("GET", f"{API}/repos/{repo}/compare/{base}...{head}")
        return {f["filename"]: f.get("patch") for f in payload.get("files", [])}

    def _search(self, query: str) -> tuple[int, list[dict[str, Any]]]:
        nodes: list[dict[str, Any]] = []
        after: str | None = None
        while True:
            _, payload = self._request(
                "POST",
                f"{API}/graphql",
                {"query": _SEARCH, "variables": {"q": query, "after": after}},
            )
            page = payload["data"]["search"]
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
                "user": {
                    "id": (n.get("author") or {}).get("databaseId"),
                    "login": (n.get("author") or {}).get("login"),
                    "type": "Bot" if (n.get("author") or {}).get("__typename") == "Bot" else "User",
                },
            }
            for n in nodes
        ]


def _next_link(link: str) -> str | None:
    for part in link.split(","):
        url, _, rel = part.partition(";")
        if 'rel="next"' in rel:
            return url.strip().strip("<>")
    return None
