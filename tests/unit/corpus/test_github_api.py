"""The GitHub API client the route collects with, exercised against a fake transport."""

from __future__ import annotations

import json
from typing import Any

import pytest

from sphragis.corpus.github_api import GitHubAPI, Gone, order_commits


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
    fake = Fake([("comments", 403, limited, {"message": "rate limit"}), ("comments", 200, {}, [])])
    slept: list[float] = []
    assert _api(fake, slept).pr_comments("o/r", 1) == []
    assert slept and slept[0] >= 60


def test_retry_after_is_honoured_for_a_secondary_limit() -> None:
    fake = Fake(
        [
            ("comments", 403, {"retry-after": "30"}, {"message": "secondary"}),
            ("comments", 200, {}, []),
        ]
    )
    slept: list[float] = []
    _api(fake, slept).pr_comments("o/r", 1)
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


def _commit(oid: str, at: str) -> dict[str, str]:
    return {"oid": oid, "committedDate": at}


def test_heads_recover_force_pushed_commits_in_commit_time_order() -> None:
    """Review finding: an amended commit can sit in the timeline before the force push that
    replaced the commit it amends; ordering by timeline position put them backwards."""
    nodes = [
        {"__typename": "PullRequestCommit", "commit": _commit("a1", "2025-01-01T00:00:00Z")},
        {"__typename": "PullRequestCommit", "commit": _commit("b1", "2025-01-03T00:00:00Z")},
        {
            "__typename": "HeadRefForcePushedEvent",
            "beforeCommit": _commit("a2", "2025-01-02T00:00:00Z"),
            "afterCommit": _commit("b1", "2025-01-03T00:00:00Z"),
        },
    ]
    fake = Fake([("timelineItems", 200, {}, _timeline(nodes))])
    heads = _api(fake).pr_heads("o/r", 1)
    assert [h["sha"] for h in heads] == ["a1", "a2", "b1"]
    assert heads[1]["committed_at"] == "2025-01-02T00:00:00Z"


def test_a_graphql_rate_limit_inside_a_200_waits_rather_than_counting_prs_withdrawn() -> None:
    """Review finding: GitHub reports an exhausted GraphQL budget as HTTP 200 with errors."""
    limited = {"errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]}
    fake = Fake(
        [
            ("timelineItems", 200, {"x-ratelimit-reset": "1100"}, limited),
            ("timelineItems", 200, {}, _timeline([])),
        ]
    )
    slept: list[float] = []
    assert _api(fake, slept).pr_heads("o/r", 1) == []
    assert slept and slept[0] >= 100


def test_graphql_not_found_and_a_null_repository_are_gone() -> None:
    missing = {"errors": [{"type": "NOT_FOUND", "message": "Could not resolve"}], "data": None}
    with pytest.raises(Gone):
        _api(Fake([("timelineItems", 200, {}, missing)])).pr_heads("o/r", 1)
    with pytest.raises(Gone):
        _api(Fake([("timelineItems", 200, {}, {"data": {"repository": None}})])).pr_heads("o/r", 1)


def test_other_graphql_errors_raise() -> None:
    broken = {"errors": [{"type": "INTERNAL", "message": "boom"}]}
    with pytest.raises(RuntimeError, match="GraphQL errors"):
        _api(Fake([("search(", 200, {}, broken)])).merged_prs("o", "2025-01-02", qualifier="x")


def test_file_oids_batches_commits_and_reports_absent_files() -> None:
    shas = [f"s{i}" for i in range(55)]
    first = {"data": {"repository": {f"c{i}": {"oid": f"b{i}"} for i in range(50)}}}
    second = {"data": {"repository": {"c0": {"oid": "b50"}, "c1": None}}}
    fake = Fake([("s0:f.py", 200, {}, first), ("s50:f.py", 200, {}, second)])
    oids = _api(fake).file_oids("o/r", "f.py", shas)
    assert oids["s0"] == "b0" and oids["s50"] == "b50" and oids["s51"] is None


def test_fork_point_is_the_merge_base_with_the_base_branch() -> None:
    fake = Fake([("compare/main...c1", 200, {}, {"merge_base_commit": {"sha": "base0"}})])
    assert _api(fake).fork_point("o/r", "main", "c1") == "base0"


def test_a_rebased_series_in_one_second_is_ordered_parent_first() -> None:
    """Review finding: a rebase gives a series one committer second, leaving order to the sha."""
    second = "2025-01-01T00:00:00Z"
    series = [
        {"sha": "a", "committed_at": second, "parents": ["c"]},
        {"sha": "b", "committed_at": second, "parents": ["base"]},
        {"sha": "c", "committed_at": second, "parents": ["b"]},
    ]
    assert [c["sha"] for c in order_commits(series)] == ["b", "c", "a"]
