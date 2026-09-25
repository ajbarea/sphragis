"""Per-host pacing: the run's interval, a host's crawl delay, and multi-request operations."""

from __future__ import annotations

import pytest

from sphragis.corpus.pacing import Pacer


def _pacer(interval: float) -> tuple[Pacer, list[float]]:
    now, slept = [0.0], []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    return Pacer(interval, clock=lambda: now[0], sleep=sleep), slept


def test_a_crawl_delay_is_a_floor_under_the_runs_interval() -> None:
    delays = {"review.opendev.org": 2.0}
    assert Pacer(1.0, host_intervals=delays).interval("review.opendev.org") == 2.0
    assert Pacer(3.0, host_intervals=delays).interval("review.opendev.org") == 3.0
    assert Pacer(1.0, host_intervals=delays).interval("android.googlesource.com") == 1.0


def test_a_fetch_that_made_several_requests_waits_them_all_out() -> None:
    pacer, slept = _pacer(1.0)
    pacer.wait("h")
    pacer.charge("h", 3)  # a git fetch that turned out to be four HTTP requests
    pacer.wait("h")
    assert slept == pytest.approx([4.0])


def test_charging_a_host_never_asked_is_a_no_op() -> None:
    pacer, slept = _pacer(1.0)
    pacer.charge("h", 5)
    pacer.wait("h")
    assert slept == []
