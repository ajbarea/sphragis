"""The GitHub API client the route collects with, exercised against a fake transport."""

from __future__ import annotations

import json
from typing import Any

import pytest

from sphragis.corpus.github_api import GitHubAPI, Gone


class Fake:
    """Answers requests from a script of (status, headers, body) per URL substring, in order."""

    def __init__(self, script: list[tuple[str, int, dict[str, str], Any]]) -> None:
        self.script = script
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None):
        self.calls.append((method, url))
        for index, (needle, status, out_headers, payload) in enumerate(self.script):
            if needle in url or (body is not None and needle in body.decode()):
                del self.script[index]
                text = payload if isinstance(payload, str) else json.dumps(payload)
                return status, out_headers, text
        raise AssertionError(f"unscripted request {method} {url}")


def _api(fake: Fake, slept: list[float] | None = None, now: float = 1000.0) -> GitHubAPI:
    record = slept if slept is not None else []
    return GitHubAPI("token", transport=fake, sleep=record.append, clock=lambda: now)


def test_rest_pages_follow_the_link_header() -> None:
    nxt = {"link": '<https://api.github.com/repos/o/r/pulls/1/comments?page=2>; rel="next"'}
    fake = Fake(
        [
            ("comments?per_page=100", 200, nxt, [{"id": 1}]),
            ("page=2", 200, {}, [{"id": 2}]),
        ]
    )
    assert [c["id"] for c in _api(fake).pr_comments("o/r", 1)] == [1, 2]


def test_an_exhausted_rate_limit_waits_until_its_reset() -> None:
    limited = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1060"}
    fake = Fake([("commits", 403, limited, {"message": "rate limit"}), ("commits", 200, {}, [])])
    slept: list[float] = []
    assert _api(fake, slept).pr_commits("o/r", 1) == []
    assert slept and slept[0] >= 60


def test_retry_after_is_honoured_for_a_secondary_limit() -> None:
    fake = Fake(
        [
            ("commits", 403, {"retry-after": "30"}, {"message": "secondary"}),
            ("commits", 200, {}, []),
        ]
    )
    slept: list[float] = []
    _api(fake, slept).pr_commits("o/r", 1)
    assert slept[0] == 30.0, "the wait GitHub asked for, before the pacing floor"


def test_a_404_is_gone_and_not_retried() -> None:
    fake = Fake([("comments", 404, {}, {"message": "Not Found"})])
    with pytest.raises(Gone):
        _api(fake).pr_comments("o/r", 9)
    assert len(fake.calls) == 1


def _search_page(count: int, numbers: list[int]) -> dict[str, Any]:
    nodes = [
        {
            "number": n,
            "createdAt": "2025-01-02T03:04:05Z",
            "repository": {"nameWithOwner": "o/r"},
            "author": {"login": "dev", "__typename": "User"},
        }
        for n in numbers
    ]
    return {
        "data": {
            "search": {
                "issueCount": count,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": nodes,
            }
        }
    }


def test_a_day_over_the_search_cap_is_split_until_each_part_fits() -> None:
    fake = Fake(
        [
            ("2025-01-02T00:00:00..2025-01-02T23:59:59", 200, {}, _search_page(1500, [])),
            ("2025-01-02T00:00:00..2025-01-02T11:59:59", 200, {}, _search_page(2, [1, 2])),
            ("2025-01-02T12:00:00..2025-01-02T23:59:59", 200, {}, _search_page(1, [3])),
        ]
    )
    prs = _api(fake).merged_prs("o", "2025-01-02", qualifier="is:merged merged")
    assert sorted(p["number"] for p in prs) == [1, 2, 3]


def test_compare_returns_each_files_patch_and_none_where_github_omits_it() -> None:
    files = {"files": [{"filename": "a.py", "patch": "@@ -1 +1 @@\n-x\n+y"}, {"filename": "b.bin"}]}
    fake = Fake([("compare/c1...c2", 200, {}, files)])
    assert _api(fake).compare_files("o/r", "c1", "c2") == {
        "a.py": "@@ -1 +1 @@\n-x\n+y",
        "b.bin": None,
    }


def _timeline(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "timelineItems": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": nodes,
                    }
                }
            }
        }
    }


def test_heads_recover_commits_a_force_push_rewrote_away() -> None:
    """Every commit that was ever the PR's head, in order, like Gerrit's patch sets."""
    nodes = [
        {"__typename": "PullRequestCommit", "commit": {"oid": "a1"}},
        {"__typename": "PullRequestCommit", "commit": {"oid": "a2"}},
        {
            "__typename": "HeadRefForcePushedEvent",
            "beforeCommit": {"oid": "a2"},
            "afterCommit": {"oid": "b1"},
        },
        {"__typename": "PullRequestCommit", "commit": {"oid": "b1"}},
        {"__typename": "PullRequestCommit", "commit": {"oid": "b2"}},
    ]
    fake = Fake([("timelineItems", 200, {}, _timeline(nodes))])
    assert [h["sha"] for h in _api(fake).pr_heads("o/r", 1)] == ["a1", "a2", "b1", "b2"]


def test_a_force_push_whose_old_head_was_never_listed_still_has_it() -> None:
    nodes = [
        {
            "__typename": "HeadRefForcePushedEvent",
            "beforeCommit": {"oid": "old"},
            "afterCommit": {"oid": "new"},
        },
    ]
    fake = Fake([("timelineItems", 200, {}, _timeline(nodes))])
    assert [h["sha"] for h in _api(fake).pr_heads("o/r", 1)] == ["old", "new"]
