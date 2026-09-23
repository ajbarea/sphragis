"""Request pacing per host, shared by the REST transport and the git route.

A host's robots.txt `Crawl-delay` is a floor under whatever interval the run asks for: the
CLI's `--request-interval` can slow a host down but never speed one past what it publishes. The
delays themselves are recorded once, with each host's REST permission, in `cli.REST_PERMITTED`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping


class Pacer:
    """Hold operations against one host at least `interval(host)` seconds apart.

    Measured 2026-09-15 against review.opendev.org: after hours of unpaced building (about 20
    requests a second) a quarter to a third of new handshakes were dropped; after 20 quiet
    minutes, 0 of 30. The drops follow our volume, so the fix is to send less, not to retry
    harder.

    The interval was 0.2 s, and that only postponed it: both review.opendev.org and
    codereview.qt-project.org went on to refuse handshakes after about three hours at 5
    requests a second, and Qt's refusal outlasted 25 minutes of silence, which reads as a
    firewall ban rather than load shedding. Building a full corpus is roughly 70,000 requests
    against a volunteer-run server, so the default is 1 request a second. A frozen corpus is
    fetched once and reused; an overnight collection costs nothing a second run would not
    cost more.

    One git fetch is several HTTP requests (ref listing, then one or more pack negotiations),
    so the git route reports what each fetch actually cost through `charge`, and the next
    operation waits out the whole bill rather than one request's share of it.
    """

    def __init__(
        self,
        min_interval: float,
        *,
        host_intervals: Mapping[str, float] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._min_interval = min_interval
        self._host_intervals = dict(host_intervals or {})
        self._clock = clock
        self._sleep = sleep
        self._next: dict[str, float] = {}

    def interval(self, host: str) -> float:
        """The larger of the run's interval and the host's published crawl delay."""
        return max(self._min_interval, self._host_intervals.get(host, 0.0))

    def wait(self, host: str) -> None:
        """Sleep until `host` may be asked again, then book one request against it."""
        interval = self.interval(host)
        if interval <= 0:
            return
        allowed = self._next.get(host)
        if allowed is not None:
            wait = allowed - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._next[host] = self._clock() + interval

    def charge(self, host: str, extra_requests: int) -> None:
        """Book `extra_requests` more against `host`, beyond the one `wait` booked."""
        if extra_requests > 0 and host in self._next:
            self._next[host] += self.interval(host) * extra_requests
